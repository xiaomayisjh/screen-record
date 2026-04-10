import json
import os
import subprocess
import threading

DEFAULT_SETTINGS = {
    "fps": 30,
    "encoder": "mpeg4",
    "draw_mouse": True,
    "audio_mode": "default",
    "audio_devices": [],
}

SUPPORTED_ENCODERS = [
    "h264_nvenc",
    "h264_qsv",
    "h264_amf",
    "libx264",
    "mpeg4",
]

ENCODER_NAMES = {
    "h264_nvenc": "NVIDIA NVENC",
    "h264_qsv": "Intel QuickSync",
    "h264_amf": "AMD AMF",
    "libx264": "H.264 (CPU)",
    "mpeg4": "MPEG-4 (CPU)",
}


class SettingsManager:
    def __init__(self, base_dir):
        self._path = os.path.join(base_dir, "settings.json")
        self._ffmpeg_path = os.path.join(base_dir, "ffmpeg.exe")
        self._lock = threading.Lock()
        self._settings = dict(DEFAULT_SETTINGS)
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._path):
                with open(self._path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self._settings.update(saved)
        except (json.JSONDecodeError, IOError):
            pass

    def _save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2)

    def get_all(self):
        with self._lock:
            return dict(self._settings)

    def get(self, key, default=None):
        with self._lock:
            return self._settings.get(key, default)

    def update(self, changes):
        with self._lock:
            if "fps" in changes:
                try:
                    fps = int(changes["fps"])
                    self._settings["fps"] = max(1, min(120, fps))
                except (ValueError, TypeError):
                    pass
            if "encoder" in changes and changes["encoder"] in SUPPORTED_ENCODERS:
                self._settings["encoder"] = changes["encoder"]
            if "draw_mouse" in changes:
                self._settings["draw_mouse"] = bool(changes["draw_mouse"])
            if "audio_mode" in changes and changes["audio_mode"] in ("default", "selected"):
                self._settings["audio_mode"] = changes["audio_mode"]
            if "audio_devices" in changes and isinstance(changes["audio_devices"], list):
                self._settings["audio_devices"] = [
                    int(d) for d in changes["audio_devices"]
                    if isinstance(d, (int, float))
                ]
            self._save()
        return self.get_all()

    def detect_available_encoders(self):
        if not os.path.exists(self._ffmpeg_path):
            return ["mpeg4"]

        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

            result = subprocess.run(
                [self._ffmpeg_path, "-encoders"],
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                timeout=10,
            )
            output = result.stdout + result.stderr
        except Exception:
            return ["mpeg4"]

        available = ["mpeg4"]
        for encoder in SUPPORTED_ENCODERS:
            if encoder == "mpeg4":
                continue
            if encoder in output:
                available.append(encoder)

        return available

    def get_best_encoder(self):
        available = self.detect_available_encoders()
        for encoder in SUPPORTED_ENCODERS:
            if encoder in available:
                return encoder
        return "mpeg4"
