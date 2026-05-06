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

ENCODER_HWACCEL_MAP = {
    "h264_qsv": "qsv",
    "h264_amf": "d3d11va",
    "h264_nvenc": "cuda",
}

HWACCEL_NAMES = {
    "qsv": "Intel QSV",
    "d3d11va": "D3D11VA (Intel/AMD)",
    "dxva2": "DXVA2 (Intel/AMD)",
    "cuda": "NVIDIA CUDA",
}

FALLBACK_CHAIN = ["h264_qsv", "h264_amf", "h264_nvenc", "libx264", "mpeg4"]


class SettingsManager:
    def __init__(self, base_dir):
        self._path = os.path.join(base_dir, "settings.json")
        self._ffmpeg_path = os.path.join(base_dir, "ffmpeg.exe")
        self._lock = threading.Lock()
        self._settings = dict(DEFAULT_SETTINGS)
        self._hwaccel_cache = None
        self._hwaccel_cache_time = 0
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

    def _run_ffmpeg(self, args):
        if not os.path.exists(self._ffmpeg_path):
            return ""
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            result = subprocess.run(
                [self._ffmpeg_path] + args,
                capture_output=True,
                text=True,
                startupinfo=startupinfo,
                timeout=10,
            )
            return result.stdout + result.stderr
        except Exception:
            return ""

    def detect_available_encoders(self):
        output = self._run_ffmpeg(["-encoders"])
        if not output:
            return ["mpeg4"]

        available = ["mpeg4"]
        for encoder in SUPPORTED_ENCODERS:
            if encoder == "mpeg4":
                continue
            if encoder in output:
                available.append(encoder)

        return available

    def detect_available_hwaccels(self):
        import time as _time
        now = _time.time()
        if self._hwaccel_cache and (now - self._hwaccel_cache_time) < 30:
            return self._hwaccel_cache

        output = self._run_ffmpeg(["-hwaccels"])
        available = []
        if output:
            for line in output.splitlines():
                line = line.strip()
                if line and line not in ("Hardware acceleration methods:", ""):
                    if line in HWACCEL_NAMES:
                        available.append(line)

        self._hwaccel_cache = available
        self._hwaccel_cache_time = now
        return available

    def get_encoder_hwaccel(self, encoder):
        hwaccel = ENCODER_HWACCEL_MAP.get(encoder)
        if not hwaccel:
            return None
        available = self.detect_available_hwaccels()
        if hwaccel in available:
            return hwaccel
        return None

    def get_best_encoder(self):
        available_encoders = self.detect_available_encoders()
        available_hwaccels = self.detect_available_hwaccels()

        for encoder in FALLBACK_CHAIN:
            if encoder not in available_encoders:
                continue
            if encoder in ENCODER_HWACCEL_MAP:
                hwaccel = ENCODER_HWACCEL_MAP[encoder]
                if hwaccel in available_hwaccels:
                    return encoder
            else:
                return encoder

        for encoder in FALLBACK_CHAIN:
            if encoder in available_encoders:
                return encoder

        return "mpeg4"

    def get_fallback_encoder(self, current_encoder):
        idx = FALLBACK_CHAIN.index(current_encoder) if current_encoder in FALLBACK_CHAIN else len(FALLBACK_CHAIN) - 1
        for i in range(idx + 1, len(FALLBACK_CHAIN)):
            encoder = FALLBACK_CHAIN[i]
            if encoder in self.detect_available_encoders():
                return encoder
        return "mpeg4"
