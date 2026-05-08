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

from recorder.repair import FileHealthChecker, RepairResult, FileHealth, write_recording_marker, read_recording_marker, delete_recording_marker


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
        with open(test_file, "wb") as f:
            f.write(b'\x00\x00\x00\x20xxxx')
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
        with open(test_file, "wb") as f:
            f.write(b'\x00\x00\x00\x20ftypisom')
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
        write_recording_marker(self.temp_dir, "test.mp4", "h264_qsv", "qsv")
        info = read_recording_marker(self.temp_dir)
        self.assertIsNotNone(info)
        self.assertEqual(info["filename"], "test.mp4")
        self.assertEqual(info["encoder"], "h264_qsv")

    def test_read_marker_no_file(self):
        info = read_recording_marker(self.temp_dir)
        self.assertIsNone(info)

    def test_delete_marker(self):
        write_recording_marker(self.temp_dir, "test.mp4", "mpeg4", None)
        delete_recording_marker(self.temp_dir)
        info = read_recording_marker(self.temp_dir)
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
