import io
import threading
import time
import numpy as np
import sounddevice as sd
from scipy.io import wavfile

SAMPLE_RATE = 16000
CHANNELS = 1

# Whisper hallucinates "Thank you", "Thanks for watching", etc. on silence.
# Gate on duration and loudness before we even hit the API.
MIN_DURATION_SEC = 0.4
MIN_OVERALL_RMS = 70     # int16; quiet rooms are often ~30-60.
MIN_FRAME_RMS = 120      # speech should cross this in at least a few frames.
MIN_ACTIVE_SEC = 0.12
MIN_PEAK = 450
FRAME_SEC = 0.03
STARTUP_GRACE_SEC = 0.35


class Recorder:
    """Streams mic audio into a buffer until stop() is called."""

    def __init__(self):
        self.chunks = []
        self.stream = None
        self.sample_rate = SAMPLE_RATE
        self.lock = threading.RLock()
        self._audio_ready = threading.Event()
        self._last_status = None
        self._last_start = None

    def _callback(self, indata, frames, time_info, status):
        with self.lock:
            if status:
                self._last_status = str(status)
            self.chunks.append(indata.copy())
            self._audio_ready.set()

    def _close_stream(self, stream, *, abort=False):
        errors = []
        if stream is None:
            return errors
        try:
            if abort:
                stream.abort()
            else:
                stream.stop()
        except Exception as e:
            errors.append(f"stop failed: {e}")
        try:
            stream.close()
        except Exception as e:
            errors.append(f"close failed: {e}")
        return errors

    def _make_stream(self, sample_rate):
        return sd.InputStream(
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            latency="low",
            callback=self._callback,
        )

    def _start_stream(self):
        """Prefer 16 kHz, but fall back to the system default input rate.

        Some macOS/CoreAudio devices are unreliable immediately after sleep or
        long idle periods when opened at a non-native sample rate. Transcription
        APIs accept normal WAV sample rates, so falling back is safer than
        dropping the recording.
        """
        last_error = None
        for sample_rate in (SAMPLE_RATE, None):
            stream = None
            try:
                stream = self._make_stream(sample_rate)
                stream.start()
                actual_rate = int(stream.samplerate or sample_rate or SAMPLE_RATE)
                return stream, actual_rate
            except Exception as e:
                last_error = e
                self._close_stream(stream, abort=True)
        raise last_error

    def _reset_backend(self):
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if not terminate or not initialize:
            return
        try:
            terminate()
            initialize()
            print("[SpeakrFlow] audio backend reset")
        except Exception as e:
            print(f"[SpeakrFlow] audio backend reset failed: {e}")

    def start(self):
        old_stream = None
        with self.lock:
            old_stream = self.stream
            self.stream = None
            self.chunks = []
            self.sample_rate = SAMPLE_RATE
            self._last_status = None
            self._last_start = time.monotonic()
            self._audio_ready.clear()

        # If a release event was missed, do not let an old CoreAudio stream
        # poison the next recording.
        self._close_stream(old_stream, abort=True)

        stream, sample_rate = self._start_stream()
        with self.lock:
            self.stream = stream
            self.sample_rate = sample_rate

        # Give CoreAudio a short chance to deliver the first buffer after idle.
        # The user can still keep holding the key; audio is appended normally.
        self._audio_ready.wait(STARTUP_GRACE_SEC)

    def stop(self):
        """Returns (wav_buffer, reason). wav_buffer is None when we skipped;
        reason is a short string explaining why ('too_short', 'silence', or None)."""
        with self.lock:
            stream = self.stream
            self.stream = None
            sample_rate = self.sample_rate

        if stream is None:
            return None, "no_stream"

        close_errors = self._close_stream(stream)
        with self.lock:
            if not self.chunks:
                self._reset_backend()
                reason = "no_audio"
                if close_errors:
                    reason += f" ({'; '.join(close_errors)})"
                return None, reason
            audio = np.concatenate(self.chunks, axis=0)
            self.chunks = []
            last_status = self._last_status

        duration = len(audio) / sample_rate
        if duration < MIN_DURATION_SEC:
            return None, "too_short"

        metrics = _speech_metrics(audio, sample_rate)
        print(
            "[SpeakrFlow] captured "
            f"{duration:.2f}s @ {sample_rate}Hz, "
            f"rms={metrics['overall_rms']:.1f}, "
            f"peak={metrics['peak']:.0f}, "
            f"active={metrics['active_sec']:.2f}s"
            + (f", input_status={last_status}" if last_status else "")
        )
        if not _has_speech(metrics):
            return None, "silence"

        buf = io.BytesIO()
        wavfile.write(buf, sample_rate, audio)
        buf.seek(0)
        return buf, None


def _speech_metrics(audio, sample_rate):
    samples = audio.astype(np.float32).reshape(-1)
    if samples.size == 0:
        return {"overall_rms": 0.0, "peak": 0.0, "active_sec": 0.0}

    overall_rms = float(np.sqrt(np.mean(samples ** 2)))
    peak = float(np.max(np.abs(samples)))
    frame_size = max(1, int(sample_rate * FRAME_SEC))
    frame_count = samples.size // frame_size
    if frame_count == 0:
        active_sec = 0.0
    else:
        frames = samples[:frame_count * frame_size].reshape(frame_count, frame_size)
        frame_rms = np.sqrt(np.mean(frames ** 2, axis=1))
        active_sec = float(np.count_nonzero(frame_rms >= MIN_FRAME_RMS) * FRAME_SEC)

    return {
        "overall_rms": overall_rms,
        "peak": peak,
        "active_sec": active_sec,
    }


def _has_speech(metrics):
    if metrics["peak"] < MIN_PEAK:
        return False
    return (
        metrics["overall_rms"] >= MIN_OVERALL_RMS
        or metrics["active_sec"] >= MIN_ACTIVE_SEC
    )
