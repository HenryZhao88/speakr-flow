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
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.cf.CFStringGetLength.argtypes = [C.c_void_p]
        self.cf.CFStringGetLength.restype = C.c_long
        self.cf.CFStringGetCString.argtypes = [C.c_void_p, C.c_void_p, C.c_long, U32]
        self.cf.CFStringGetCString.restype = C.c_bool
        self.cf.CFRelease.argtypes = [C.c_void_p]
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

    def string(self, obj, selector):
        value = self.value(obj, selector, C.c_void_p)
        if not value:
            return ""
        try:
            text = C.create_string_buffer(self.cf.CFStringGetLength(value) * 4 + 1)
            if not self.cf.CFStringGetCString(value, text, len(text), 0x08000100):
                raise ValueError("Could not read microphone name")
            return text.value.decode("utf-8")
        finally:
            self.cf.CFRelease(value)

    def input_channels(self, device):
        layout = self.data(device, "slay", "inpt")
        count = U32.from_buffer_copy(layout).value
        if not count:
            return 0
        buffers = (Buffer * count).from_buffer_copy(layout, BufferList.buffers.offset)
        return sum(buffer.channels for buffer in buffers)

    def input_devices(self):
        devices = []
        for device in self.ids(1, "dev#"):
            try:
                channels = self.input_channels(device)
                if channels:
                    devices.append({"id": device, "uid": self.string(device, "uid "),
                                    "name": self.string(device, "lnam"), "channels": channels})
            except OSError:
                continue  # A device can disconnect while enumerating.
        return devices

    def resolve_input(self, uid=None):
        default = self.value(1, "dIn ") if uid is None else None
        for device in self.input_devices():
            if (device["uid"] == uid if uid is not None else device["id"] == default):
                return device
        if uid is not None:
            raise RuntimeError("Selected microphone is unavailable. Reconnect it or choose another mic in Settings.")
        raise RuntimeError("Mac default microphone is unavailable. Choose an input in macOS Sound settings.")

    def snapshot(self, input_uid=None):
        selected = self.resolve_input(input_uid)
        device = selected["id"]
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
            "channels": selected["channels"],
            "builtin": self.value(device, "tran") == fourcc("bltn"),
            "external_inputs": tuple(sorted(active)),
            "processes": tuple(processes),
        }

    def subscribe(self, state):
        wanted = {(1, "dIn ", "glob"), (1, "prs#", "glob"), (1, "dev#", "glob")}
        if state is not None:
            wanted.update({(state["device"], "slay", "inpt"),
                           (state["device"], "gone", "glob")})
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
