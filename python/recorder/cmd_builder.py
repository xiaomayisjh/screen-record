import os


class CmdBuilder:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.ffmpeg = os.path.join(base_dir, "ffmpeg.exe")
        self.tmp_dir = os.path.join(base_dir, "tmp")
        self.fps = 30
        self.source = "desktop"
        self.encoder = "mpeg4"
        self.hwaccel = None
        self.draw_mouse = 1
        self.enable_webcam = False
        self.aud_list = [None]
        self.audio_delay_ms = 0

    def config(self, fps=None, source=None, encoder=None,
               hwaccel="unchanged", draw_mouse=None,
               webcam=None, aud_list=None, audio_delay_ms=None):
        if fps is not None:
            self.fps = fps
        if source is not None:
            self.source = source
        if encoder is not None:
            self.encoder = encoder
        if hwaccel != "unchanged":
            self.hwaccel = hwaccel
        if draw_mouse is not None:
            self.draw_mouse = 1 if draw_mouse else 0
        if webcam is not None:
            self.enable_webcam = bool(webcam)
        if aud_list is not None:
            self.aud_list = aud_list
        if audio_delay_ms is not None:
            self.audio_delay_ms = audio_delay_ms

    def set_source(self, is_window, window_name=""):
        if not is_window:
            self.source = "desktop"
        else:
            self.source = "title=" + window_name

    def _add_encoder_params(self, cmd):
        cmd.extend(["-c:v", self.encoder])
        
        if self.encoder == "mpeg4":
            cmd.extend(["-q:v", "7"])
        elif self.encoder == "libx264":
            cmd.extend(["-preset", "fast", "-crf", "23"])
        elif self.encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
            cmd.extend(["-preset", "fast"])

    def get_capture_cmd(self, filename):
        cmd = [self.ffmpeg, "-f", "gdigrab"]
        cmd.extend(["-framerate", str(self.fps)])
        cmd.extend(["-draw_mouse", str(self.draw_mouse)])
        cmd.extend(["-i", self.source])
        
        self._add_encoder_params(cmd)
        
        if self.hwaccel:
            cmd.extend(["-hwaccel", self.hwaccel])
        cmd.extend(["-y", filename])
        return cmd

    def get_merge_cmd(self, filename):
        cmd = [self.ffmpeg]
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
        if self.hwaccel:
            cmd.extend(["-hwaccel", self.hwaccel])
        # Copy video stream directly (no re-encoding) unless webcam overlay requires it
        if not self.enable_webcam:
            if self.encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"):
                self._add_encoder_params(cmd)
            else:
                cmd.extend(["-c:v", "copy"])
        cmd.extend(["-shortest"])
        cmd.extend(["-y", filename])
        return cmd
