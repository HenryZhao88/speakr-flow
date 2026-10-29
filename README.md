# SpeakrFlow

A free, local Wispr Flow clone for macOS. Hold a key, talk, release — it transcribes and pastes wherever your cursor is. Lives in the menu bar.

## How it works
- Hold the configured key (default: **Right Option**) to record
- Release to transcribe (Groq's `whisper-large-v3-turbo` by default — fast and basically free)
- There is no minimum recording length; silence is skipped.
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
python scripts/build_native.py
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
- **"… Ns" next to the icon** — finishing the recording or waiting for transcription, counting seconds

Click the 🎙 icon for:
- **Status** line (Idle / Recording… / Finishing recording… / Transcribing…) and the result of the last attempt (✓/⚠ with time and details)
- Recent transcriptions (click any to copy)
- Clear history
- **Search Transcripts…** — search everything you've ever dictated (also in Settings)
- **Open Settings…** — provider, model, hotkey, language, prompt, auto-paste, etc.
- Reload `.env`
- Quit

## Choosing a microphone

Open **Settings… → Microphone**. **Mac default** follows the current macOS
input device. Or choose a specific microphone, such as **MacBook Pro Microphone**,
your iPhone, or AirPods, then click **Save**. SpeakrFlow remembers that choice
across restarts and uses it for the next recording, independently of your audio
output. You can listen through AirPods while recording from your Mac or iPhone.

The list refreshes when you open it; **Refresh** also picks up newly connected
devices. An iPhone must be available through
[Continuity Camera](https://support.apple.com/en-us/102546) to appear.
If a chosen mic disconnects, it stays selected and is marked **unavailable**;
reconnect it or choose another mic. SpeakrFlow reports the missing microphone
instead of silently recording from a different one. System sound settings are
never changed.

## Providers

Default: **Groq** with `whisper-large-v3-turbo`. Sign up at https://console.groq.com — has a generous free tier.

Alt: **OpenAI** with `whisper-1`. Switch in Settings.

## Searching transcripts

**Search Transcripts…** (menu bar, or the button in Settings) searches every
transcript in `~/.speakrflow`. Results are grouped by recording, with one row per
place the phrase was said. Pick a row to see the full transcript with every
match highlighted. **Play** replays the audio, and **Show in Finder** reveals the
file. With the search box empty, the window lists all recordings, newest first.
Press ↓ or Return in the search box to jump into the results.

## Config + data

User data is stored in `~/Library/Application Support/SpeakrFlow/`:
- `config.json` — settings
- `history.json` — transcription history

Every recording is also archived in `~/.speakrflow/`, numbered in order:
`1.wav` with its transcript in `1.txt`, then `2.wav`/`2.txt`, and so on. If a
transcription fails, its `.wav` is kept without a `.txt`.

API keys live in `.env` (gitignored).

Diagnostics are saved to `~/Library/Logs/SpeakrFlow/app.log` (rotated automatically).
They include startup identity, recording failures, and API response codes, without
logging API keys, audio, or transcript text. macOS crash reports are in
`~/Library/Logs/DiagnosticReports/`.

When a call exposes the built-in mic as multiple quiet channels, SpeakrFlow uses
Apple's voice-processing input. It prepares that input on audio-device events,
records only while the hotkey is held, and returns to ordinary input after the
call. External microphones retain the existing capture path. No macOS preferences
are changed. The native callback bridge is compiled automatically by the app build
(source runs need the extra build step above and Apple's command-line tools).
