"""Native processed input for the built-in microphone's shared call mode."""
import ctypes as C
from pathlib import Path
import sys
import threading

import numpy as np

from .audio_environment import AudioEnvironment
from .diagnostics import logger

Receive = C.CFUNCTYPE(None, C.POINTER(C.c_float), C.c_uint)
_UNCHANGED = object()


class CaptureStats(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "callbacks", "frames", "rejected", "gaps", "slow_callbacks",
        "callback_ns", "max_callback_ns", "first_ns", "last_ns",
    )] + [("expected_sample", C.c_double), ("have_sample", C.c_bool)]


def needs_voice_capture(state):
    return state["builtin"] and state["channels"] > 1 and bool(state["external_inputs"])


def _library():
    root = Path(__file__).resolve().parent.parent
    candidates = [
        Path(sys.executable).resolve().parent.parent / "Resources" / "native" / "libvoice_capture.dylib",
        root / "build" / "native" / "libvoice_capture.dylib",
    ]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        raise RuntimeError("Native microphone library is missing; rebuild SpeakrFlow")
    lib = C.CDLL(str(path))
    lib.sf_voice_create_for_device.argtypes = [Receive, C.c_uint32, C.c_char_p, C.c_uint]
    lib.sf_voice_create_for_device.restype = C.c_void_p
    lib.sf_voice_rate.argtypes = [C.c_void_p]
    lib.sf_voice_rate.restype = C.c_double
    lib.sf_voice_start.argtypes = [C.c_void_p, C.c_char_p, C.c_uint]
    lib.sf_voice_start.restype = C.c_bool
    lib.sf_voice_stats.argtypes = [C.c_void_p, C.POINTER(CaptureStats)]
    lib.sf_voice_stats.restype = None
    for name in ("sf_voice_preferred_mode", "sf_voice_active_mode"):
        getattr(lib, name).argtypes = []
        getattr(lib, name).restype = C.c_long
    lib.sf_voice_show_modes.argtypes = []
    lib.sf_voice_show_modes.restype = None
    for name in ("sf_voice_stop", "sf_voice_destroy"):
        getattr(lib, name).argtypes = [C.c_void_p]
        getattr(lib, name).restype = None
    return lib


def microphone_modes(lib=None):
    lib = lib or _library()
    names = {0: "Standard", 1: "Wide Spectrum", 2: "Voice Isolation"}
    return {"preferred": names.get(lib.sf_voice_preferred_mode(), "Unknown"),
            "active": names.get(lib.sf_voice_active_mode(), "Unknown")}


def show_microphone_modes():
    _library().sf_voice_show_modes()


class VoiceStream:
    def __init__(self, callback, device, released):
        self.lib = _library()
        self.callback = callback
        self.device = device
        self.released = released
        self.active = False
        self.stopped = True
        self.latency = None
        self._receive = Receive(self._received)
        error = C.create_string_buffer(1024)
        self.handle = self.lib.sf_voice_create_for_device(self._receive, device, error, len(error))
        if not self.handle:
            raise RuntimeError(error.value.decode() or "Could not prepare voice input")
        self.samplerate = self.lib.sf_voice_rate(self.handle)
        logger.info("Call microphone modes: %s", microphone_modes(self.lib))

    def _received(self, pointer, frames):
        try:
            samples = np.ctypeslib.as_array(pointer, shape=(frames,))
            audio = np.clip(samples * 32768, -32768, 32767).astype(np.int16).reshape(-1, 1)
            self.callback(audio, frames, None, None)
        except Exception:
            logger.exception("Voice input callback failed")

    def start(self):
        error = C.create_string_buffer(1024)
        if not self.lib.sf_voice_start(self.handle, error, len(error)):
            raise RuntimeError(error.value.decode() or "Could not start voice input")
        self.active, self.stopped = True, False
        logger.info("Recording microphone modes: %s", microphone_modes(self.lib))

    def stop(self):
        self.lib.sf_voice_stop(self.handle)
        stats = CaptureStats()
        self.lib.sf_voice_stats(self.handle, C.byref(stats))
        self.stats = stats
        logger.info("Voice buffers callbacks=%d frames=%d rejected=%d gap_frames=%d "
                    "span_ms=%.1f callback_total_ms=%.1f callback_max_ms=%.1f slow_callbacks=%d",
                    stats.callbacks, stats.frames, stats.rejected, stats.gaps,
                    (stats.last_ns - stats.first_ns) / 1e6, stats.callback_ns / 1e6,
                    stats.max_callback_ns / 1e6, stats.slow_callbacks)
        self.active, self.stopped = False, True

    abort = stop

    def close(self):
        self.released()

    def dispose(self):
        if self.handle:
            self.lib.sf_voice_destroy(self.handle)
            self.handle = None


class CallCapture:
    """Warm on CoreAudio events, capture only between hotkey press and release."""
    def __init__(self, callback, environment=None, stream_factory=VoiceStream):
        self.environment = environment or AudioEnvironment()
        self.callback = callback
        self.stream_factory = stream_factory
        self.lock = threading.RLock()
        self.stream = None
        self.leased = False
        self.identity = None
        self.closed = False
        self.input_uid = None
        self.thread = threading.Thread(target=self._watch, name="CallAudio", daemon=True)

    def start(self):
        self.thread.start()
        self.environment.dirty.set()

    def select_input(self, uid):
        with self.lock:
            self.input_uid = uid
            self.environment.dirty.set()

    def _watch(self):
        while True:
            self.environment.dirty.wait()
            self.environment.dirty.clear()
            with self.lock:
                if self.closed:
                    return
                try:
                    self._refresh()
                except Exception:
                    logger.exception("Could not prepare call microphone")

    def _refresh(self):
        if self.leased:
            return
        try:
            state = self.environment.snapshot(self.input_uid)
        except Exception:
            if self.stream:
                self.stream.dispose()
                self.stream = None
            self.identity = None
            self.environment.subscribe(None)
            raise
        logger.debug("Call input environment: %s", state)
        self.environment.subscribe(state)
        identity = (state["device"], state["external_inputs"])
        if self.stream and identity != self.identity:
            # Remove our own VPIO contribution before reading the new route.
            self.stream.dispose()
            self.stream = None
            state = self.environment.snapshot(self.input_uid)
        self.identity = (state["device"], state["external_inputs"])
        if needs_voice_capture(state):
            if self.stream is None:
                logger.info("Preparing processed call input: %s", state)
                self.stream = self.stream_factory(self.callback, state["device"], self.release)
                logger.info("Processed call input ready rate=%s", self.stream.samplerate)
        elif self.stream:
            self.stream.dispose()
            self.stream = None

    def acquire(self, input_uid=_UNCHANGED):
        with self.lock:
            if input_uid is not _UNCHANGED:
                self.input_uid = input_uid
            self._refresh()
            # Raw recording also owns the route until it closes. Do not prepare
            # a different input halfway through a held hotkey.
            self.leased = True
            return self.stream

    def release(self):
        with self.lock:
            self.leased = False
            self.environment.dirty.set()

    def close(self):
        with self.lock:
            self.closed = True
            if self.stream:
                self.stream.dispose()
                self.stream = None
            self.environment.close()
            self.environment.dirty.set()
