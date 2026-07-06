import os
import re
import time
import requests

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
REQUEST_TIMEOUT = (10, 90)
MAX_ATTEMPTS = 3
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# Whisper trained on YouTube and falls back to these phrases on near-silence.
# Compare against the normalized transcript (lowercase, punctuation stripped).
HALLUCINATIONS = {
    "thank you", "thank you.", "thank you!",
    "thanks for watching", "thanks for watching!", "thanks for watching.",
    "thanks", "thanks!", "thanks.",
    "bye", "bye!", "bye.", "bye bye", "goodbye",
    "you", "yeah", "yeah.",
    "mm-hmm", "mhm", "uh", "um",
    "subscribe", "please subscribe",
    ".", "...", "",
}


def _is_hallucination(text):
    normalized = re.sub(r"[^\w\s]", "", text).strip().lower()
    return normalized in HALLUCINATIONS or text.strip().lower() in HALLUCINATIONS


class TranscriptionError(Exception):
    pass


def _error_body(response):
    text = response.text.strip()
    if len(text) > 500:
        return text[:497] + "..."
    return text


def _post_with_retries(url, key, files, data):
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        file_obj = files["file"][1]
        try:
            file_obj.seek(0)
        except Exception:
            pass

        try:
            response = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            last_error = e
            if attempt == MAX_ATTEMPTS:
                raise TranscriptionError(f"Network error: {e}") from e
            time.sleep(0.5 * attempt)
            continue

        if response.ok:
            return response

        if response.status_code in TRANSIENT_STATUS_CODES and attempt < MAX_ATTEMPTS:
            time.sleep(0.5 * attempt)
            continue

        raise TranscriptionError(f"{response.status_code}: {_error_body(response)}")

    raise TranscriptionError(f"Network error: {last_error}")


def transcribe(wav_buffer, provider, model, language="", prompt=""):
    if provider == "groq":
        url = GROQ_URL
        key = os.environ.get("GROQ_API_KEY")
        if not key:
            raise TranscriptionError("GROQ_API_KEY not set in .env")
    elif provider == "openai":
        url = OPENAI_URL
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise TranscriptionError("OPENAI_API_KEY not set in .env")
    else:
        raise TranscriptionError(f"Unknown provider: {provider}")

    files = {"file": ("audio.wav", wav_buffer, "audio/wav")}
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    response = _post_with_retries(url, key, files, data)
    try:
        payload = response.json()
    except ValueError as e:
        raise TranscriptionError("Transcription provider returned invalid JSON") from e

    text = str(payload.get("text", "")).strip()
    if _is_hallucination(text):
        return ""
    return text
