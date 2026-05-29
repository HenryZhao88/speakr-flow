import json
from datetime import datetime
from .paths import HISTORY_FILE


def load():
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return []


def add(text, limit=50):
    entries = load()
    entries.insert(0, {
        "text": text,
        "time": datetime.now().isoformat(timespec="seconds"),
    })
    entries = entries[:limit]
    with open(HISTORY_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    return entries


def save(entries):
    """Overwrite the whole history file with the given list of entries."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(entries, f, indent=2)
    return entries


def update(index, text):
    """Replace the text of the entry at `index` (newest is 0)."""
    entries = load()
    if 0 <= index < len(entries):
        entries[index]["text"] = text
        save(entries)
    return entries


def delete(index):
    """Remove the entry at `index` (newest is 0)."""
    entries = load()
    if 0 <= index < len(entries):
        del entries[index]
        save(entries)
    return entries


def clear():
    with open(HISTORY_FILE, "w") as f:
        json.dump([], f)
