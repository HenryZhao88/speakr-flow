import io
import threading
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000  # whisper expects 16kHz mono
CHANNELS = 1

# Whisper hallucinates "Thank you", "Thanks for watching", etc. on silence.
# Gate on duration and loudness before we even hit the API.
MIN_DURATION_SEC = 0.4
MIN_RMS = 180  # int16 — quiet room is ~50, normal speech is 500+


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
        """Returns (wav_buffer, reason). wav_buffer is None when we skipped;
        reason is a short string explaining why ('too_short', 'silence', or None)."""
        if self.stream is None:
            return None, "no_stream"
        self.stream.stop()
        self.stream.close()
        self.stream = None
        with self.lock:
            if not self.chunks:
                return None, "no_audio"
            audio = np.concatenate(self.chunks, axis=0)

        duration = len(audio) / SAMPLE_RATE
        if duration < MIN_DURATION_SEC:
            return None, "too_short"

        # RMS on int16 samples — promote to float to avoid overflow.
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        if rms < MIN_RMS:
            return None, "silence"

        buf = io.BytesIO()
        wavfile.write(buf, SAMPLE_RATE, audio)
        buf.seek(0)
        return buf, None
