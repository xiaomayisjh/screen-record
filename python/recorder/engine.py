import os
import shutil
import subprocess
import threading
import time
from datetime import datetime

from .audio import StreamingAudioRecorder
from .cmd_builder import CmdBuilder
from .webcam import DeviceEnumerator, WebcamCapturer
from .settings_manager import SettingsManager


_startupinfo = subprocess.STARTUPINFO()
_startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_startupinfo.wShowWindow = subprocess.SW_HIDE


class RecordingEngine:
    """Thread-safe recording orchestrator."""

    def __init__(self, base_dir, settings):
        self.base_dir = base_dir
        self.captures_dir = os.path.join(base_dir, "ScreenCaptures")
        self.tmp_dir = os.path.join(base_dir, "tmp")
        self.settings = settings

        self.cmd_builder = CmdBuilder(base_dir)
        self.audio_recorder = StreamingAudioRecorder()
        self.device_enumerator = DeviceEnumerator(
            os.path.join(base_dir, "ffmpeg.exe")
        )

        self._state = "idle"
        self._lock = threading.Lock()
        # Use Condition + version counter for multi-client SSE support
        self._state_condition = threading.Condition(self._lock)
        self._state_version = 0
        self._video_proc = None
        self._webcam_capturer = None
        self._merge_proc = None
        self._recording_start = None
        self._audio_start = None
        self._filename = None
        self._error_message = None
        self._stderr_file = None

        os.makedirs(self.captures_dir, exist_ok=True)
        self._cleanup_tmp()

    def _notify_state_change(self):
        """Must be called with self._lock held."""
        self._state_version += 1
        self._state_condition.notify_all()

    def get_state(self):
        with self._lock:
            return self._get_state_unlocked()

    def _get_state_unlocked(self):
        """Get state dict. Caller must hold self._lock."""
        result = {
            "state": self._state,
            "recording": self._state == "recording",
            "merging": self._state == "merging",
            "filename": self._filename,
            "elapsed": 0,
            "error": self._error_message,
        }
        if self._state == "recording" and self._recording_start:
            result["elapsed"] = time.time() - self._recording_start
        return result

    def wait_for_state_change(self, last_version, timeout=1.0):
        """Wait until state version changes or timeout. Safe for multiple clients."""
        with self._state_condition:
            self._state_condition.wait_for(
                lambda: self._state_version > last_version, timeout=timeout
            )
            return self._state_version, self._get_state_unlocked()

    def has_ffmpeg(self):
        return os.path.isfile(os.path.join(self.base_dir, "ffmpeg.exe"))

    def generate_filename(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"ScreenCapture_{timestamp}.mp4"
        num = 0
        while os.path.exists(os.path.join(self.captures_dir, name)):
            num += 1
            name = f"ScreenCapture_{timestamp}_{num}.mp4"
        return name

    def start_recording(self, config):
        with self._lock:
            if self._state != "idle":
                raise RuntimeError("Already recording or merging")

            self._filename = config.get("filename") or self.generate_filename()
            self._error_message = None

            os.makedirs(self.captures_dir, exist_ok=True)
            os.makedirs(self.tmp_dir, exist_ok=True)

            s = self.settings.get_all()
            self.cmd_builder.config(
                fps=s["fps"],
                encoder=s["encoder"],
                draw_mouse=s["draw_mouse"],
            )
            self.cmd_builder.set_source(
                config.get("source") == "title",
                config.get("window_title", ""),
            )

            # Redirect FFmpeg stderr to a temp file to avoid pipe buffer deadlock
            self._recording_start = time.time()
            tmp_video = os.path.join(self.tmp_dir, "tmp.mkv")
            video_cmd = self.cmd_builder.get_capture_cmd(tmp_video)
            self._stderr_file = open(
                os.path.join(self.tmp_dir, "ffmpeg_stderr.log"), "w",
                encoding="utf-8", errors="replace",
            )
            self._video_proc = subprocess.Popen(
                args=video_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
                startupinfo=_startupinfo,
            )

            self._audio_start = time.time()
            audio_devices = s.get("audio_devices", [])
            if s.get("audio_mode") == "default" or not audio_devices:
                self.audio_recorder.devices = [None]
            else:
                self.audio_recorder.devices = audio_devices

            has_input = False
            try:
                for i in range(self.audio_recorder.get_device_count()):
                    if self.audio_recorder.is_input_device(i):
                        has_input = True
                        break
            except Exception:
                pass

            if has_input:
                self.audio_recorder.record(os.path.join(self.tmp_dir, "tmp.wav"))
            else:
                self.audio_recorder.devices = []

            if config.get("webcam") and config.get("webcam_device"):
                self._webcam_capturer = WebcamCapturer(self.base_dir)
                self._webcam_capturer.set_device(config["webcam_device"])
                self._webcam_capturer.start(
                    os.path.join(self.tmp_dir, "webcamtmp.mkv")
                )
                self.cmd_builder.config(webcam=True)
            else:
                self._webcam_capturer = None
                self.cmd_builder.config(webcam=False)

            self._state = "recording"
            self._notify_state_change()

        threading.Thread(target=self._monitor, daemon=True).start()

    def stop_recording(self):
        with self._lock:
            if self._state != "recording":
                return
            self._state = "merging"
            self._notify_state_change()

        if self._video_proc:
            self._video_proc.terminate()
            try:
                self._video_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._video_proc.kill()
                self._video_proc.wait(timeout=5)
        self.audio_recorder.stop()
        if self._webcam_capturer:
            self._webcam_capturer.stop()
        self._close_stderr_file()

        threading.Thread(target=self._merge, daemon=True).start()

    def _close_stderr_file(self):
        if self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass
            self._stderr_file = None

    def _read_stderr_log(self):
        log_path = os.path.join(self.tmp_dir, "ffmpeg_stderr.log")
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    return f.read()[-500:]
        except Exception:
            pass
        return ""

    def _monitor(self):
        while True:
            with self._lock:
                if self._state != "recording":
                    return
            if self._video_proc and self._video_proc.poll() is not None:
                self._close_stderr_file()
                stderr_out = self._read_stderr_log()
                with self._lock:
                    self._error_message = f"FFmpeg crashed: {stderr_out}"
                    self._state = "idle"
                    self._notify_state_change()
                self.audio_recorder.stop()
                if self._webcam_capturer:
                    self._webcam_capturer.stop()
                self._cleanup_tmp()
                return
            time.sleep(1.0)

    def _merge(self):
        try:
            audio_delay_ms = 0
            if self._audio_start and self._recording_start:
                delay = self._audio_start - self._recording_start
                if delay > 0:
                    audio_delay_ms = int(delay * 1000)

            output_path = os.path.join(self.captures_dir, self._filename)
            audio_ok = self._check_audio_files()

            if not audio_ok:
                tmp_video = os.path.join(self.tmp_dir, "tmp.mkv")
                if os.path.exists(tmp_video):
                    shutil.copy2(tmp_video, output_path)
                else:
                    self._error_message = "Video file not found after recording"
            else:
                devices = self.audio_recorder.devices
                self.cmd_builder.config(
                    aud_list=devices, audio_delay_ms=audio_delay_ms
                )
                merge_cmd = self.cmd_builder.get_merge_cmd(output_path)
                stderr_file = open(
                    os.path.join(self.tmp_dir, "merge_stderr.log"), "w",
                    encoding="utf-8", errors="replace",
                )
                self._merge_proc = subprocess.Popen(
                    args=merge_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    startupinfo=_startupinfo,
                )
                self._merge_proc.wait()
                stderr_file.close()

                if self._merge_proc.returncode != 0:
                    merge_err = ""
                    try:
                        log_path = os.path.join(self.tmp_dir, "merge_stderr.log")
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            merge_err = f.read()[-300:]
                    except Exception:
                        pass
                    self._error_message = (
                        f"Merge failed (exit {self._merge_proc.returncode}): {merge_err}"
                    )

            self._cleanup_tmp()
        except Exception as e:
            self._error_message = str(e)
        finally:
            with self._lock:
                self._state = "idle"
                self._notify_state_change()

    def _check_audio_files(self):
        devices = self.audio_recorder.devices
        if not devices:
            return False
        for i in range(len(devices)):
            path = os.path.join(self.tmp_dir, f"tmp_{i}.wav")
            if not os.path.exists(path) or os.path.getsize(path) < 100:
                return False
        return True

    def _cleanup_tmp(self):
        self._close_stderr_file()
        if os.path.isdir(self.tmp_dir):
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def list_files(self):
        files = []
        if os.path.isdir(self.captures_dir):
            for name in os.listdir(self.captures_dir):
                path = os.path.join(self.captures_dir, name)
                if os.path.isfile(path) and name.lower().endswith(
                    (".mp4", ".mkv", ".avi")
                ):
                    stat = os.stat(path)
                    files.append({
                        "name": name,
                        "size": stat.st_size,
                        "size_human": self._human_size(stat.st_size),
                        "date": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    })
        files.sort(key=lambda f: f["date"], reverse=True)
        return files

    def delete_file(self, name):
        safe_name = os.path.basename(name)
        path = os.path.join(self.captures_dir, safe_name)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False

    @staticmethod
    def _human_size(size):
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
