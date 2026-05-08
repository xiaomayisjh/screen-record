# Real-Time Disk Flush & Crash Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time disk flushing (fragmented MP4 direct write) and automatic crash recovery (file repair) to the screen recorder, with full API and CLI control.

**Architecture:** FFmpeg writes directly to the final .mp4 file using fragmented MP4 format (`-movflags +frag_keyframe+empty_moov -flush_packets 1`). A `.recording` marker file tracks active recordings. On stop, files are remuxed to standard MP4. On crash, the fragmented MP4 remains playable and is auto-repaired on next startup. Repair is also available on-demand via API and CLI.

**Tech Stack:** Python (Flask, threading, subprocess), Rust (Axum, tokio, async/await), FFmpeg (fragmented MP4, remux, ffprobe)

---

## File Structure

### Python

| File | Responsibility |
|------|---------------|
| `python/recorder/repair.py` | **NEW** — File health check, repair logic, marker file management |
| `python/recorder/cmd_builder.py` | **MODIFY** — Add fragmented MP4 params, remux command builder, simplify merge codec logic |
| `python/recorder/engine.py` | **MODIFY** — Direct write to captures_dir, marker file, auto-repair on init, repair methods |
| `python/web/api.py` | **MODIFY** — New repair/health endpoints, modified responses |
| `python/cli.py` | **MODIFY** — New --repair, --repair-file, --check-files, --json arguments |
| `python/tests/test_repair.py` | **NEW** — Tests for repair functionality |
| `python/tests/test_hwaccel.py` | **MODIFY** — Update capture cmd tests for fragmented MP4 params |

### Rust

| File | Responsibility |
|------|---------------|
| `rust/src/main.rs` | **MODIFY** — Fragmented MP4 params, marker file, auto-repair, new API routes, new CLI args, repair logic |
| `rust/assets/app.js` | **MODIFY** — UI updates for file health status display |

---

### Task 1: Create repair.py — File Health Check & Repair Logic

**Files:**
- Create: `python/recorder/repair.py`
- Test: `python/tests/test_repair.py`

- [ ] **Step 1: Write the failing test for health check**

```python
# python/tests/test_repair.py
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import subprocess
if not hasattr(subprocess, 'STARTUPINFO'):
    subprocess.STARTUPINFO = type('STARTUPINFO', (), {'dwFlags': 0, 'wShowWindow': 0})
    subprocess.STARTF_USESHOWWINDOW = 1
    subprocess.SW_HIDE = 0

sys.modules.setdefault('pyaudio', MagicMock())

from recorder.repair import FileHealthChecker, RepairResult, FileHealth


class TestFileHealth(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "test_repair_tmp")
        os.makedirs(self.temp_dir, exist_ok=True)
        self.ffmpeg_path = os.path.join(self.temp_dir, "ffmpeg.exe")
        with open(self.ffmpeg_path, "w") as f:
            f.write("fake")
        self.checker = FileHealthChecker(self.ffmpeg_path, self.temp_dir)

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_health_enum_values(self):
        self.assertEqual(FileHealth.HEALTHY.value, "healthy")
        self.assertEqual(FileHealth.FRAGMENTED.value, "fragmented")
        self.assertEqual(FileHealth.BROKEN.value, "broken")

    @patch.object(FileHealthChecker, '_run_ffprobe')
    def test_check_file_healthy(self, mock_probe):
        mock_probe.return_value = 0
        test_file = os.path.join(self.temp_dir, "test.mp4")
        with open(test_file, "w") as f:
            f.write("fake mp4")
        result = self.checker.check_file(test_file)
        self.assertEqual(result, FileHealth.HEALTHY)

    @patch.object(FileHealthChecker, '_run_ffprobe')
    def test_check_file_broken(self, mock_probe):
        mock_probe.return_value = 1
        test_file = os.path.join(self.temp_dir, "test.mp4")
        with open(test_file, "w") as f:
            f.write("broken")
        result = self.checker.check_file(test_file)
        self.assertEqual(result, FileHealth.BROKEN)

    def test_check_file_not_exists(self):
        result = self.checker.check_file("/nonexistent/file.mp4")
        self.assertEqual(result, FileHealth.BROKEN)

    @patch.object(FileHealthChecker, '_run_ffprobe')
    def test_check_file_fragmented(self, mock_probe):
        mock_probe.return_value = 0
        test_file = os.path.join(self.temp_dir, "test.mp4")
        with open(test_file, "w") as f:
            f.write("ftypisom")
        result = self.checker.check_file(test_file)
        self.assertEqual(result, FileHealth.FRAGMENTED)


class TestRepairResult(unittest.TestCase):
    def test_empty_result(self):
        result = RepairResult()
        self.assertEqual(result.repaired, [])
        self.assertEqual(result.failed, [])
        self.assertEqual(result.healthy, [])

    def test_to_dict(self):
        result = RepairResult(
            repaired=["a.mp4"],
            failed=[{"name": "b.mp4", "error": "broken"}],
            healthy=["c.mp4"],
        )
        d = result.to_dict()
        self.assertIn("repaired", d)
        self.assertIn("failed", d)
        self.assertIn("healthy", d)
        self.assertEqual(d["repaired"], ["a.mp4"])


class TestMarkerFile(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "test_marker_tmp")
        os.makedirs(self.temp_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_write_and_read_marker(self):
        from recorder.repair import write_recording_marker, read_recording_marker
        write_recording_marker(self.temp_dir, "test.mp4", "h264_qsv", "qsv")
        info = read_recording_marker(self.temp_dir)
        self.assertIsNotNone(info)
        self.assertEqual(info["filename"], "test.mp4")
        self.assertEqual(info["encoder"], "h264_qsv")

    def test_read_marker_no_file(self):
        from recorder.repair import read_recording_marker
        info = read_recording_marker(self.temp_dir)
        self.assertIsNone(info)

    def test_delete_marker(self):
        from recorder.repair import write_recording_marker, read_recording_marker, delete_recording_marker
        write_recording_marker(self.temp_dir, "test.mp4", "mpeg4", None)
        delete_recording_marker(self.temp_dir)
        info = read_recording_marker(self.temp_dir)
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/python && python -m pytest tests/test_repair.py -v 2>&1 | head -30`
Expected: FAIL — `ModuleNotFoundError: No module named 'recorder.repair'`

