"""Pure formatting helpers for the menu bar status display."""

import time

STATE_LABELS = {
    "idle": "Idle",
    "recording": "Recording…",
    "transcribing": "Transcribing…",
}

_MAX_DETAIL = 60


def transcribing_title(elapsed_sec):
    """Menu bar title while a transcription request is in flight.

    Counts up so a slow API call is visibly still alive rather than frozen.
    """
    if elapsed_sec < 1:
        return "…"
    return f"… {int(elapsed_sec)}s"


def status_line(state):
    return f"Status: {STATE_LABELS.get(state, state)}"


def last_line(ok, detail, timestamp):
    """One-line summary of the most recent transcription attempt."""
    when = time.strftime("%H:%M", time.localtime(timestamp))
    detail = (detail or "").replace("\n", " ").strip()
    if len(detail) > _MAX_DETAIL:
        detail = detail[:_MAX_DETAIL - 1] + "…"
    mark = "✓" if ok else "⚠"
    if detail:
        return f"Last: {mark} {when} — {detail}"
    return f"Last: {mark} {when}"
