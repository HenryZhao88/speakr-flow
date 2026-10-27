import io
import threading
import time
import numpy as np
import sounddevice as sd
from scipy.io import wavfile
from .diagnostics import logger
from . import input_devices

SAMPLE_RATE = 16000
CHANNELS = 1

# Whisper hallucinates "Thank you", "Thanks for watching", etc. on silence.
# Gate on loudness, not recording length: short words are valid input.
MIN_OVERALL_RMS = 70     # int16; quiet rooms are often ~30-60.
MIN_FRAME_RMS = 120      # speech should cross this in at least a few frames.
MIN_ACTIVE_SEC = 0.12
MIN_PEAK = 450
FRAME_SEC = 0.03
STARTUP_GRACE_SEC = 0.35
STOP_GRACE_SEC = 1.0


class Recorder:
    """Streams mic audio into a buffer until stop() is called."""

    def __init__(self, input_device_uid=None):
        self.chunks = []
        self.stream = None
        self.sample_rate = SAMPLE_RATE
        self.lock = threading.RLock()
        self._audio_ready = threading.Event()
        self._last_status = None
        self._last_start = None
        self._stream_control = None
        self._blocked_stream = None
        self._call_capture = None
        self.input_device_uid = input_device_uid

    def prepare_call_capture(self):
        from .voice_capture import CallCapture
        self._call_capture = CallCapture(self._callback)
        self._call_capture.select_input(self.input_device_uid)
        self._call_capture.start()

    def select_input(self, uid):
        self.input_device_uid = uid
        if self._call_capture is not None:
            self._call_capture.select_input(uid)

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
        control = self._stream_control
        if control is not None and control[0] is stream and not stream.stopped:
            _, stop_requested, finished = control
            # A callback-stopped stream can already be inactive while its
            # finished callback is still pending, so check stopped, not active.
            # Let the audio callback stop its own IO thread first. Calling
            # Pa_StopStream while it is active can deadlock CoreAudio's
            # IsRunning listener against AudioOutputUnitStop during calls.
            stop_requested.set()
            if not finished.wait(STOP_GRACE_SEC):
                # Keep ownership and refuse new streams. A direct stop here
                # would re-enter the exact native lock cycle we are avoiding.
                self._blocked_stream = stream
                raise RuntimeError("Microphone did not finish stopping; restart SpeakrFlow")
        try:
            if abort:
                stream.abort()
            else:
                stream.stop()
        except Exception as e:
            errors.append(f"stop failed: {e}")
        try:
            stream.close()
            if self._stream_control is control:
                self._stream_control = None
        except Exception as e:
            errors.append(f"close failed: {e}")
        return errors

    def _make_stream(self, sample_rate, device=None):
        stop_requested = threading.Event()
        finished = threading.Event()

        def callback(indata, frames, time_info, status):
            if stop_requested.is_set():
                raise sd.CallbackAbort
            self._callback(indata, frames, time_info, status)

        stream = sd.InputStream(
            device=device,
            samplerate=sample_rate,
            channels=CHANNELS,
            dtype="int16",
            latency="low",
            callback=callback,
            finished_callback=finished.set,
        )
        self._stream_control = (stream, stop_requested, finished)
        return stream

    def _start_stream(self):
        """Prefer 16 kHz, but fall back to the selected microphone's native rate.

        Some macOS/CoreAudio devices are unreliable immediately after sleep or
        long idle periods when opened at a non-native sample rate. Transcription
        APIs accept normal WAV sample rates, so falling back is safer than
        dropping the recording.
        """
        input_uid = self.input_device_uid
        if self._call_capture is not None:
            stream = self._call_capture.acquire(input_uid)
            if stream is not None:
                try:
                    stream.start()
                    logger.info("Microphone started backend=voice-processing device=%s rate=%s",
                                stream.device, stream.samplerate)
                    return stream, int(stream.samplerate)
                except Exception:
                    stream.stop()
                    stream.close()
                    raise
        try:
            environment = input_devices.AudioEnvironment()
            try:
                selected = environment.resolve_input(input_uid)
            finally:
                environment.close()
            input_devices.refresh_portaudio()
            logger.info("Selected microphone name=%s coreaudio_id=%s follows_default=%s",
                        selected["name"], selected["id"], input_uid is None)
            last_error = RuntimeError("Selected microphone could not be opened. Reconnect it and try again.")
            for device in input_devices.candidate_indices(selected):
                for sample_rate in (SAMPLE_RATE, None):
                    stream = None
                    try:
                        stream = self._make_stream(sample_rate, device=device)
                        actual_device = input_devices.stream_device_id(stream)
                        if actual_device != selected["id"]:
                            self._close_stream(stream, abort=True)
                            break  # Same name, different hardware. Never start it.
                        logger.info("Microphone opened device=%s coreaudio_id=%s rate=%s latency=%s",
                                    stream.device, actual_device, stream.samplerate, stream.latency)
                        stream.start()
                        logger.info("Microphone stream started active=%s", stream.active)
                        return stream, int(stream.samplerate or sample_rate or SAMPLE_RATE)
                    except Exception as e:
                        last_error = e
                        logger.exception("Microphone startup failed requested_rate=%s", sample_rate)
                        self._close_stream(stream, abort=True)
            raise last_error
        except Exception:
            if self._call_capture is not None and self._blocked_stream is None:
                self._call_capture.release()
            raise

    def _reset_backend(self):
        terminate = getattr(sd, "_terminate", None)
        initialize = getattr(sd, "_initialize", None)
        if not terminate or not initialize:
            return
        try:
            terminate()
            initialize()
            logger.info("Audio backend reset")
        except Exception as e:
            logger.warning("Audio backend reset failed: %s", e)

    def start(self):
        if self._blocked_stream is not None:
            raise RuntimeError("Microphone did not finish stopping; restart SpeakrFlow")
        logger.info("Recording requested")
        # Log the backend's view before opening: long-lived CoreAudio device
        # lists can differ from a new process after a call or device change.
        try:
            logger.info("Input device before recording: %s", sd.query_devices(kind="input"))
        except Exception:
            logger.exception("Could not inspect input device")
        # Close before resetting the buffer so a late callback from an older
        # recording cannot contaminate this recording.
        with self.lock:
            old_stream = self.stream
            self.stream = None
        self._close_stream(old_stream, abort=True)
        if old_stream is not None and self._call_capture is not None:
            self._call_capture.release()

        with self.lock:
            self.chunks = []
            self.sample_rate = SAMPLE_RATE
            self._last_status = None
            self._last_start = time.monotonic()
            self._audio_ready.clear()

        stream, sample_rate = self._start_stream()
        with self.lock:
            self.stream = stream
            self.sample_rate = sample_rate

        # Give CoreAudio a short chance to deliver the first buffer after idle.
        # The user can still keep holding the key; audio is appended normally.
        ready = self._audio_ready.wait(STARTUP_GRACE_SEC)
        logger.info("Microphone first buffer received=%s", ready)

    def stop(self):
        """Returns (wav_buffer, reason). wav_buffer is None when we skipped;
        reason is a short string explaining why ('no_audio', 'silence', or None)."""
        logger.info("Recording stop requested")
        with self.lock:
            stream = self.stream
            self.stream = None
            sample_rate = self.sample_rate

        if stream is None:
            return None, "no_stream"

        close_errors = self._close_stream(stream)
        if self._call_capture is not None:
            self._call_capture.release()
        logger.info("Microphone closed errors=%s", close_errors)
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
        metrics = _speech_metrics(audio, sample_rate)
        logger.info(
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