- [ ] **Step 3: Write the repair.py implementation**

```python
# python/recorder/repair.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /workspace/python && python -m pytest tests/test_repair.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add python/recorder/repair.py python/tests/test_repair.py
git commit -m "feat: add file health checker and repair module"
```

---

### Task 2: Modify cmd_builder.py — Fragmented MP4 Params & Remux Command

**Files:**
- Modify: `python/recorder/cmd_builder.py`
- Test: `python/tests/test_hwaccel.py`

- [ ] **Step 1: Write the failing test for fragmented MP4 params**

Add to `python/tests/test_hwaccel.py` in the `TestCmdBuilder` class:

```python
    def test_capture_cmd_includes_movflags(self):
        self.builder.config(encoder="mpeg4")
        cmd = self.builder.get_capture_cmd("out.mp4")
        self.assertIn("-movflags", cmd)
        movflags_idx = cmd.index("-movflags")
        self.assertIn("frag_keyframe", cmd[movflags_idx + 1])
        self.assertIn("empty_moov", cmd[movflags_idx + 1])

    def test_capture_cmd_includes_flush_packets(self):
        self.builder.config(encoder="mpeg4")
        cmd = self.builder.get_capture_cmd("out.mp4")
        self.assertIn("-flush_packets", cmd)
        flush_idx = cmd.index("-flush_packets")
        self.assertEqual(cmd[flush_idx + 1], "1")

    def test_get_remux_cmd(self):
        self.builder.config(encoder="mpeg4")
        cmd = self.builder.get_remux_cmd("input.mp4", "output.mp4")
        self.assertIn("-movflags", cmd)
        self.assertIn("+faststart", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("copy", cmd)
        self.assertIn("input.mp4", cmd)
        self.assertIn("output.mp4", cmd)

    def test_get_merge_cmd_uses_copy_without_webcam(self):
        self.builder.config(encoder="h264_qsv")
        self.builder.aud_list = [None]
        cmd = self.builder.get_merge_cmd("out.mp4")
        cv_idx = cmd.index("-c:v")
        self.assertEqual(cmd[cv_idx + 1], "copy")

    def test_get_merge_cmd_reencodes_with_webcam(self):
        self.builder.config(encoder="h264_qsv", webcam=True)
        self.builder.aud_list = [None]
        cmd = self.builder.get_merge_cmd("out.mp4")
        self.assertIn("h264_qsv", cmd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /workspace/python && python -m pytest tests/test_hwaccel.py::TestCmdBuilder::test_capture_cmd_includes_movflags -v`
Expected: FAIL — `AssertionError` (movflags not in cmd)

- [ ] **Step 3: Modify cmd_builder.py**

In `python/recorder/cmd_builder.py`, modify the `get_capture_cmd` method to add fragmented MP4 params and flush_packets. Add a new `get_remux_cmd` method. Simplify the merge codec logic:

```python
    def get_capture_cmd(self, filename):
        cmd = [self.ffmpeg]
        self._add_hwaccel_params(cmd)
        cmd.extend(["-f", "gdigrab"])
        cmd.extend(["-framerate", str(self.fps)])
        cmd.extend(["-draw_mouse", str(self.draw_mouse)])
        cmd.extend(["-i", self.source])

        self._add_encoder_params(cmd)

        cmd.extend(["-movflags", "+frag_keyframe+empty_moov"])
        cmd.extend(["-flush_packets", "1"])
        cmd.extend(["-y", filename])
        return cmd

    def get_remux_cmd(self, input_path, output_path):
        cmd = [self.ffmpeg]
        cmd.extend(["-i", input_path])
        cmd.extend(["-c:v", "copy"])
        cmd.extend(["-movflags", "+faststart"])
        cmd.extend(["-y", output_path])
        return cmd
```

And modify the merge command to simplify codec selection — use `-c:v copy` when no webcam overlay, re-encode only with webcam:

