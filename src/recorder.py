import io
import threading
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000  # whisper expects 16kHz mono
CHANNELS = 1


class Recorder:
    """Streams mic audio into a buffer until stop() is called."""

    def __init__(self):
        self.chunks = []
        self.stream = None
        self.lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status):
        with self.lock:
            self.chunks.append(indata.copy())

    def start(self):
        with self.lock:
            self.chunks = []
        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            callback=self._callback,
        )
        self.stream.start()

    def stop(self):
        if self.stream is None:
            return None
        self.stream.stop()
        self.stream.close()
        self.stream = None
        with self.lock:
            if not self.chunks:
                return None
            audio = np.concatenate(self.chunks, axis=0)

        # Return an in-memory wav file ready for upload
        buf = io.BytesIO()
        wavfile.write(buf, SAMPLE_RATE, audio)
        buf.seek(0)
        return buf

    def duration_seconds(self):
        with self.lock:
            total = sum(len(c) for c in self.chunks)
        return total / SAMPLE_RATE
