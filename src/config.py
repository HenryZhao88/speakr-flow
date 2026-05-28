import json
from .paths import CONFIG_FILE

DEFAULTS = {
    "provider": "groq",                      # "groq" or "openai"
    "model": "whisper-large-v3-turbo",       # groq default; openai uses "whisper-1"
    "hotkey": "right_option",                # see hotkey.py for valid values
    "auto_paste": True,                      # paste transcription at cursor
    "play_sounds": True,                     # start/stop chimes
    "language": "",                          # blank = auto-detect
    "prompt": "",                            # optional context prompt for Whisper
    "history_limit": 50,
}


def load():
    if not CONFIG_FILE.exists():
        save(DEFAULTS)
        return dict(DEFAULTS)
    with open(CONFIG_FILE) as f:
        data = json.load(f)
    # Backfill any new keys added between versions
    for k, v in DEFAULTS.items():
        data.setdefault(k, v)
    return data


def save(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