```python
    def get_merge_cmd(self, filename):
        cmd = [self.ffmpeg]
        self._add_hwaccel_params(cmd)
        cmd.extend(["-i", os.path.join(self.tmp_dir, "tmp.mkv")])
        for i in range(len(self.aud_list)):
            cmd.extend(["-i", os.path.join(self.tmp_dir, f"tmp_{i}.wav")])

        if len(self.aud_list) > 0:
            delay_ms = self.audio_delay_ms

            if len(self.aud_list) == 1:
                if delay_ms > 0:
                    cmd.extend(["-af", f"adelay={delay_ms}|{delay_ms}"])
            else:
                merge_inputs = "".join(
                    [f"[{i+1}:a]" for i in range(len(self.aud_list))]
                )
                if delay_ms > 0:
                    cmd.extend([
                        "-filter_complex",
                        f"{merge_inputs}amerge=inputs={len(self.aud_list)}[merged];[merged]adelay={delay_ms}|{delay_ms}[out]",
                        "-map", "0:v", "-map", "[out]",
                    ])
                else:
                    cmd.extend([
                        "-filter_complex",
                        f"{merge_inputs}amerge=inputs={len(self.aud_list)}[out]",
                        "-map", "0:v", "-map", "[out]",
                    ])
            cmd.extend(["-ac", "2"])

        if self.enable_webcam:
            webcam_path = os.path.join(self.tmp_dir, "webcamtmp.mkv")
            cmd.extend([
                "-i", webcam_path,
                "-vf", "[2:v] scale=640:-1 [inner]; [0:0][inner] overlay=0:0 [out]",
                "-map", "[out]",
            ])
            self._add_encoder_params(cmd)
        else:
            cmd.extend(["-c:v", "copy"])

        cmd.extend(["-movflags", "+faststart"])
        cmd.extend(["-shortest"])
        cmd.extend(["-y", filename])
        return cmd
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /workspace/python && python -m pytest tests/test_hwaccel.py -v`
Expected: All tests PASS (may need to fix existing tests that assumed old merge behavior)

- [ ] **Step 5: Commit**

```bash
git add python/recorder/cmd_builder.py python/tests/test_hwaccel.py
git commit -m "feat: add fragmented MP4 params and remux command to cmd_builder"
```

---

### Task 3: Modify engine.py — Direct Write, Marker File, Auto-Repair

**Files:**
- Modify: `python/recorder/engine.py`

- [ ] **Step 1: Update imports and add repair integration**

At the top of `python/recorder/engine.py`, add import:

```python
from .repair import (
    FileHealthChecker, write_recording_marker,
    read_recording_marker, delete_recording_marker,
)
```

- [ ] **Step 2: Add health_checker to __init__**

In `RecordingEngine.__init__`, after `self._fallback_info = None`, add:

```python
        self.health_checker = FileHealthChecker(
            os.path.join(base_dir, "ffmpeg.exe"), self.captures_dir
        )
```

And at the end of `__init__`, after `self._cleanup_tmp()`, add auto-repair:

```python
        repair_result = self.health_checker.auto_repair_on_startup()
        if repair_result.repaired or repair_result.failed:
            import logging
            log = logging.getLogger("engine")
            for name in repair_result.repaired:
                log.info("Auto-repaired: %s", name)
            for item in repair_result.failed:
                log.warning("Auto-repair failed: %s — %s", item["name"], item["error"])
```

- [ ] **Step 3: Modify _start_capture to write directly to captures_dir**

In `_start_capture`, change the video output path from tmp to captures_dir:

```python
    def _start_capture(self, settings, config):
        self.cmd_builder.config(
            fps=settings["fps"],
            encoder=self._current_encoder,
            draw_mouse=settings["draw_mouse"],
        )
        self.cmd_builder.set_source(
            config.get("source") == "title",
            config.get("window_title", ""),
        )

        self._current_hwaccel = self.cmd_builder.hwaccel

        output_path = os.path.join(self.captures_dir, self._filename)
        video_cmd = self.cmd_builder.get_capture_cmd(output_path)
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

        self._recording_start = time.time()

        write_recording_marker(
            self.captures_dir, self._filename,
            self._current_encoder, self._current_hwaccel,
        )

        audio_devices = settings.get("audio_devices", [])
        if settings.get("audio_mode") == "default" or not audio_devices:
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
```

- [ ] **Step 4: Modify _merge to read from captures_dir and use remux/merge**

Replace the `_merge` method:

```python
    def _merge(self):
        try:
            output_path = os.path.join(self.captures_dir, self._filename)
            audio_ok = self._check_audio_files()
            has_webcam = self._webcam_capturer is not None

            if not audio_ok and not has_webcam:
                tmp_remux = output_path + ".remux.mp4"
                remux_cmd = self.cmd_builder.get_remux_cmd(output_path, tmp_remux)
                stderr_file = open(
                    os.path.join(self.tmp_dir, "remux_stderr.log"), "w",
                    encoding="utf-8", errors="replace",
                )
                proc = subprocess.Popen(
                    args=remux_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_file,
                    startupinfo=_startupinfo,
                )
                proc.wait()
                stderr_file.close()
                if proc.returncode == 0 and os.path.isfile(tmp_remux):
                    os.replace(tmp_remux, output_path)
                elif os.path.isfile(tmp_remux):
                    os.remove(tmp_remux)
            else:
                tmp_merged = output_path + ".merged.mp4"
                audio_delay_ms = 0
                if self._audio_start and self._recording_start:
                    delay = self._audio_start - self._recording_start
                    if delay > 0:
                        audio_delay_ms = int(delay * 1000)

                devices = self.audio_recorder.devices
                self.cmd_builder.config(
                    aud_list=devices, audio_delay_ms=audio_delay_ms
                )
                merge_cmd = self.cmd_builder.get_merge_cmd(tmp_merged)
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
                    if os.path.isfile(tmp_merged):
                        os.remove(tmp_merged)
                else:
                    if os.path.isfile(tmp_merged):
                        os.replace(tmp_merged, output_path)

            delete_recording_marker(self.captures_dir)
            self._cleanup_tmp()
        except Exception as e:
            self._error_message = str(e)
            delete_recording_marker(self.captures_dir)
        finally:
            with self._lock:
                self._state = "idle"
                self._notify_state_change()
```

