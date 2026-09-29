"""Opt-in macOS integration check. Opens the mic; saves/uploads no audio.

Another process activates Apple's voice I/O, reproducing the shared microphone
layout used during calls. Check routing, short buffers, and route restoration.
Run after scripts/build_native.py, with no call in progress.
"""
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from Foundation import NSDate, NSRunLoop
from src.recorder import Recorder
from src.voice_capture import CallCapture, VoiceStream


def pump_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Audio state transition timed out")
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.02))


def peer():
    stream = VoiceStream(lambda *args: None, None, lambda: None)
    try:
        stream.start()
        # Parent controls lifetime; exiting the parent closes the pipe.
        sys.stdin.buffer.read(1)
    finally:
        stream.dispose()


def check():
    recorder = Recorder()
    manager = CallCapture(recorder._callback)
    if manager.environment.snapshot()["channels"] != 1:
        raise RuntimeError("Run this check without an active call or multichannel input")
    recorder._call_capture = manager
    manager.start()
    process = subprocess.Popen([sys.executable, __file__, "--peer"], stdin=subprocess.PIPE)
    try:
        pump_until(lambda: manager.stream is not None)
        for duration in (.03, .1, .2):
            recorder.start()
            assert isinstance(recorder.stream, VoiceStream), "Quiet raw input selected during call"
            time.sleep(duration)
            with recorder.lock:
                frames = sum(len(chunk) for chunk in recorder.chunks)
            recorder.stop()
            assert frames > 0, "Short recording produced no buffers"
        process.stdin.close()
        process.wait(timeout=5)
        pump_until(lambda: manager.stream is None)
        recorder.start()
        assert not isinstance(recorder.stream, VoiceStream), "Call route persisted after peer exited"
        recorder.stop()
        assert manager.environment.snapshot()["channels"] == 1
        print("PASS: automatic call route, 30/100/200 ms buffers, normal route restored")
    finally:
        manager.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


if __name__ == "__main__":
    peer() if "--peer" in sys.argv else check()
