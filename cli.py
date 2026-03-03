"""
Screen Recorder CLI - 纯命令行无人值守静默录屏

用法:
    python cli.py                           # 零参数，全屏+默认音频，Ctrl+C 停止
    python cli.py --duration 60             # 录制60秒
    python cli.py --duration 3600 --fps 30 --encoder h264_nvenc -o rec.mp4
    python cli.py --list-devices            # 列出可用设备
    python cli.py --schedule 23:00 --duration 7200 --verbose
"""

import argparse
import logging
import os
import re
import signal
import sys
import threading
import time
import tempfile
from datetime import datetime, timedelta

from recorder.engine import RecordingEngine
from recorder.settings_manager import SettingsManager

log = logging.getLogger("cli")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="cli",
        description="Screen Recorder CLI - 纯命令行无人值守静默录屏",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
示例:
  python cli.py                              全屏录制，Ctrl+C 停止
  python cli.py --duration 60                录制60秒
  python cli.py -o demo.mp4 --verbose        自定义文件名 + 打印状态
  python cli.py --list-devices               列出可用音频/摄像头设备
  python cli.py --schedule 23:00 --duration 7200  23:00开始录2小时
""",
    )

    vid = p.add_argument_group("视频")
    vid.add_argument("--fps", type=int, default=None, help="帧率 1-120 (默认 30)")
    vid.add_argument("--encoder", choices=["mpeg4", "h264_nvenc"], default=None,
                     help="编码器 (默认 mpeg4)")
    vid.add_argument("--no-mouse", action="store_true", help="不绘制鼠标光标")
    vid.add_argument("--window", metavar="TITLE", default=None,
                     help="录制指定窗口 (按标题匹配，默认全屏)")
    vid.add_argument("--webcam", action="store_true", help="启用摄像头叠加")
    vid.add_argument("--webcam-device", metavar="NAME", default=None,
                     help="摄像头设备名")

    aud = p.add_argument_group("音频")
    aud.add_argument("--no-audio", action="store_true", help="不录制音频")
    aud.add_argument("--audio-devices", type=int, nargs="+", metavar="ID",
                     default=None, help="指定音频设备 ID (可多个)")

    out = p.add_argument_group("输出")
    out.add_argument("-o", "--output", metavar="FILE", default=None,
                     help="输出文件名 (默认自动生成时间戳)")
    out.add_argument("--output-dir", metavar="DIR", default=None,
                     help="输出目录 (默认 ScreenCaptures/)")

    stop = p.add_argument_group("停止条件")
    stop.add_argument("--duration", type=int, default=0, metavar="SEC",
                      help="录制时长(秒)，0=无限 (默认 0)")
    stop.add_argument("--max-size", metavar="SIZE", default=None,
                      help="文件大小上限，如 500M / 2G")

    sched = p.add_argument_group("定时")
    sched.add_argument("--schedule", metavar="HH:MM", default=None,
                       help="定时开始录制 (24小时制，如 23:00)")

    logs = p.add_argument_group("日志")
    logs.add_argument("-v", "--verbose", action="store_true",
                      help="在终端打印录制状态")
    logs.add_argument("--log-file", metavar="PATH", default=None,
                      help="写入日志文件")

    conf = p.add_argument_group("配置")
    conf.add_argument("--config", metavar="FILE", default=None,
                      help="从 JSON 配置文件读取设置 (如 settings.json)")
    conf.add_argument("--list-devices", action="store_true",
                      help="列出可用的音频和摄像头设备后退出")

    args = p.parse_args(argv)

    if args.fps is not None and not (1 <= args.fps <= 120):
        p.error("--fps 必须在 1-120 之间")
    if args.max_size is not None:
        args._max_size_bytes = parse_size(args.max_size)
        if args._max_size_bytes is None:
            p.error(f"无法解析 --max-size: {args.max_size} (示例: 500M, 2G)")
    else:
        args._max_size_bytes = 0
    if args.schedule is not None:
        if not re.match(r"^\d{1,2}:\d{2}$", args.schedule):
            p.error(f"--schedule 格式错误: {args.schedule} (应为 HH:MM)")
        parts = args.schedule.split(":")
        h, m = int(parts[0]), int(parts[1])
        if not (0 <= h <= 23 and 0 <= m <= 59):
            p.error(f"--schedule 时间无效: {args.schedule} (小时 0-23, 分钟 0-59)")

    return args


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"B": 1, "K": 1024, "KB": 1024, "M": 1024**2, "MB": 1024**2,
               "G": 1024**3, "GB": 1024**3, "T": 1024**4, "TB": 1024**4}


def parse_size(s):
    """Parse size string like '500M', '2G', '1.5GB' into bytes. Returns None on failure."""
    s = s.strip().upper()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*([A-Z]*)\s*$", s)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or "B"
    multiplier = _SIZE_UNITS.get(unit)
    if multiplier is None:
        return None
    return int(num * multiplier)


def human_size(size_bytes):
    """Format bytes to human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def setup_logging(verbose, log_file):
    """Configure logging: silent / verbose / file."""
    handlers = []
    fmt = "[%(asctime)s] %(message)s"
    datefmt = "%H:%M:%S"

    if verbose:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        handlers.append(h)

    if log_file:
        h = logging.FileHandler(log_file, encoding="utf-8")
        h.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        handlers.append(h)

    if handlers:
        logging.basicConfig(level=logging.INFO, handlers=handlers)
    else:
        logging.basicConfig(level=logging.CRITICAL + 1)