- [ ] **Step 5: Add output_path to get_state**

In `_get_state_unlocked`, add `output_path`:

```python
    def _get_state_unlocked(self):
        result = {
            "state": self._state,
            "recording": self._state == "recording",
            "merging": self._state == "merging",
            "filename": self._filename,
            "elapsed": 0,
            "error": self._error_message,
            "encoder": self._current_encoder,
            "hwaccel": self._current_hwaccel,
            "fallback": self._fallback_info,
            "output_path": os.path.join(self.captures_dir, self._filename) if self._filename else None,
        }
        if self._state == "recording" and self._recording_start:
            result["elapsed"] = time.time() - self._recording_start
        return result
```

- [ ] **Step 6: Modify _monitor to not delete output on crash**

In `_monitor`, when FFmpeg crashes and fallback is not possible, the output file is already in captures_dir. Remove the `self._cleanup_tmp()` call in the crash path (the marker file will trigger auto-repair on next startup). Change the crash cleanup:

```python
                with self._lock:
                    self._error_message = f"FFmpeg crashed: {stderr_out}"
                    self._state = "idle"
                    self._notify_state_change()
                self.audio_recorder.stop()
                if self._webcam_capturer:
                    self._webcam_capturer.stop()
                delete_recording_marker(self.captures_dir)
                self._cleanup_tmp()
                return
```

- [ ] **Step 7: Add repair methods to engine**

Add these methods to `RecordingEngine`:

```python
    def check_files_health(self):
        return self.health_checker.check_all_files()

    def repair_file(self, name):
        return self.health_checker.repair_file(name)

    def repair_all_files(self):
        return self.health_checker.repair_all()
```

- [ ] **Step 8: Run existing tests**

Run: `cd /workspace/python && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add python/recorder/engine.py
git commit -m "feat: direct write to captures_dir with marker file and auto-repair"
```

---

### Task 4: Modify api.py — New Repair/Health Endpoints

**Files:**
- Modify: `python/web/api.py`

- [ ] **Step 1: Add new API endpoints**

In `python/web/api.py`, add the following endpoints after the existing `/api/hwaccels` route:

```python
    @app.route("/api/files/health")
    def api_files_health():
        health_map = _engine().check_files_health()
        result = {}
        for name, health in health_map.items():
            result[name] = health.value
        return jsonify({"health": result})

    @app.route("/api/files/<name>/repair", methods=["POST"])
    def api_repair_file(name):
        success, error = _engine().repair_file(name)
        if success:
            return jsonify({"ok": True, "name": name})
        return jsonify({"ok": False, "name": name, "error": error}), 500

    @app.route("/api/repair", methods=["POST"])
    def api_repair_all():
        result = _engine().repair_all_files()
        return jsonify({"ok": True, **result.to_dict()})
```

- [ ] **Step 2: Modify /api/record/start response**

Change the start endpoint to include `output_path`:

```python
    @app.route("/api/record/start", methods=["POST"])
    def api_start():
        data = request.get_json(force=True, silent=True) or {}
        try:
            _engine().start_recording({
                "filename": data.get("filename"),
                "source": data.get("source", "desktop"),
                "window_title": data.get("window_title", ""),
                "webcam": data.get("webcam", False),
                "webcam_device": data.get("webcam_device", ""),
            })
            state = _engine().get_state()
            return jsonify({"ok": True, "filename": _engine()._filename, "output_path": state.get("output_path")})
        except RuntimeError as e:
            return jsonify({"ok": False, "error": str(e)}), 409
```

- [ ] **Step 3: Modify /api/files response to include health**

```python
    @app.route("/api/files")
    def api_files():
        files = _engine().list_files()
        health_map = _engine().check_files_health()
        for f in files:
            f["health"] = health_map.get(f["name"], FileHealth.HEALTHY).value
        return jsonify({"files": files})
```

Add import at top:

```python
from recorder.repair import FileHealth
```

- [ ] **Step 4: Run the Flask app manually to verify**

Run: `cd /workspace/python && python -c "from web.api import register_api; print('API module OK')"`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add python/web/api.py
git commit -m "feat: add repair/health API endpoints and output_path in responses"
```

---

### Task 5: Modify cli.py — New Repair/Check Arguments

**Files:**
- Modify: `python/cli.py`

- [ ] **Step 1: Add new CLI arguments**

In `parse_args`, add to the `conf` argument group:

```python
    conf.add_argument("--repair", action="store_true",
                      help="扫描并修复所有损坏的录制文件后退出")
    conf.add_argument("--repair-file", metavar="FILE", default=None,
                      help="修复指定的录制文件后退出")
    conf.add_argument("--check-files", action="store_true",
                      help="检查所有录制文件的健康状态后退出")
    conf.add_argument("--json", action="store_true",
                      help="以 JSON 格式输出结果 (方便脚本解析)")
