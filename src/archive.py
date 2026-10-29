"""Permanent archive of every recording and transcript.

Each recording gets the next number: ~/.speakrflow/1.wav with its transcript
in 1.txt, then 2.wav/2.txt, and so on. A recording whose transcription failed
keeps its .wav without a .txt.
"""
import os
import re
import threading
from datetime import datetime

from . import paths

_LOCK = threading.RLock()
_NAME = re.compile(r"^(\d+)\.(wav|txt)$")
_text_cache = {}  # path -> (mtime_ns, size, text)


def _numbered_files():
    """{number: {"wav": Path, "txt": Path}} for files present in the archive."""
    found = {}
    try:
        names = os.listdir(paths.ARCHIVE_DIR)
    except FileNotFoundError:
        return found
    for name in names:
        m = _NAME.match(name)
        if m:
            found.setdefault(int(m.group(1)), {})[m.group(2)] = paths.ARCHIVE_DIR / name
    return found


def _claim(ext):
    """Create the next free N.<ext> exclusively and return (number, fd)."""
    paths.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    number = max(_numbered_files(), default=0) + 1
    while True:
        try:
            fd = os.open(paths.ARCHIVE_DIR / f"{number}.{ext}",
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            return number, fd
        except FileExistsError:
            number += 1


def save_recording(wav_bytes):
    """Write the audio as the next N.wav and return N."""
    with _LOCK:
        number, fd = _claim("wav")
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        return number


def save_transcript(text, number=None):
    """Write N.txt next to recording N (or under a new number) and return N."""
    with _LOCK:
        if number is None:
            number, fd = _claim("txt")
            os.close(fd)
        path = paths.ARCHIVE_DIR / f"{number}.txt"
        tmp = paths.ARCHIVE_DIR / f".{number}.txt.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(text))
            f.write("\n")
        os.replace(tmp, path)
        return number


def _read_text(path):
    try:
        st = path.stat()
        cached = _text_cache.get(path)
        if cached and cached[:2] == (st.st_mtime_ns, st.st_size):
            return cached[2]
        text = path.read_text(encoding="utf-8", errors="replace").rstrip("\n")
    except OSError:
        return None
    _text_cache[path] = (st.st_mtime_ns, st.st_size, text)
    return text


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def list_recordings():
    """Every archived recording, newest first.

    Each entry: {"number", "time" (datetime), "text" (str or None),
    "audio" (Path or None), "transcript" (Path or None)}.
    """
    entries = []
    for number, files in _numbered_files().items():
        audio, transcript = files.get("wav"), files.get("txt")
        times = [t for t in (_mtime(audio) if audio else None,
                             _mtime(transcript) if transcript else None) if t is not None]
        entries.append({
            "number": number,
            "time": datetime.fromtimestamp(min(times)) if times else None,
            "text": _read_text(transcript) if transcript else None,
            "audio": audio,
            "transcript": transcript,
        })
    entries.sort(key=lambda e: e["number"], reverse=True)
    return entries


def search(query, entries=None):
    """Recordings whose transcript contains `query` (case-insensitive), newest
    first, each with "matches": a list of (start, end) offsets into "text".
    A blank query returns every recording with no matches."""
    if entries is None:
        entries = list_recordings()
    query = " ".join(str(query or "").split())
    if not query:
        return [dict(e, matches=[]) for e in entries]
    # Let a single space in the query match any run of whitespace/newlines.
    pattern = re.compile(r"\s+".join(map(re.escape, query.split(" "))), re.IGNORECASE)
    results = []
    for e in entries:
        if not e["text"]:
            continue
        matches = [m.span() for m in pattern.finditer(e["text"])]
        if matches:
            results.append(dict(e, matches=matches))
    return results


def snippet(text, start, end, before=30, after=100):
    """A one-line excerpt around text[start:end]. Context is lopsided so the
    match stays near the front where a truncated row can still show it.

    Returns (excerpt, match_start, match_end) with offsets into the excerpt.
    """
    left = max(0, start - before)
    right = min(len(text), end + after)
    # Snap to word boundaries so the excerpt doesn't begin mid-word.
    if left > 0:
        space = text.find(" ", left, start)
        left = space + 1 if space != -1 else left
    if right < len(text):
        space = text.rfind(" ", end, right)
        right = space if space != -1 else right

    def flat(s):
        return re.sub(r"\s", " ", s)

    prefix = ("…" if left > 0 else "") + flat(text[left:start])
    match = flat(text[start:end])
    suffix = flat(text[end:right]) + ("…" if right < len(text) else "")
    return prefix + match + suffix, len(prefix), len(prefix) + len(match)
