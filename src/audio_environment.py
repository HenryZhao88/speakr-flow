"""Read-only CoreAudio state and notifications, independent of PortAudio's cache."""
import ctypes as C
import os
import threading

from .diagnostics import logger

U32 = C.c_uint32


def fourcc(value):
    return int.from_bytes(value.encode("ascii"), "big")


class Address(C.Structure):
    _fields_ = [("selector", U32), ("scope", U32), ("element", U32)]


class Buffer(C.Structure):
    _fields_ = [("channels", U32), ("size", U32), ("data", C.c_void_p)]


class BufferList(C.Structure):
    _fields_ = [("count", U32), ("buffers", Buffer * 1)]


Listener = C.CFUNCTYPE(C.c_int32, U32, U32, C.POINTER(Address), C.c_void_p)


class AudioEnvironment:
    def __init__(self):
        self.lib = C.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self.lib.AudioObjectGetPropertyDataSize.argtypes = [
            U32, C.POINTER(Address), U32, C.c_void_p, C.POINTER(U32)]
        self.lib.AudioObjectGetPropertyData.argtypes = [
            U32, C.POINTER(Address), U32, C.c_void_p, C.POINTER(U32), C.c_void_p]
        for name in ("AudioObjectAddPropertyListener", "AudioObjectRemovePropertyListener"):
            getattr(self.lib, name).argtypes = [U32, C.POINTER(Address), Listener, C.c_void_p]
        self.dirty = threading.Event()
        self._subscriptions = set()
        self._callback = Listener(self._changed)
        self._notification_lock = threading.Lock()
        self._notification_timer = None
        self._closed = False

    def _changed(self, _object, _count, _addresses, _context):
        # Never initialize/stop audio from CoreAudio's notification thread.
        logger.debug("Audio environment changed object=%s", _object)
        # Stream layout notifications precede publication of the process's
        # active device list. Coalesce each event burst once, after that update;
        # no periodic polling or continuously open diagnostic stream.
        with self._notification_lock:
            if not self._closed and (self._notification_timer is None or
                                     not self._notification_timer.is_alive()):
                self._notification_timer = threading.Timer(.15, self.dirty.set)
                self._notification_timer.daemon = True
                self._notification_timer.start()
        return 0

    def data(self, obj, selector, scope="glob"):
        address = Address(fourcc(selector), fourcc(scope), 0)
        size = U32()
        status = self.lib.AudioObjectGetPropertyDataSize(
            obj, C.byref(address), 0, None, C.byref(size))
        if status:
            raise OSError(status, f"CoreAudio property {selector}")
        result = C.create_string_buffer(size.value)
        status = self.lib.AudioObjectGetPropertyData(
            obj, C.byref(address), 0, None, C.byref(size), result)
        if status:
            raise OSError(status, f"CoreAudio property {selector}")
        return result.raw[:size.value]

    def value(self, obj, selector, kind=U32, scope="glob"):
        return kind.from_buffer_copy(self.data(obj, selector, scope)).value

    def ids(self, obj, selector, scope="glob"):
        data = self.data(obj, selector, scope)
        return tuple((U32 * (len(data) // 4)).from_buffer_copy(data))

    def snapshot(self):
        device = self.value(1, "dIn ")
        layout = self.data(device, "slay", "inpt")
        count = U32.from_buffer_copy(layout).value
        buffers = (Buffer * count).from_buffer_copy(layout, BufferList.buffers.offset)
        processes, active = [], []
        for obj in self.ids(1, "prs#"):
            try:
                if self.value(obj, "ppid") == os.getpid():
                    continue
                processes.append(obj)
                if self.value(obj, "piri") and device in self.ids(obj, "pdv#", "inpt"):
                    active.append(obj)
            except OSError:
                pass  # A process can exit between enumeration and inspection.
        return {
            "device": device,
            "channels": sum(buffer.channels for buffer in buffers),
            "builtin": self.value(device, "tran") == fourcc("bltn"),
            "external_inputs": tuple(sorted(active)),
            "processes": tuple(processes),
        }

    def subscribe(self, state):
        wanted = {(1, "dIn ", "glob"), (1, "prs#", "glob"),
                  (state["device"], "slay", "inpt"),
                  (state["device"], "gone", "glob")}
        wanted.update((obj, "piri", "glob") for obj in state["processes"])
        wanted.update((obj, "pdv#", "inpt") for obj in state["processes"])
        for entry in self._subscriptions - wanted:
            self._listen(entry, False)
        for entry in wanted - self._subscriptions:
            self._listen(entry, True)

    def _listen(self, entry, add):
        obj, selector, scope = entry
        address = Address(fourcc(selector), fourcc(scope), 0)
        method = (self.lib.AudioObjectAddPropertyListener if add
                  else self.lib.AudioObjectRemovePropertyListener)
        status = method(obj, C.byref(address), self._callback, None)
        if add and not status:
            self._subscriptions.add(entry)
        elif not add:
            self._subscriptions.discard(entry)
        elif status:
            logger.debug("CoreAudio listener unavailable: %s status=%s", entry, status)

    def close(self):
        with self._notification_lock:
            self._closed = True
            if self._notification_timer:
                self._notification_timer.cancel()
        for entry in tuple(self._subscriptions):
            self._listen(entry, False)