```

- [ ] **Step 2: Add repair/check handling in main()**

In `main()`, after the `--list-devices` block and before the FFmpeg check, add:

```python
    if args.repair:
        from recorder.repair import FileHealthChecker
        checker = FileHealthChecker(
            os.path.join(base_dir, "ffmpeg.exe"),
            engine.captures_dir,
        )
        result = checker.repair_all()
        if args.json:
            import json as _json
            print(_json.dumps(result.to_dict(), ensure_ascii=False))
        else:
            if result.repaired:
                print("已修复:")
                for name in result.repaired:
                    print(f"  ✓ {name}")
            if result.failed:
                print("修复失败:")
                for item in result.failed:
                    print(f"  ✗ {item['name']}: {item['error']}")
            if result.healthy:
                print(f"健康文件: {len(result.healthy)} 个")
        _cleanup(engine, tmp_settings_dir)
        sys.exit(0 if not result.failed else 1)

    if args.repair_file:
        from recorder.repair import FileHealthChecker
        checker = FileHealthChecker(
            os.path.join(base_dir, "ffmpeg.exe"),
            engine.captures_dir,
        )
        success, error = checker.repair_file(args.repair_file)
        if args.json:
            import json as _json
            print(_json.dumps({"ok": success, "name": args.repair_file, "error": error}, ensure_ascii=False))
        else:
            if success:
                print(f"已修复: {args.repair_file}")
            else:
                print(f"修复失败: {args.repair_file} — {error}", file=sys.stderr)
        _cleanup(engine, tmp_settings_dir)
        sys.exit(0 if success else 1)

    if args.check_files:
        from recorder.repair import FileHealthChecker
        checker = FileHealthChecker(
            os.path.join(base_dir, "ffmpeg.exe"),
            engine.captures_dir,
        )
        health_map = checker.check_all_files()
        if args.json:
            import json as _json
            print(_json.dumps({k: v.value for k, v in health_map.items()}, ensure_ascii=False))
        else:
            if not health_map:
                print("没有录制文件")
            else:
                for name, health in health_map.items():
                    icon = {"healthy": "✓", "fragmented": "⚠", "broken": "✗"}.get(health.value, "?")
                    print(f"  {icon} {name} ({health.value})")
        _cleanup(engine, tmp_settings_dir)
        sys.exit(0)
```

- [ ] **Step 3: Verify CLI help**

Run: `cd /workspace/python && python cli.py --help`
Expected: New arguments appear in help output

- [ ] **Step 4: Commit**

```bash
git add python/cli.py
git commit -m "feat: add --repair, --repair-file, --check-files, --json CLI arguments"
```

---

### Task 6: Modify Rust main.rs — Fragmented MP4, Marker, Repair, API, CLI

**Files:**
- Modify: `rust/src/main.rs`

This is the largest task. The Rust version mirrors all Python changes.

- [ ] **Step 1: Add fragmented MP4 params to build_capture_cmd**

In `build_capture_cmd`, before the output filename, add:

```rust
    cmd.extend(["-movflags".to_string(), "+frag_keyframe+empty_moov".to_string()]);
    cmd.extend(["-flush_packets".to_string(), "1".to_string()]);
```

- [ ] **Step 2: Add build_remux_cmd function**

```rust
fn build_remux_cmd(ffmpeg: &Path, input: &Path, output: &Path) -> Vec<String> {
    vec![
        ffmpeg.to_string_lossy().to_string(),
        "-i".to_string(),
        input.to_string_lossy().to_string(),
        "-c:v".to_string(),
        "copy".to_string(),
        "-movflags".to_string(),
        "+faststart".to_string(),
        "-y".to_string(),
        output.to_string_lossy().to_string(),
    ]
}
```

- [ ] **Step 3: Simplify merge codec logic and add faststart**

In `build_merge_cmd`, replace the video codec section:

```rust
    if !has_webcam {
        cmd.extend(["-c:v".to_string(), "copy".to_string()]);
    }
```

And add before the final `-y`:

```rust
    cmd.extend(["-movflags".to_string(), "+faststart".to_string()]);
```

- [ ] **Step 4: Add FileHealth enum and RepairResult struct**

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum FileHealth {
    Healthy,
    Fragmented,
    Broken,
}

#[derive(Debug, Clone, Serialize)]
struct RepairResult {
    repaired: Vec<String>,
    failed: Vec<FailedRepair>,
    healthy: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct FailedRepair {
    name: String,
    error: String,
}

#[derive(Debug, Serialize)]
struct HealthResponse {
    health: std::collections::HashMap<String, FileHealth>,
}
```

- [ ] **Step 5: Add marker file functions**

```rust
const MARKER_FILENAME: &str = ".recording";

fn write_recording_marker(captures_dir: &Path, filename: &str, encoder: &str, hwaccel: Option<&str>) {
    let marker = serde_json::json!({
        "filename": filename,
        "started_at": Local::now().to_rfc3339(),
        "encoder": encoder,
        "hwaccel": hwaccel.unwrap_or(""),
    });
    let _ = std::fs::write(captures_dir.join(MARKER_FILENAME), marker.to_string());
}

fn read_recording_marker(captures_dir: &Path) -> Option<serde_json::Value> {
    let path = captures_dir.join(MARKER_FILENAME);
    if !path.is_file() {
        return None;
    }
    std::fs::read_to_string(&path)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok())
}

fn delete_recording_marker(captures_dir: &Path) {
    let _ = std::fs::remove_file(captures_dir.join(MARKER_FILENAME));
}
```

- [ ] **Step 6: Add FileHealthChecker to EngineInner and RecordingEngine**

Add to `RecordingEngine`:

```rust
    ffprobe_path: PathBuf,
```

Add health check and repair methods to `RecordingEngine`:

