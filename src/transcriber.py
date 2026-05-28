import os
import requests

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"


class TranscriptionError(Exception):
    pass


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

    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {key}"},
        files=files,
        data=data,
        timeout=60,
    )
    if not r.ok:
        raise TranscriptionError(f"{r.status_code}: {r.text}")
    return r.json().get("text", "").strip()
