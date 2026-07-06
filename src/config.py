import json
import os
import threading
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

_LOCK = threading.RLock()
VALID_PROVIDERS = {"groq", "openai"}
DEFAULT_MODELS = {
    "groq": DEFAULTS["model"],
    "openai": "whisper-1",
}


def normalize(data):
    if not isinstance(data, dict):
        data = {}
    cfg = dict(DEFAULTS)
    cfg.update(data)

    if cfg.get("provider") not in VALID_PROVIDERS:
        cfg["provider"] = DEFAULTS["provider"]

    model = str(cfg.get("model", "")).strip()
    other_provider_defaults = {
        default_model
        for provider, default_model in DEFAULT_MODELS.items()
        if provider != cfg["provider"]
    }
    if not model or model in other_provider_defaults:
        model = DEFAULT_MODELS[cfg["provider"]]
    cfg["model"] = model

    cfg["hotkey"] = str(cfg.get("hotkey") or DEFAULTS["hotkey"])
    cfg["language"] = str(cfg.get("language") or "").strip()
    cfg["prompt"] = str(cfg.get("prompt") or "").strip()
    cfg["auto_paste"] = bool(cfg.get("auto_paste"))
    cfg["play_sounds"] = bool(cfg.get("play_sounds"))
    try:
        cfg["history_limit"] = max(1, int(cfg.get("history_limit", 50)))
    except (TypeError, ValueError):
        cfg["history_limit"] = DEFAULTS["history_limit"]

    return cfg


def load():
    with _LOCK:
        if not CONFIG_FILE.exists():
            save(DEFAULTS)
            return dict(DEFAULTS)
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            data = {}
        cfg = normalize(data)
        if cfg != data:
            save(cfg)
        return cfg


def save(cfg):
    with _LOCK:
        normalized = normalize(cfg)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2)
            f.write("\n")
        os.replace(tmp, CONFIG_FILE)
        return normalized
