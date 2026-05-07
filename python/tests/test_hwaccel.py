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

from recorder.cmd_builder import CmdBuilder, ENCODER_HWACCEL_MAP, FALLBACK_ENCODER, HW_ENCODERS
from recorder.settings_manager import (
    SettingsManager, ENCODER_NAMES, HWACCEL_NAMES, FALLBACK_CHAIN,
    ENCODER_HWACCEL_MAP as SM_ENCODER_HWACCEL_MAP,
)


class TestEncoderHwaccelMap(unittest.TestCase):
    def test_qsv_maps_to_qsv_hwaccel(self):
        self.assertEqual(ENCODER_HWACCEL_MAP["h264_qsv"], "qsv")

    def test_amf_maps_to_d3d11va(self):
        self.assertEqual(ENCODER_HWACCEL_MAP["h264_amf"], "d3d11va")

    def test_nvenc_maps_to_cuda(self):
        self.assertEqual(ENCODER_HWACCEL_MAP["h264_nvenc"], "cuda")

    def test_software_encoders_have_no_hwaccel(self):
        self.assertNotIn("mpeg4", ENCODER_HWACCEL_MAP)
        self.assertNotIn("libx264", ENCODER_HWACCEL_MAP)


class TestFallbackChain(unittest.TestCase):
    def test_qsv_falls_back_to_libx264(self):
        self.assertEqual(FALLBACK_ENCODER["h264_qsv"], "libx264")

    def test_amf_falls_back_to_libx264(self):
        self.assertEqual(FALLBACK_ENCODER["h264_amf"], "libx264")

    def test_nvenc_falls_back_to_libx264(self):
        self.assertEqual(FALLBACK_ENCODER["h264_nvenc"], "libx264")

    def test_libx264_falls_back_to_mpeg4(self):
        self.assertEqual(FALLBACK_ENCODER["libx264"], "mpeg4")

    def test_mpeg4_has_no_fallback(self):
        self.assertNotIn("mpeg4", FALLBACK_ENCODER)

    def test_fallback_chain_order(self):
        self.assertEqual(FALLBACK_CHAIN, ["h264_qsv", "h264_amf", "h264_nvenc", "libx264", "mpeg4"])


class TestCmdBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = CmdBuilder("C:\\app")

    def test_default_encoder_is_mpeg4(self):
        self.assertEqual(self.builder.encoder, "mpeg4")

    def test_default_hwaccel_is_none(self):
        self.assertIsNone(self.builder.hwaccel)

    def test_config_sets_encoder_and_hwaccel_qsv(self):
        self.builder.config(encoder="h264_qsv")
        self.assertEqual(self.builder.encoder, "h264_qsv")
        self.assertEqual(self.builder.hwaccel, "qsv")

    def test_config_sets_encoder_and_hwaccel_amf(self):
        self.builder.config(encoder="h264_amf")
        self.assertEqual(self.builder.encoder, "h264_amf")
        self.assertEqual(self.builder.hwaccel, "d3d11va")

    def test_config_sets_encoder_and_hwaccel_nvenc(self):
        self.builder.config(encoder="h264_nvenc")
        self.assertEqual(self.builder.encoder, "h264_nvenc")
        self.assertEqual(self.builder.hwaccel, "cuda")

    def test_config_software_encoder_no_hwaccel(self):
        self.builder.config(encoder="libx264")
        self.assertEqual(self.builder.encoder, "libx264")
        self.assertIsNone(self.builder.hwaccel)

    def test_config_mpeg4_no_hwaccel(self):
        self.builder.config(encoder="mpeg4")
        self.assertEqual(self.builder.encoder, "mpeg4")
        self.assertIsNone(self.builder.hwaccel)

    def test_capture_cmd_hwaccel_before_input(self):
        self.builder.config(encoder="h264_qsv")
        cmd = self.builder.get_capture_cmd("out.mkv")
        hwaccel_idx = cmd.index("-hwaccel")
        input_idx = cmd.index("-i")
        self.assertLess(hwaccel_idx, input_idx, "-hwaccel must come before -i in FFmpeg")

    def test_capture_cmd_qsv_includes_output_format(self):
        self.builder.config(encoder="h264_qsv")
        cmd = self.builder.get_capture_cmd("out.mkv")
        self.assertIn("-hwaccel_output_format", cmd)
        qsv_out_idx = cmd.index("-hwaccel_output_format")
        self.assertEqual(cmd[qsv_out_idx + 1], "qsv")

    def test_capture_cmd_no_hwaccel_for_software(self):
        self.builder.config(encoder="mpeg4")
        cmd = self.builder.get_capture_cmd("out.mkv")
        self.assertNotIn("-hwaccel", cmd)

    def test_capture_cmd_structure_qsv(self):
        self.builder.config(encoder="h264_qsv", fps=60, draw_mouse=True)
        cmd = self.builder.get_capture_cmd("test.mkv")
        self.assertEqual(cmd[0], self.builder.ffmpeg)
        self.assertIn("-hwaccel", cmd)
        self.assertIn("qsv", cmd)
        self.assertIn("-f", cmd)
        self.assertIn("gdigrab", cmd)
        self.assertIn("-framerate", cmd)
        self.assertIn("60", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("h264_qsv", cmd)
        self.assertIn("test.mkv", cmd)

    def test_capture_cmd_structure_amf(self):
        self.builder.config(encoder="h264_amf")
        cmd = self.builder.get_capture_cmd("test.mkv")
        self.assertIn("-hwaccel", cmd)
        self.assertIn("d3d11va", cmd)
        self.assertIn("-c:v", cmd)
        self.assertIn("h264_amf", cmd)

    def test_merge_cmd_hwaccel_before_input(self):
        self.builder.config(encoder="h264_qsv")
        self.builder.aud_list = []
        cmd = self.builder.get_merge_cmd("out.mp4")
        hwaccel_idx = cmd.index("-hwaccel")
        input_idx = cmd.index("-i")
        self.assertLess(hwaccel_idx, input_idx)

    def test_get_fallback_encoder_qsv(self):
        self.builder.config(encoder="h264_qsv")
        self.assertEqual(self.builder.get_fallback_encoder(), "libx264")

    def test_get_fallback_encoder_amf(self):
        self.builder.config(encoder="h264_amf")
        self.assertEqual(self.builder.get_fallback_encoder(), "libx264")

    def test_get_fallback_encoder_mpeg4(self):
        self.builder.config(encoder="mpeg4")
        self.assertIsNone(self.builder.get_fallback_encoder())

    def test_is_hardware_encoder(self):
        self.builder.config(encoder="h264_qsv")
        self.assertTrue(self.builder.is_hardware_encoder())
        self.builder.config(encoder="mpeg4")
        self.assertFalse(self.builder.is_hardware_encoder())
        self.builder.config(encoder="libx264")
        self.assertFalse(self.builder.is_hardware_encoder())

    def test_encoder_params_qsv(self):
        self.builder.config(encoder="h264_qsv")
        cmd = self.builder.get_capture_cmd("out.mkv")
        cv_idx = cmd.index("-c:v")
        self.assertEqual(cmd[cv_idx + 1], "h264_qsv")
        self.assertIn("-preset", cmd)

    def test_encoder_params_libx264(self):
        self.builder.config(encoder="libx264")
        cmd = self.builder.get_capture_cmd("out.mkv")
        cv_idx = cmd.index("-c:v")
        self.assertEqual(cmd[cv_idx + 1], "libx264")
        self.assertIn("-preset", cmd)
        self.assertIn("-crf", cmd)

    def test_encoder_params_mpeg4(self):
        self.builder.config(encoder="mpeg4")
        cmd = self.builder.get_capture_cmd("out.mkv")
        cv_idx = cmd.index("-c:v")
        self.assertEqual(cmd[cv_idx + 1], "mpeg4")
        self.assertIn("-q:v", cmd)

    def test_explicit_hwaccel_override(self):
        self.builder.config(encoder="mpeg4", hwaccel="d3d11va")
        self.assertEqual(self.builder.hwaccel, "d3d11va")

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

    def test_merge_cmd_includes_faststart(self):
        self.builder.config(encoder="mpeg4")
        self.builder.aud_list = []
        cmd = self.builder.get_merge_cmd("out.mp4")
        self.assertIn("-movflags", cmd)
        movflags_idx = cmd.index("-movflags")
        self.assertIn("+faststart", cmd[movflags_idx + 1])

    def test_hwaccel_unchanged_preserves_auto(self):
        self.builder.config(encoder="h264_qsv")
        self.assertEqual(self.builder.hwaccel, "qsv")
        self.builder.config(fps=60)
        self.assertEqual(self.builder.hwaccel, "qsv")


class TestSettingsManagerHwaccel(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "test_tmp")
        os.makedirs(self.temp_dir, exist_ok=True)
        ffmpeg_path = os.path.join(self.temp_dir, "ffmpeg.exe")
        with open(ffmpeg_path, "w") as f:
            f.write("fake")

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_detect_hwaccels_qsv_available(self, mock_run):
        mock_run.return_value = "Hardware acceleration methods:\nqsv\nd3d11va\ndxva2\ncuda\n"
        sm = SettingsManager(self.temp_dir)
        result = sm.detect_available_hwaccels()
        self.assertIn("qsv", result)
        self.assertIn("d3d11va", result)
        self.assertIn("cuda", result)

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_detect_hwaccels_empty(self, mock_run):
        mock_run.return_value = ""
        sm = SettingsManager(self.temp_dir)
        result = sm.detect_available_hwaccels()
        self.assertEqual(result, [])

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_detect_hwaccels_only_qsv(self, mock_run):
        mock_run.return_value = "Hardware acceleration methods:\nqsv\n"
        sm = SettingsManager(self.temp_dir)
        result = sm.detect_available_hwaccels()
        self.assertEqual(result, ["qsv"])

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_encoder_hwaccel_available(self, mock_run):
        mock_run.return_value = "Hardware acceleration methods:\nqsv\nd3d11va\n"
        sm = SettingsManager(self.temp_dir)
        result = sm.get_encoder_hwaccel("h264_qsv")
        self.assertEqual(result, "qsv")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_encoder_hwaccel_not_available(self, mock_run):
        mock_run.return_value = "Hardware acceleration methods:\ndxva2\n"
        sm = SettingsManager(self.temp_dir)
        result = sm.get_encoder_hwaccel("h264_qsv")
        self.assertIsNone(result)

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_encoder_hwaccel_software_encoder(self, mock_run):
        sm = SettingsManager(self.temp_dir)
        result = sm.get_encoder_hwaccel("mpeg4")
        self.assertIsNone(result)

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_best_encoder_prefers_hw_with_hwaccel(self, mock_run):
        def side_effect(args):
            if args == ["-encoders"]:
                return "h264_qsv h264_amf h264_nvenc libx264 mpeg4"
            if args == ["-hwaccels"]:
                return "Hardware acceleration methods:\nqsv\n"
            return ""
        mock_run.side_effect = side_effect
        sm = SettingsManager(self.temp_dir)
        result = sm.get_best_encoder()
        self.assertEqual(result, "h264_qsv")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_best_encoder_falls_back_when_no_hwaccel(self, mock_run):
        def side_effect(args):
            if args == ["-encoders"]:
                return "h264_qsv h264_amf h264_nvenc libx264 mpeg4"
            if args == ["-hwaccels"]:
                return "Hardware acceleration methods:\n"
            return ""
        mock_run.side_effect = side_effect
        sm = SettingsManager(self.temp_dir)
        result = sm.get_best_encoder()
        self.assertEqual(result, "libx264")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_best_encoder_amd_preference(self, mock_run):
        def side_effect(args):
            if args == ["-encoders"]:
                return "h264_amf libx264 mpeg4"
            if args == ["-hwaccels"]:
                return "Hardware acceleration methods:\nd3d11va\n"
            return ""
        mock_run.side_effect = side_effect
        sm = SettingsManager(self.temp_dir)
        result = sm.get_best_encoder()
        self.assertEqual(result, "h264_amf")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_fallback_encoder(self, mock_run):
        mock_run.return_value = "libx264 mpeg4"
        sm = SettingsManager(self.temp_dir)
        result = sm.get_fallback_encoder("h264_qsv")
        self.assertEqual(result, "libx264")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_fallback_encoder_to_mpeg4(self, mock_run):
        mock_run.return_value = "mpeg4"
        sm = SettingsManager(self.temp_dir)
        result = sm.get_fallback_encoder("libx264")
        self.assertEqual(result, "mpeg4")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_get_fallback_encoder_mpeg4_returns_mpeg4(self, mock_run):
        sm = SettingsManager(self.temp_dir)
        result = sm.get_fallback_encoder("mpeg4")
        self.assertEqual(result, "mpeg4")

    @patch.object(SettingsManager, '_run_ffmpeg')
    def test_hwaccel_cache(self, mock_run):
        mock_run.return_value = "Hardware acceleration methods:\nqsv\n"
        sm = SettingsManager(self.temp_dir)
        sm.detect_available_hwaccels()
        sm.detect_available_hwaccels()
        self.assertEqual(mock_run.call_count, 1)


class TestEngineFallback(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(os.path.dirname(__file__), "test_engine_tmp")
        os.makedirs(self.temp_dir, exist_ok=True)
        ffmpeg_path = os.path.join(self.temp_dir, "ffmpeg.exe")
        with open(ffmpeg_path, "w") as f:
            f.write("fake")

    def tearDown(self):
        import shutil
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_engine(self, mock_settings):
        import subprocess
        if not hasattr(subprocess, 'STARTUPINFO'):
            subprocess.STARTUPINFO = type('STARTUPINFO', (), {'dwFlags': 0, 'wShowWindow': 0})
            subprocess.STARTF_USESHOWWINDOW = 1
            subprocess.SW_HIDE = 0
        from recorder.engine import RecordingEngine
        engine = RecordingEngine.__new__(RecordingEngine)
        engine.base_dir = self.temp_dir
        engine.captures_dir = os.path.join(self.temp_dir, "ScreenCaptures")
        engine.tmp_dir = os.path.join(self.temp_dir, "tmp")
        engine.settings = mock_settings
        engine.cmd_builder = CmdBuilder(self.temp_dir)
        engine._state = "idle"
        engine._lock = __import__('threading').Lock()
        engine._state_condition = __import__('threading').Condition(engine._lock)
        engine._state_version = 0
        engine._video_proc = None
        engine._webcam_capturer = None
        engine._merge_proc = None
        engine._recording_start = None
        engine._audio_start = None
        engine._filename = None
        engine._error_message = None
        engine._stderr_file = None
        engine._fallback_count = 0
        engine._original_encoder = None
        engine._current_encoder = None
        engine._current_hwaccel = None
        engine._fallback_info = None
        return engine

    def test_attempt_fallback_from_qsv(self):
        mock_settings = MagicMock()
        mock_settings.get_fallback_encoder.return_value = "libx264"
        engine = self._make_engine(mock_settings)
        engine._current_encoder = "h264_qsv"
        engine._original_encoder = "h264_qsv"
        engine._fallback_count = 0

        result = engine._attempt_fallback()
        self.assertTrue(result)
        self.assertEqual(engine._current_encoder, "libx264")
        self.assertIsNotNone(engine._fallback_info)
        self.assertEqual(engine._fallback_info["original_encoder"], "h264_qsv")
        self.assertEqual(engine._fallback_info["current_encoder"], "libx264")
        self.assertEqual(engine._fallback_info["fallback_from"], "h264_qsv")

    def test_attempt_fallback_max_retries(self):
        from recorder.engine import MAX_FALLBACK_RETRIES
        mock_settings = MagicMock()
        engine = self._make_engine(mock_settings)
        engine._current_encoder = "h264_qsv"
        engine._original_encoder = "h264_qsv"
        engine._fallback_count = MAX_FALLBACK_RETRIES

        result = engine._attempt_fallback()
        self.assertFalse(result)

    def test_attempt_fallback_software_encoder_no_fallback(self):
        mock_settings = MagicMock()
        mock_settings.get_fallback_encoder.return_value = "mpeg4"
        engine = self._make_engine(mock_settings)
        engine._current_encoder = "mpeg4"
        engine._original_encoder = "mpeg4"
        engine._fallback_count = 0

        result = engine._attempt_fallback()
        self.assertFalse(result)

    def test_state_includes_encoder_and_hwaccel(self):
        mock_settings = MagicMock()
        engine = self._make_engine(mock_settings)
        engine._current_encoder = "h264_qsv"
        engine._current_hwaccel = "qsv"
        engine._fallback_info = None

        state = engine.get_state()
        self.assertEqual(state["encoder"], "h264_qsv")
        self.assertEqual(state["hwaccel"], "qsv")
        self.assertIsNone(state["fallback"])

    def test_state_includes_fallback_info(self):
        mock_settings = MagicMock()
        engine = self._make_engine(mock_settings)
        engine._current_encoder = "libx264"
        engine._current_hwaccel = None
        engine._fallback_info = {
            "original_encoder": "h264_qsv",
            "current_encoder": "libx264",
            "fallback_from": "h264_qsv",
            "fallback_count": 1,
            "message": "Intel QuickSync failed, fallback to H.264 (CPU)",
        }

        state = engine.get_state()
        self.assertIsNotNone(state["fallback"])
        self.assertEqual(state["fallback"]["original_encoder"], "h264_qsv")
        self.assertEqual(state["fallback"]["current_encoder"], "libx264")


class TestConsistencyBetweenModules(unittest.TestCase):
    def test_hwaccel_map_consistency(self):
        for key in ENCODER_HWACCEL_MAP:
            self.assertIn(key, SM_ENCODER_HWACCEL_MAP,
                          f"ENCODER_HWACCEL_MAP key {key} missing from settings_manager")
        for key in SM_ENCODER_HWACCEL_MAP:
            self.assertIn(key, ENCODER_HWACCEL_MAP,
                          f"settings_manager ENCODER_HWACCEL_MAP key {key} missing from cmd_builder")

    def test_hw_encoders_set_consistency(self):
        self.assertEqual(HW_ENCODERS, set(ENCODER_HWACCEL_MAP.keys()))

    def test_all_hw_encoders_have_names(self):
        for enc in HW_ENCODERS:
            self.assertIn(enc, ENCODER_NAMES)

    def test_all_hwaccels_have_names(self):
        for hwaccel in ENCODER_HWACCEL_MAP.values():
            self.assertIn(hwaccel, HWACCEL_NAMES)

    def test_all_hw_encoders_have_fallback(self):
        for enc in HW_ENCODERS:
            self.assertIn(enc, FALLBACK_ENCODER)


if __name__ == "__main__":
    unittest.main()
