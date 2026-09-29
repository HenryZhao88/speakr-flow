"""Build the Apple audio callback bridge for source runs and app bundles."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent
output = root / "build" / "native" / "libvoice_capture.dylib"
output.parent.mkdir(parents=True, exist_ok=True)
subprocess.run([
    "/usr/bin/clang", "-dynamiclib", "-fobjc-arc", "-fblocks", "-O2",
    "-Wall", "-Wextra", "-framework", "AVFAudio", "-framework", "Foundation",
    "-framework", "AVFoundation",
    str(root / "src" / "voice_capture.m"), "-o", str(output),
], check=True)
