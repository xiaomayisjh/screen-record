import subprocess
import time
import os

_startupinfo = subprocess.STARTUPINFO()
_startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
_startupinfo.wShowWindow = subprocess.SW_HIDE


class DeviceEnumerator:
    def __init__(self, ffmpeg_path):
        self._ffmpeg = ffmpeg_path
        self._cache = None
        self._cache_time = 0
        self._cache_ttl = 10

    def list_all(self):
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return self._cache

        try:
            result = subprocess.run(
                [self._ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                startupinfo=_startupinfo,
                timeout=10,
            )
            output = result.stdout.decode("utf-8", errors="replace")
            output = output.replace("\\r\\n", "\n").replace("\\\\", "\\")
        except Exception:
            return {"webcam": [], "microphone": []}

        cameras = self._parse_section(output, end_marker="DirectShow audio")
        microphones = self._parse_section(output, start_marker="DirectShow audio")

        self._cache = {"webcam": cameras, "microphone": microphones}
        self._cache_time = now
        return self._cache

    def _parse_section(self, output, start_marker=None, end_marker=None):
        section = output
        if start_marker:
            idx = section.find(start_marker)
            if idx >= 0:
                section = section[idx:]
            else:
                return []
        if end_marker:
            idx = section.find(end_marker)
            if idx >= 0:
                section = section[:idx]

        devices = []
        for line in section.splitlines():
            bracket_end = line.find("]")
            if bracket_end < 0:
                continue
            remainder = line[bracket_end + 1 :].strip()
            if remainder.startswith('"'):
                name = remainder.strip('" ')
                if name:
                    devices.append(name)
        return devices


class WebcamCapturer:
    def __init__(self, base_dir):
        self._ffmpeg = os.path.join(base_dir, "ffmpeg.exe")
        self._device = ""
        self._proc = None

    def set_device(self, device_name):
        self._device = device_name

    def start(self, output_path):
        self._proc = subprocess.Popen(
            args=[
                self._ffmpeg, "-f", "dshow",
                "-i", "video=" + self._device,
                "-y", "-c:v", "mpeg4", "-qscale:v", "7",
                output_path,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=_startupinfo,
        )

    def stop(self):
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