```rust
    fn check_file_health(&self, filepath: &Path) -> FileHealth {
        if !filepath.is_file() {
            return FileHealth::Broken;
        }
        let output = Command::new(&self.ffprobe_path)
            .args(["-v", "error", "-show_format"])
            .arg(filepath)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output();

        match output {
            Ok(o) if o.status.success() => {
                if is_likely_fragmented_mp4(filepath) {
                    FileHealth::Fragmented
                } else {
                    FileHealth::Healthy
                }
            }
            _ => FileHealth::Broken,
        }
    }

    fn check_all_files_health(&self) -> std::collections::HashMap<String, FileHealth> {
        let mut results = std::collections::HashMap::new();
        if let Ok(entries) = std::fs::read_dir(&self.captures_dir) {
            for entry in entries.filter_map(|e| e.ok()) {
                let path = entry.path();
                if path.is_file() {
                    if let Some(ext) = path.extension() {
                        let ext = ext.to_string_lossy().to_lowercase();
                        if ["mp4", "mkv", "avi"].contains(&ext.as_str()) {
                            let name = entry.file_name().to_string_lossy().to_string();
                            if !name.starts_with('.') {
                                results.insert(name, self.check_file_health(&path));
                            }
                        }
                    }
                }
            }
        }
        results
    }

    fn repair_file(&self, name: &str) -> Result<(), String> {
        let safe_name = Path::new(name)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let filepath = self.captures_dir.join(&safe_name);
        if !filepath.is_file() {
            return Err(format!("File not found: {}", safe_name));
        }

        let tmp_output = self.captures_dir.join(format!("{}.repairing.mp4", safe_name));
        let cmd = build_remux_cmd(&self.ffmpeg_path, &filepath, &tmp_output);

        let mut proc = spawn_ffmpeg(&cmd, None)
            .map_err(|e| format!("Failed to start repair: {}", e))?;

        let status = proc.wait()
            .map_err(|e| format!("Repair process error: {}", e))?;

        if status.success() && tmp_output.is_file() {
            std::fs::rename(&tmp_output, &filepath)
                .map_err(|e| format!("Failed to replace file: {}", e))
        } else {
            let _ = std::fs::remove_file(&tmp_output);
            let broken_path = format!("{}.broken", filepath.display());
            let _ = std::fs::rename(&filepath, &broken_path);
            Err(format!("FFmpeg repair failed (exit {:?})", status.code()))
        }
    }

    fn repair_all_files(&self) -> RepairResult {
        let mut result = RepairResult {
            repaired: vec![],
            failed: vec![],
            healthy: vec![],
        };
        let health_map = self.check_all_files_health();
        for (name, health) in health_map {
            match health {
                FileHealth::Healthy => result.healthy.push(name),
                FileHealth::Fragmented | FileHealth::Broken => {
                    match self.repair_file(&name) {
                        Ok(()) => result.repaired.push(name),
                        Err(e) => result.failed.push(FailedRepair { name, error: e }),
                    }
                }
            }
        }
        result
    }

    fn auto_repair_on_startup(&self) -> RepairResult {
        let mut result = RepairResult {
            repaired: vec![],
            failed: vec![],
            healthy: vec![],
        };

        if let Some(marker) = read_recording_marker(&self.captures_dir) {
            if let Some(filename) = marker.get("filename").and_then(|v| v.as_str()) {
                let filepath = self.captures_dir.join(filename);
                let health = self.check_file_health(&filepath);
                match health {
                    FileHealth::Healthy => result.healthy.push(filename.to_string()),
                    FileHealth::Fragmented | FileHealth::Broken => {
                        match self.repair_file(filename) {
                            Ok(()) => result.repaired.push(filename.to_string()),
                            Err(e) => result.failed.push(FailedRepair {
                                name: filename.to_string(),
                                error: e,
                            }),
                        }
                    }
                }
            }
            delete_recording_marker(&self.captures_dir);
        }

        let health_map = self.check_all_files_health();
        let already_processed: std::collections::HashSet<String> = result.repaired.iter()
            .chain(result.healthy.iter())
            .chain(result.failed.iter().map(|f| f.name.clone()))
            .cloned()
            .collect();

        for (name, health) in health_map {
            if already_processed.contains(&name) {
                continue;
            }
            match health {
                FileHealth::Healthy => result.healthy.push(name),
                FileHealth::Fragmented | FileHealth::Broken => {
                    match self.repair_file(&name) {
                        Ok(()) => result.repaired.push(name),
                        Err(e) => result.failed.push(FailedRepair { name, error: e }),
                    }
                }
            }
        }
        result
    }
```

Add helper function:

```rust
fn is_likely_fragmented_mp4(filepath: &Path) -> bool {
    if let Ok(mut f) = std::fs::File::open(filepath) {
        let mut header = [0u8; 12];
        if std::io::Read::read_exact(&mut f, &mut header).is_ok() {
            return &header[4..8] == b"ftyp";
        }
    }
    false
}
```

- [ ] **Step 7: Modify start_recording to write directly and use marker**

In `start_recording`, change the video output path:

```rust
        let output_path = self.captures_dir.join(&filename);
        let video_cmd = build_capture_cmd(&self.ffmpeg_path, &settings, &source, &output_path);
```

And add marker write after setting inner state:

```rust
        write_recording_marker(&self.captures_dir, &filename, &settings.encoder, None);
```

- [ ] **Step 8: Modify merge/remux in EngineRef**