def format_duration(seconds):
    """Format seconds to HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_tmp_dir_size(tmp_dir):
    """Get total size of all files in tmp directory."""
    total = 0
    if not os.path.isdir(tmp_dir):
        return 0
    for f in os.listdir(tmp_dir):
        path = os.path.join(tmp_dir, f)
        if os.path.isfile(path):
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
    return total


# ---------------------------------------------------------------------------
# Device listing
# ---------------------------------------------------------------------------

def list_devices(engine):
    """Print available audio and webcam devices, then exit."""
    print("音频设备:")
    try:
        found = False
        for i in range(engine.audio_recorder.get_device_count()):
            if engine.audio_recorder.is_input_device(i):
                name = engine.audio_recorder.get_device_name(i)
                api = engine.audio_recorder.get_api_name(i)
                print(f"  [{i}] {name} ({api})")
                found = True
        if not found:
            print("  (未找到音频输入设备)")
    except Exception as e:
        print(f"  (枚举音频设备失败: {e})")

    print()
    print("摄像头设备:")
    try:
        dshow = engine.device_enumerator.list_all()
        cams = dshow.get("webcam", [])
        if cams:
            for cam in cams:
                print(f"  {cam}")
        else:
            print("  (未找到摄像头设备)")
    except Exception as e:
        print(f"  (枚举摄像头设备失败: {e})")


# ---------------------------------------------------------------------------
# Schedule waiting
# ---------------------------------------------------------------------------

def wait_for_schedule(schedule_str):
    """Wait until the scheduled HH:MM time. Returns when the time arrives."""
    parts = schedule_str.split(":")
    target_h, target_m = int(parts[0]), int(parts[1])

    now = datetime.now()
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    if target <= now:
        # Target time already passed today, schedule for tomorrow
        target += timedelta(days=1)

    wait_seconds = (target - now).total_seconds()
    log.info("等待定时开始: %s (还需等待 %s)", schedule_str, format_duration(wait_seconds))
    print(f"定时录制: 将在 {schedule_str} 开始录制 (等待 {format_duration(wait_seconds)})")

    # Sleep in short intervals so Ctrl+C can interrupt
    end_time = time.time() + wait_seconds
    while time.time() < end_time:
        remaining = end_time - time.time()
        time.sleep(min(remaining, 1.0))


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------

def create_cli_settings(base_dir, config_path=None):
    """Create an isolated SettingsManager that never overwrites Web UI's settings.json.

    Uses a temp directory so all changes are transient.
    If config_path is provided, the file is copied into the temp dir as settings.json
    so SettingsManager can load it regardless of the original filename.
    """
    import json
    import shutil

    tmp_settings_dir = tempfile.mkdtemp(prefix="screen_record_cli_")

    if config_path:
        # Copy user-specified config file as settings.json in the temp dir
        dst = os.path.join(tmp_settings_dir, "settings.json")
        shutil.copy2(config_path, dst)
    else:
        # Load defaults from base_dir settings.json if it exists, for --list-devices etc.
        src = os.path.join(base_dir, "settings.json")
        if os.path.isfile(src):
            dst = os.path.join(tmp_settings_dir, "settings.json")
            shutil.copy2(src, dst)

    return SettingsManager(tmp_settings_dir), tmp_settings_dir


def apply_settings(settings_manager, args):
    """Apply CLI args to SettingsManager (transient, never persisted to project dir)."""
    changes = {}
    if args.fps is not None:
        changes["fps"] = args.fps
    if args.encoder is not None:
        changes["encoder"] = args.encoder
    if args.no_mouse:
        changes["draw_mouse"] = False
    if args.no_audio:
        changes["audio_mode"] = "default"
        changes["audio_devices"] = []
    elif args.audio_devices is not None:
        changes["audio_mode"] = "selected"
        changes["audio_devices"] = args.audio_devices
    if changes:
        settings_manager.update(changes)


def build_config(args):
    """Convert CLI args to the config dict expected by RecordingEngine.start_recording()."""
    config = {}
    if args.output:
        config["filename"] = args.output
    if args.window:
        config["source"] = "title"
        config["window_title"] = args.window
    else:
        config["source"] = "desktop"
        config["window_title"] = ""
    config["webcam"] = args.webcam
    config["webcam_device"] = args.webcam_device or ""
    return config


# ---------------------------------------------------------------------------
# Stop condition monitor
# ---------------------------------------------------------------------------

class StopConditionMonitor:
    """Background thread that monitors stop conditions and triggers engine.stop_recording()."""

    def __init__(self, engine, duration=0, max_size_bytes=0, tmp_dir="tmp",
                 verbose=False):
        self._engine = engine
        self._duration = duration
        self._max_size_bytes = max_size_bytes
        self._tmp_dir = tmp_dir
        self._verbose = verbose
        self._stop_event = threading.Event()
        self._thread = None
        self._start_time = None
        self._stop_reason = None

    @property
    def stop_reason(self):
        return self._stop_reason

    def start(self):
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def _monitor_loop(self):
        last_status_print = 0
        while not self._stop_event.is_set():
            state = self._engine.get_state()
            if state["state"] != "recording":
                return

            elapsed = time.time() - self._start_time

            # Check duration
            if self._duration > 0 and elapsed >= self._duration:
                self._stop_reason = "duration"
                log.info("已达到设定时长 (%s)，停止录制",
                         format_duration(self._duration))
                self._engine.stop_recording()
                return

            # Check file size
            if self._max_size_bytes > 0:
                current_size = get_tmp_dir_size(self._tmp_dir)
                if current_size >= self._max_size_bytes:
                    self._stop_reason = "max_size"
                    log.info("已达到文件大小上限 (%s)，停止录制",
                             human_size(self._max_size_bytes))
                    self._engine.stop_recording()
                    return

            # Verbose status print every 5 seconds
            if self._verbose and time.time() - last_status_print >= 5:
                parts = [f"录制中... {format_duration(elapsed)}"]
                if self._duration > 0:
                    remaining = max(0, self._duration - elapsed)
                    parts.append(f"剩余 {format_duration(remaining)}")
                current_size = get_tmp_dir_size(self._tmp_dir)
                if current_size > 0:
                    parts.append(human_size(current_size))
                log.info(" | ".join(parts))
                last_status_print = time.time()

            self._stop_event.wait(1.0)

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _cleanup(engine, tmp_settings_dir=None):
    """Clean up resources."""
    try:
        engine.audio_recorder.destroy()
    except Exception:
        pass
    if tmp_settings_dir:
        import shutil
        shutil.rmtree(tmp_settings_dir, ignore_errors=True)


def main():
    args = parse_args()
    setup_logging(args.verbose, args.log_file)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    tmp_settings_dir = None

    # Load settings into an isolated temp dir (never overwrites Web UI's settings.json)
    if args.config:
        config_path = os.path.abspath(args.config)
        if not os.path.isfile(config_path):
            print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
            sys.exit(2)
        settings, tmp_settings_dir = create_cli_settings(base_dir, config_path)
    else:
        settings, tmp_settings_dir = create_cli_settings(base_dir)

    engine = RecordingEngine(base_dir, settings)

    # --list-devices
    if args.list_devices:
        list_devices(engine)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(0)

    # Check FFmpeg
    if not engine.has_ffmpeg():
        print("错误: 未找到 ffmpeg.exe，请将 ffmpeg.exe 放在项目根目录", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(1)

    # Apply CLI args to settings (writes only to temp dir, not project dir)
    apply_settings(settings, args)

    # Handle --no-audio: override the engine's audio recorder
    if args.no_audio:
        engine.audio_recorder.devices = []

    # Output directory override
    if args.output_dir:
        engine.captures_dir = os.path.abspath(args.output_dir)
        os.makedirs(engine.captures_dir, exist_ok=True)

    # --schedule: wait for the scheduled time
    if args.schedule:
        try:
            wait_for_schedule(args.schedule)
        except KeyboardInterrupt:
            print("\n等待被取消", file=sys.stderr)
            _cleanup(engine, tmp_settings_dir)
            sys.exit(0)

    # Build recording config
    config = build_config(args)

    # Signal handling: Ctrl+C sets a flag; main loop does the actual stop
    # to avoid deadlock (signal handler runs in main thread which may hold engine lock)
    interrupted = threading.Event()

    def signal_handler(sig, frame):
        if interrupted.is_set():
            # Second Ctrl+C: force exit (bypasses Python cleanup)
            print("\n强制退出", file=sys.stderr)
            os._exit(1)
        interrupted.set()
        log.info("收到中断信号，正在停止录制...")

    signal.signal(signal.SIGINT, signal_handler)

    # Start recording
    try:
        engine.start_recording(config)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(1)
    except Exception as e:
        print(f"启动录制失败: {e}", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(1)

    filename = engine.get_state()["filename"]
    log.info("开始录制 → %s", filename)
    if args.verbose:
        info_parts = [f"FPS={settings.get('fps')}", f"编码={settings.get('encoder')}"]
        if args.duration > 0:
            info_parts.append(f"时长={format_duration(args.duration)}")
        if args._max_size_bytes > 0:
            info_parts.append(f"大小上限={args.max_size}")
        log.info("录制参数: %s", " | ".join(info_parts))

    # Start stop condition monitor
    monitor = StopConditionMonitor(
        engine=engine,
        duration=args.duration,
        max_size_bytes=args._max_size_bytes,
        tmp_dir=engine.tmp_dir,
        verbose=args.verbose,
    )
    monitor.start()

    # Wait for recording to finish (state goes from recording/merging back to idle)
    last_version = 0
    was_recording = False
    logged_merging = False
    try:
        while True:
            version, state = engine.wait_for_state_change(last_version, timeout=1.0)
            last_version = version

            # Check if Ctrl+C was pressed; stop recording from main thread (avoids deadlock)
            if interrupted.is_set() and state["state"] == "recording":
                engine.stop_recording()

            if state["state"] == "recording":
                was_recording = True
            elif state["state"] == "merging" and not logged_merging:
                log.info("停止录制，正在合并音视频...")
                logged_merging = True
            elif state["state"] == "idle" and was_recording:
                break
            elif state["state"] == "idle" and not was_recording:
                # Engine crashed before we even saw "recording" state
                break
    except KeyboardInterrupt:
        # Already handled by signal_handler, just wait for merge
        pass

    monitor.stop()

    # Check result
    error = engine.get_state().get("error")
    if error:
        log.info("录制出错: %s", error)
        print(f"错误: {error}", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(1)

    # Report result
    output_path = os.path.join(engine.captures_dir, filename)
    if os.path.isfile(output_path):
        size = os.path.getsize(output_path)
        log.info("完成! 文件: %s (%s)", output_path, human_size(size))
        # Always print the output file path to stdout (for scripting)
        print(output_path)
    else:
        log.info("警告: 输出文件不存在: %s", output_path)
        print(f"警告: 输出文件不存在: {output_path}", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(1)

    _cleanup(engine, tmp_settings_dir)
    sys.exit(0)


if __name__ == "__main__":
    main()
