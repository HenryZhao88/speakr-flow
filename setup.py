"""Build a native macOS .app bundle.

    pip install py2app
    python setup.py py2app

The result lands in dist/SpeakrFlow.app — drag it to /Applications.
"""
from setuptools import setup

APP = ["run.py"]
DATA_FILES = [("assets", ["assets/menubar_icon.png"])]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": None,
    "plist": {
        "CFBundleName": "SpeakrFlow",
        "CFBundleDisplayName": "SpeakrFlow",
        "CFBundleIdentifier": "com.speakrflow.app",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        # LSUIElement hides the dock icon — this is a menu bar app.
        "LSUIElement": True,
        "NSMicrophoneUsageDescription":
            "SpeakrFlow needs the microphone to transcribe your speech.",
        "NSAppleEventsUsageDescription":
            "SpeakrFlow uses Accessibility to paste transcriptions at your cursor.",
    },
    # _sounddevice_data MUST be a "package" (not zipped) so its bundled
    # libportaudio.dylib stays as a real file dlopen() can load.
    "packages": [
        "rumps", "pynput", "sounddevice", "_sounddevice_data",
        "numpy", "scipy", "requests",
    ],
    "includes": ["src", "src.app"],
}

setup(
    app=APP,
    name="SpeakrFlow",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