Update `do_merge` to handle remux (no audio) and merge (with audio), writing to a temp file then replacing:

```rust
    async fn do_merge(&self) -> Result<(), String> {
        let encoder = self.settings.lock().await.get_all().encoder;

        let inner = self.inner.lock().await;
        let filename = inner.filename.clone()
            .ok_or_else(|| "No filename set".to_string())?;
        let audio_delay_ms = if let (Some(rec_start), Some(aud_start)) =
            (inner.recording_start, inner.audio_start)
        {
            aud_start.duration_since(rec_start).as_millis() as u64
        } else {
            0
        };
        let audio_count = inner.audio_device_count;
        let has_webcam = inner.has_webcam;
        drop(inner);

        let output_path = self.captures_dir.join(&filename);

        if audio_count == 0 && !has_webcam {
            let tmp_remux = self.captures_dir.join(format!("{}.remux.mp4", filename));
            let remux_cmd = build_remux_cmd(&self.ffmpeg_path, &output_path, &tmp_remux);
            let mut proc = spawn_ffmpeg(&remux_cmd, None)
                .map_err(|e| format!("Failed to start remux: {}", e))?;
            let status = tokio::task::spawn_blocking(move || proc.wait())
                .await
                .map_err(|e| format!("Remux task panicked: {}", e))?
                .map_err(|e| format!("Remux process error: {}", e))?;
            if status.success() && tmp_remux.is_file() {
                std::fs::rename(&tmp_remux, &output_path)
                    .map_err(|e| format!("Failed to replace file: {}", e))?;
            } else {
                let _ = std::fs::remove_file(&tmp_remux);
            }
        } else {
            let tmp_merged = self.captures_dir.join(format!("{}.merged.mp4", filename));
            let merge_cmd = build_merge_cmd(
                &self.ffmpeg_path, &self.tmp_dir, &tmp_merged,
                audio_count, audio_delay_ms, has_webcam, &encoder,
            );
            let stderr_file = std::fs::File::create(self.tmp_dir.join("merge_stderr.log")).ok();
            let mut proc = spawn_ffmpeg(&merge_cmd, stderr_file)
                .map_err(|e| format!("Failed to start merge: {}", e))?;
            let status = tokio::task::spawn_blocking(move || proc.wait())
                .await
                .map_err(|e| format!("Merge task panicked: {}", e))?
                .map_err(|e| format!("Merge process error: {}", e))?;
            if !status.success() {
                let stderr = read_log(&self.tmp_dir.join("merge_stderr.log"));
                let _ = std::fs::remove_file(&tmp_merged);
                return Err(format!("Merge failed (exit {}): {}", status.code().unwrap_or(-1), stderr));
            }
            if tmp_merged.is_file() {
                std::fs::rename(&tmp_merged, &output_path)
                    .map_err(|e| format!("Failed to replace file: {}", e))?;
            }
        }

        delete_recording_marker(&self.captures_dir);
        Ok(())
    }
```

- [ ] **Step 9: Add auto-repair on engine init**

In `RecordingEngine::new` / `with_captures_dir`, after creating the engine, add:

```rust
        let repair_result = engine.auto_repair_on_startup();
        if !repair_result.repaired.is_empty() || !repair_result.failed.is_empty() {
            for name in &repair_result.repaired {
                tracing::info!("Auto-repaired: {}", name);
            }
            for item in &repair_result.failed {
                tracing::warn!("Auto-repair failed: {} — {}", item.name, item.error);
            }
        }
```

- [ ] **Step 10: Add API routes for repair/health**

Add new API handlers:

```rust
async fn api_files_health(State(state): State<AppState>) -> Json<HealthResponse> {
    let health_map = state.engine.check_all_files_health();
    Json(HealthResponse { health: health_map })
}

async fn api_repair_file(
    State(state): State<AppState>,
    AxumPath(name): AxumPath<String>,
) -> Result<Json<serde_json::Value>, (StatusCode, Json<ErrorResponse>)> {
    match state.engine.repair_file(&name) {
        Ok(()) => Ok(Json(serde_json::json!({"ok": true, "name": name}))),
        Err(e) => Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(ErrorResponse { ok: false, error: e }),
        )),
    }
}

async fn api_repair_all(State(state): State<AppState>) -> Json<RepairResult> {
    Json(state.engine.repair_all_files())
}
```

Add routes to `create_router`:

```rust
        .route("/api/files/health", get(api_files_health))
        .route("/api/files/{name}/repair", post(api_repair_file))
        .route("/api/repair", post(api_repair_all))
```

- [ ] **Step 11: Add CLI arguments for repair**

In `CliArgs`, add:

```rust
    /// Scan and repair all broken recording files
    #[arg(long)]
    repair: bool,

    /// Repair a specific recording file
    #[arg(long)]
    repair_file: Option<String>,

    /// Check health status of recording files
    #[arg(long)]
    check_files: bool,

    /// Output results in JSON format
    #[arg(long)]
    json_output: bool,
```

Update `is_cli_mode` to include new flags:

```rust
fn is_cli_mode(args: &CliArgs) -> bool {
    args.fps.is_some()
        || args.encoder.is_some()
        || args.no_mouse
        || args.window.is_some()
        || args.no_audio
        || args.audio_devices.is_some()
        || args.output.is_some()
        || args.output_dir.is_some()
        || args.duration > 0
        || args.max_size.is_some()
        || args.schedule.is_some()
        || args.list_devices
        || args.webcam
        || args.repair
        || args.repair_file.is_some()
        || args.check_files
}
```

