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


def clear():
    with open(HISTORY_FILE, "w") as f:
        json.dump([], f)
