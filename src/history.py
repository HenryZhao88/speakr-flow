import json
import os
import threading
from datetime import datetime
from .paths import HISTORY_FILE

_LOCK = threading.RLock()


def _coerce_entries(data):
    if not isinstance(data, list):
        return []
    entries = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        if text is None:
            continue
        entries.append({
            "text": str(text),
            "time": str(entry.get("time", "")),
        })
    return entries


def _coerce_limit(limit):
    try:
        return max(1, int(limit))
    except (TypeError, ValueError):
        return 50


def _atomic_write(entries):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(HISTORY_FILE.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")
    os.replace(tmp, HISTORY_FILE)


def load():
    with _LOCK:
        if not HISTORY_FILE.exists():
            return []
        try:
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return _coerce_entries(json.load(f))
        except (OSError, json.JSONDecodeError, TypeError):
            return []


def add(text, limit=50):
    with _LOCK:
        entries = load()
        entries.insert(0, {
            "text": str(text),
            "time": datetime.now().isoformat(timespec="seconds"),
        })
        entries = entries[:_coerce_limit(limit)]
        _atomic_write(entries)
        return entries


def save(entries):
    """Overwrite the whole history file with the given list of entries."""
    with _LOCK:
        coerced = _coerce_entries(entries)
        _atomic_write(coerced)
        return coerced


def update(index, text):
    """Replace the text of the entry at `index` (newest is 0)."""
    with _LOCK:
        entries = load()
        if 0 <= index < len(entries):
            entries[index]["text"] = str(text)
            save(entries)
        return entries


def delete(index):
    """Remove the entry at `index` (newest is 0)."""
    with _LOCK:
        entries = load()
        if 0 <= index < len(entries):
            del entries[index]
            save(entries)
        return entries


def clear():
    with _LOCK:
        _atomic_write([])
