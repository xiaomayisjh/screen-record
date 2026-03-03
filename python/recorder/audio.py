import pyaudio
import wave
import threading
import os

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 2
RATE = 44100


class StreamingAudioRecorder:
    """Records audio directly to WAV files on disk, never accumulating in RAM.

    Memory usage: O(CHUNK) constant regardless of recording duration.
    """

    def __init__(self):
        self._stop_event = threading.Event()
        self._thread = None
        self._pa = pyaudio.PyAudio()
        self.devices = [None]
        self.error = False
        self._lock = threading.Lock()

    def record(self, base_filename):
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._stop_event.set()
                self._thread.join(timeout=5)

            self.error = False
            self._stop_event.clear()
            self._base_filename = base_filename
            self._thread = threading.Thread(target=self._record_loop, daemon=True)
            self._thread.start()

    def _record_loop(self):
        streams = []
        wav_files = []

        try:
            base, ext = os.path.splitext(self._base_filename)

            for i, device_id in enumerate(self.devices):
                stream = self._pa.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK,
                    input_device_index=device_id,
                )
                streams.append(stream)

                out_path = f"{base}_{i}{ext}"
                wf = wave.open(out_path, "wb")
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self._pa.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wav_files.append(wf)

            while not self._stop_event.is_set():
                for i in range(len(self.devices)):
                    data = streams[i].read(CHUNK, exception_on_overflow=False)
                    wav_files[i].writeframes(data)

        except Exception as e:
            self.error = True
            print(f"Audio recording error: {e}")

        finally:
            for stream in streams:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            for wf in wav_files:
                try:
                    wf.close()
                except Exception:
                    pass

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def get_device_count(self):
        return self._pa.get_device_count()

    def get_device_name(self, device_id):
        return self._pa.get_device_info_by_index(device_id)["name"]

    def is_input_device(self, device_id):
        return int(self._pa.get_device_info_by_index(device_id)["maxInputChannels"]) > 0

    def get_api_name(self, device_id):
        info = self._pa.get_device_info_by_index(device_id)
        return self._pa.get_host_api_info_by_index(info["hostApi"])["name"]

    def set_to_default(self):
        self.devices = [None]

    def set_to_devices(self, devices):
        self.devices = devices

    def destroy(self):
        self._pa.terminate()
