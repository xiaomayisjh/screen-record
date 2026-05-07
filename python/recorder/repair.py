import json
import os
import subprocess
import threading
from enum import Enum
from datetime import datetime


class FileHealth(Enum):
    HEALTHY = "healthy"
    FRAGMENTED = "fragmented"
    BROKEN = "broken"


class RepairResult:
    def __init__(self, repaired=None, failed=None, healthy=None):
        self.repaired = repaired or []
        self.failed = failed or []
        self.healthy = healthy or []

    def to_dict(self):
        return {
            "repaired": self.repaired,
            "failed": self.failed,
            "healthy": self.healthy,
        }


MARKER_FILENAME = ".recording"


def write_recording_marker(captures_dir, filename, encoder, hwaccel):
    marker_path = os.path.join(captures_dir, MARKER_FILENAME)
    data = {
        "filename": filename,
        "started_at": datetime.now().isoformat(),
        "encoder": encoder,
        "hwaccel": hwaccel,
    }
    with open(marker_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def read_recording_marker(captures_dir):
    marker_path = os.path.join(captures_dir, MARKER_FILENAME)
    if not os.path.isfile(marker_path):
        return None
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def delete_recording_marker(captures_dir):
    marker_path = os.path.join(captures_dir, MARKER_FILENAME)
    try:
        os.remove(marker_path)
    except OSError:
        pass


class FileHealthChecker:
    def __init__(self, ffmpeg_path, captures_dir):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = os.path.join(
            os.path.dirname(ffmpeg_path), "ffprobe.exe"
        )
        self.captures_dir = captures_dir
        self._repair_lock = threading.Lock()

    def _run_ffprobe(self, filepath):
        if not os.path.isfile(self.ffprobe_path):
            return -1
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [self.ffprobe_path, "-v", "error", "-show_format", filepath],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                timeout=10,
            )
            return result.returncode
        except Exception:
            return -1

    def _is_fragmented_mp4(self, filepath):
        try:
            with open(filepath, "rb") as f:
                header = f.read(32)
            if len(header) < 12:
                return False
            if header[4:8] == b'ftyp':
                return True
        except OSError:
            pass
        return False

    def check_file(self, filepath):
        if not os.path.isfile(filepath):
            return FileHealth.BROKEN
        probe_result = self._run_ffprobe(filepath)
        if probe_result != 0:
            return FileHealth.BROKEN
        if self._is_fragmented_mp4(filepath):
            return FileHealth.FRAGMENTED
        return FileHealth.HEALTHY

    def check_all_files(self):
        results = {}
        if not os.path.isdir(self.captures_dir):
            return results
        for name in os.listdir(self.captures_dir):
            if not name.lower().endswith((".mp4", ".mkv", ".avi")):
                continue
            if name.startswith("."):
                continue
            filepath = os.path.join(self.captures_dir, name)
            if os.path.isfile(filepath):
                results[name] = self.check_file(filepath)
        return results

    def repair_file(self, filename):
        safe_name = os.path.basename(filename)
        filepath = os.path.join(self.captures_dir, safe_name)
        if not os.path.isfile(filepath):
            return False, f"File not found: {safe_name}"

        with self._repair_lock:
            tmp_output = filepath + ".repairing.mp4"
            try:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                result = subprocess.run(
                    [
                        self.ffmpeg_path, "-i", filepath,
                        "-c", "copy",
                        "-movflags", "+faststart",
                        "-y", tmp_output,
                    ],
                    capture_output=True,
                    text=True,
                    startupinfo=startupinfo,
                    timeout=120,
                )
                if result.returncode != 0:
                    broken_path = filepath + ".broken"
                    os.rename(filepath, broken_path)
                    return False, f"FFmpeg repair failed (exit {result.returncode})"
                os.replace(tmp_output, filepath)
                return True, None
            except subprocess.TimeoutExpired:
                if os.path.isfile(tmp_output):
                    os.remove(tmp_output)
                return False, "Repair timed out"
            except Exception as e:
                if os.path.isfile(tmp_output):
                    os.remove(tmp_output)
                return False, str(e)

    def repair_all(self):
        result = RepairResult()
        health_map = self.check_all_files()
        for name, health in health_map.items():
            if health == FileHealth.HEALTHY:
                result.healthy.append(name)
            elif health in (FileHealth.FRAGMENTED, FileHealth.BROKEN):
                success, error = self.repair_file(name)
                if success:
                    result.repaired.append(name)
                else:
                    result.failed.append({"name": name, "error": error})
        return result

    def auto_repair_on_startup(self):
        marker = read_recording_marker(self.captures_dir)
        result = RepairResult()
        if marker:
            filename = marker.get("filename", "")
            if filename:
                health = self.check_file(
                    os.path.join(self.captures_dir, filename)
                )
                if health in (FileHealth.FRAGMENTED, FileHealth.BROKEN):
                    success, error = self.repair_file(filename)
                    if success:
                        result.repaired.append(filename)
                    else:
                        result.failed.append({"name": filename, "error": error})
                elif health == FileHealth.HEALTHY:
                    result.healthy.append(filename)
            delete_recording_marker(self.captures_dir)
        general_health = self.check_all_files()
        for name, health in general_health.items():
            if name in result.repaired or name in result.healthy or name in [f["name"] for f in result.failed]:
                continue
            if health == FileHealth.HEALTHY:
                result.healthy.append(name)
            elif health in (FileHealth.FRAGMENTED, FileHealth.BROKEN):
                success, error = self.repair_file(name)
                if success:
                    result.repaired.append(name)
                else:
                    result.failed.append({"name": name, "error": error})
        return result
