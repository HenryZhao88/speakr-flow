"""Bridge live CoreAudio identities to PortAudio's process-local device indices."""
import ctypes as C

import sounddevice as sd

from .audio_environment import AudioEnvironment


def available_inputs():
    environment = AudioEnvironment()
    try:
        return environment.input_devices()
    finally:
        environment.close()


def refresh_portaudio():
    # Called only with no PortAudio streams open. PortAudio caches device IDs,
    # channels and defaults at initialization, including devices now unplugged.
    sd._terminate()
    sd._initialize()


def candidate_indices(device):
    hosts = sd.query_hostapis()
    return [index for index, info in enumerate(sd.query_devices())
            if info["max_input_channels"] > 0 and info["name"] == device["name"]
            and hosts[info["hostapi"]]["name"] == "Core Audio"]


def stream_device_id(stream):
    # Public PortAudio CoreAudio extension, absent from sounddevice's CFFI
    # declarations. Query the same loaded library and the actual stream pointer.
    lib = C.CDLL(sd._libname)
    get_device = lib.PaMacCore_GetStreamInputDevice
    get_device.argtypes = [C.c_void_p]
    get_device.restype = C.c_uint32
    return get_device(int(sd._ffi.cast("uintptr_t", stream._ptr)))