Add repair handling in `run_cli_mode` (or in `main` before WebUI/CLI split):

```rust
    if args.repair {
        let result = engine.repair_all_files();
        if args.json_output {
            println!("{}", serde_json::to_string(&result).unwrap_or_default());
        } else {
            for name in &result.repaired {
                println!("  ✓ {} (repaired)", name);
            }
            for item in &result.failed {
                println!("  ✗ {} — {}", item.name, item.error);
            }
            if !result.healthy.is_empty() {
                println!("  {} healthy files", result.healthy.len());
            }
        }
        std::process::exit(if result.failed.is_empty() { 0 } else { 1 });
    }

    if let Some(ref file) = args.repair_file {
        match engine.repair_file(file) {
            Ok(()) => {
                if args.json_output {
                    println!("{}", serde_json::json!({"ok": true, "name": file}));
                } else {
                    println!("Repaired: {}", file);
                }
                std::process::exit(0);
            }
            Err(e) => {
                if args.json_output {
                    eprintln!("{}", serde_json::json!({"ok": false, "name": file, "error": e}));
                } else {
                    eprintln!("Repair failed: {} — {}", file, e);
                }
                std::process::exit(1);
            }
        }
    }

    if args.check_files {
        let health_map = engine.check_all_files_health();
        if args.json_output {
            println!("{}", serde_json::to_string(&health_map).unwrap_or_default());
        } else if health_map.is_empty() {
            println!("No recording files found");
        } else {
            for (name, health) in &health_map {
                let icon = match health {
                    FileHealth::Healthy => "✓",
                    FileHealth::Fragmented => "⚠",
                    FileHealth::Broken => "✗",
                };
                println!("  {} {} ({:?})", icon, name, health);
            }
        }
        std::process::exit(0);
    }
```

- [ ] **Step 12: Add output_path to EngineStatus**

```rust
struct EngineStatus {
    state: RecordingState,
    recording: bool,
    merging: bool,
    filename: Option<String>,
    elapsed: f64,
    error: Option<String>,
    output_path: Option<String>,
}
```

Update `build_engine_status`:

```rust
fn build_engine_status(inner: &EngineInner) -> EngineStatus {
    let elapsed = if inner.state == RecordingState::Recording {
        inner.recording_start.map(|t| t.elapsed().as_secs_f64()).unwrap_or(0.0)
    } else {
        0.0
    };
    EngineStatus {
        state: inner.state,
        recording: inner.state == RecordingState::Recording,
        merging: inner.state == RecordingState::Merging,
        filename: inner.filename.clone(),
        elapsed,
        error: inner.error_message.clone(),
        output_path: None,
    }
}
```

Note: `output_path` will be set by the API handler since it needs `captures_dir` which is on the engine, not inner.

- [ ] **Step 13: Verify Rust compilation**

Run: `cd /workspace/rust && cargo check 2>&1 | tail -20`
Expected: No compilation errors

- [ ] **Step 14: Commit**

```bash
git add rust/src/main.rs
git commit -m "feat: add fragmented MP4, marker file, repair, and new API/CLI to Rust version"
```

---

### Task 7: Update Rust app.js — File Health UI

**Files:**
- Modify: `rust/assets/app.js`
- Modify: `python/static/js/app.js`

- [ ] **Step 1: Add file health display in loadFiles**

In both `rust/assets/app.js` and `python/static/js/app.js`, in the `loadFiles` method where files are rendered, add health indicator:

```javascript
        const healthIcon = {
            'healthy': '<span class="text-green-400">✓</span>',
            'fragmented': '<span class="text-amber-400">⚠</span>',
            'broken': '<span class="text-red-400">✗</span>',
        };
```

And in the file list rendering, add the health icon next to each file name.

- [ ] **Step 2: Add repair button for broken/fragmented files**

Add a repair button in the file list for non-healthy files:

```javascript
        const repairBtn = (file.health && file.health !== 'healthy')
            ? `<button onclick="App.repairFile('${this.escapeHtml(file.name)}')" class="text-xs text-amber-400 hover:text-amber-300 ml-2">修复</button>`
            : '';
```

- [ ] **Step 3: Add repairFile method**

```javascript
    async repairFile(name) {
        try {
            const res = await fetch(`/api/files/${encodeURIComponent(name)}/repair`, { method: 'POST' });
            const data = await res.json();
            if (data.ok) {
                this.loadFiles();
            } else {
                this.showError(`修复失败: ${data.error}`);
            }
        } catch (err) {
            this.showError(`修复请求失败: ${err}`);
        }
    },
```

- [ ] **Step 4: Commit**

```bash
git add rust/assets/app.js python/static/js/app.js
git commit -m "feat: add file health indicator and repair button in UI"
```

---

### Task 8: Integration Testing

**Files:**
- Test: `python/tests/test_repair.py`
- Test: `python/tests/test_hwaccel.py`

- [ ] **Step 1: Run all Python tests**

Run: `cd /workspace/python && python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 2: Verify Rust compilation**

Run: `cd /workspace/rust && cargo check 2>&1`
Expected: No compilation errors

- [ ] **Step 3: Verify CLI help output**

Run: `cd /workspace/python && python cli.py --help`
Expected: New --repair, --repair-file, --check-files, --json arguments visible

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete real-time disk flush and crash recovery implementation"
```
