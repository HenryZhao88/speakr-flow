# SpeakrFlow

A free, local Wispr Flow clone for macOS. Hold a key, talk, release — it transcribes and pastes wherever your cursor is. Lives in the menu bar.

## How it works
- Hold the configured key (default: **Right Option**) to record
- Release to transcribe (Groq's `whisper-large-v3-turbo` by default — fast and basically free)
- The result is pasted at your cursor and saved to history

## Setup

```bash
git clone <your-fork>
cd speakr-flow
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Open .env and paste your key from https://console.groq.com/keys
```

## Run

From source (dev):
```bash
python run.py
```

As a real `.app` you can drop in `/Applications`:
```bash
pip install py2app
python setup.py py2app
open dist/SpeakrFlow.app
# then drag dist/SpeakrFlow.app to /Applications
```

To start it at login: System Settings → General → Login Items → add `SpeakrFlow.app`.

## macOS permissions

The first time it runs, macOS will ask you to grant:
- **Microphone** — to record audio
- **Accessibility** — so the global hotkey works
- **Input Monitoring** — same reason

If hotkeys silently stop working, it's almost always one of these toggles. System Settings → Privacy & Security.

The app re-enables its key listener automatically when macOS disables it (which happens after sleep/wake or heavy throttling), so the hotkey should survive sleep. If it ever still dies, check the Status line in the menu and the toggles above.

## Menu bar

The icon shows what the app is doing:
- **Plain mic** — idle, waiting for the hotkey
- **Red mic** — recording
- **"… Ns" next to the icon** — transcription request in flight, counting seconds (so a slow API call is visibly alive, not frozen)

Click the 🎙 icon for:
- **Status** line (Idle / Recording… / Transcribing…) and the result of the last attempt (✓/⚠ with time and details)
- Recent transcriptions (click any to copy)
- Clear history
- **Open Settings…** — provider, model, hotkey, language, prompt, auto-paste, etc.
- Reload `.env`
- Quit

## Providers

Default: **Groq** with `whisper-large-v3-turbo`. Sign up at https://console.groq.com — has a generous free tier.

Alt: **OpenAI** with `whisper-1`. Switch in Settings.

## Config + data

User data is stored in `~/Library/Application Support/SpeakrFlow/`:
- `config.json` — settings
- `history.json` — transcription history

API keys live in `.env` (gitignored).
