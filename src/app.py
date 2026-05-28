import os
import sys
import threading
import traceback
from pathlib import Path

import rumps
import pyperclip
from dotenv import load_dotenv

from . import config, history, paste
from .paths import ENV_FILE
from .recorder import Recorder
from .transcriber import transcribe, TranscriptionError
from .hotkey import HoldHotkey, available_keys


load_dotenv(ENV_FILE)


# Icon resolves to assets/menubar_icon.png in source, or Resources/assets/...
# inside the py2app bundle.
def _icon_path():
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "assets" / "menubar_icon.png",
        Path(sys.executable).resolve().parent.parent / "Resources" / "assets" / "menubar_icon.png",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


PROVIDERS = ["groq", "openai"]


def _pretty_key(name):
    return name.replace("_", " ").title()


class SpeakrFlowApp(rumps.App):
    def __init__(self):
        super().__init__(
            "SpeakrFlow",
            icon=_icon_path(),
            template=True,             # let macOS auto-invert for dark mode
            quit_button=None,
        )
        self.cfg = config.load()
        self.recorder = Recorder()
        self.hotkey = None
        self.busy = False

        self._build_menu()
        self._start_hotkey()

    # ---------- menu construction ----------

    def _build_menu(self):
        self.status_item = rumps.MenuItem(self._status_text())
        self.status_item.set_callback(None)

        self.provider_menu = rumps.MenuItem("Provider")
        for p in PROVIDERS:
            item = rumps.MenuItem(p.capitalize(), callback=self._make_provider_cb(p))
            item.state = 1 if self.cfg["provider"] == p else 0
            self.provider_menu.add(item)

        self.hotkey_menu = rumps.MenuItem("Hotkey")
        for k in available_keys():
            item = rumps.MenuItem(_pretty_key(k), callback=self._make_hotkey_cb(k))
            item.state = 1 if self.cfg["hotkey"] == k else 0
            self.hotkey_menu.add(item)

        self.model_item = rumps.MenuItem(
            f"Model: {self.cfg['model']}", callback=self.edit_model,
        )
        self.language_item = rumps.MenuItem(
            f"Language: {self.cfg['language'] or 'auto'}", callback=self.edit_language,
        )
        self.prompt_item = rumps.MenuItem(
            "Context prompt…", callback=self.edit_prompt,
        )
        self.limit_item = rumps.MenuItem(
            f"History limit: {self.cfg['history_limit']}", callback=self.edit_history_limit,
        )

        self.autopaste_item = rumps.MenuItem("Auto-paste at cursor", callback=self.toggle_autopaste)
        self.autopaste_item.state = 1 if self.cfg["auto_paste"] else 0

        self.sounds_item = rumps.MenuItem("Play sounds", callback=self.toggle_sounds)
        self.sounds_item.state = 1 if self.cfg["play_sounds"] else 0

        self.history_menu = rumps.MenuItem("History")
        self._fill_history_submenu()

        self.menu = [
            self.status_item,
            None,
            self.provider_menu,
            self.hotkey_menu,
            self.model_item,
            self.language_item,
            self.prompt_item,
            self.limit_item,
            None,
            self.autopaste_item,
            self.sounds_item,
            None,
            self.history_menu,
            None,
            rumps.MenuItem("Reload .env", callback=self.reload_env),
            rumps.MenuItem("Quit SpeakrFlow", callback=rumps.quit_application),
        ]

    def _status_text(self):
        return f"Hold {_pretty_key(self.cfg['hotkey'])} to talk"

    def _fill_history_submenu(self):
        # .clear() blows up on a submenu that's never had children, because
        # rumps hasn't lazily created its NSMenu yet. Guard it.
        try:
            self.history_menu.clear()
        except AttributeError:
            pass
        entries = history.load()
        if not entries:
            empty = rumps.MenuItem("(no transcriptions yet)")
            empty.set_callback(None)
            self.history_menu.add(empty)
        else:
            for entry in entries[:20]:
                preview = entry["text"].replace("\n", " ")
                if len(preview) > 60:
                    preview = preview[:57] + "…"
                self.history_menu.add(rumps.MenuItem(
                    preview,
                    callback=lambda _, text=entry["text"]: self._copy(text),
                ))
        self.history_menu.add(rumps.separator)
        self.history_menu.add(rumps.MenuItem("Clear History", callback=self.clear_history))

    def _copy(self, text):
        pyperclip.copy(text)
        rumps.notification("SpeakrFlow", "Copied to clipboard", text[:80])

    # ---------- setting callbacks ----------

    def _make_provider_cb(self, name):
        def cb(_):
            self.cfg["provider"] = name
            config.save(self.cfg)
            for item in self.provider_menu.values():
                item.state = 1 if item.title.lower() == name else 0
        return cb

    def _make_hotkey_cb(self, name):
        def cb(_):
            self.cfg["hotkey"] = name
            config.save(self.cfg)
            for item in self.hotkey_menu.values():
                item.state = 1 if item.title == _pretty_key(name) else 0
            self.status_item.title = self._status_text()
            self._start_hotkey()
        return cb

    def toggle_autopaste(self, _):
        self.cfg["auto_paste"] = not self.cfg["auto_paste"]
        self.autopaste_item.state = 1 if self.cfg["auto_paste"] else 0
        config.save(self.cfg)

    def toggle_sounds(self, _):
        self.cfg["play_sounds"] = not self.cfg["play_sounds"]
        self.sounds_item.state = 1 if self.cfg["play_sounds"] else 0
        config.save(self.cfg)

    def edit_model(self, _):
        win = rumps.Window(
            title="Whisper model",
            message="Groq: whisper-large-v3-turbo, whisper-large-v3\nOpenAI: whisper-1",
            default_text=self.cfg["model"],
            ok="Save", cancel="Cancel",
            dimensions=(320, 24),
        )
        resp = win.run()
        if resp.clicked and resp.text.strip():
            self.cfg["model"] = resp.text.strip()
            config.save(self.cfg)
            self.model_item.title = f"Model: {self.cfg['model']}"

    def edit_language(self, _):
        win = rumps.Window(
            title="Language",
            message="ISO code like 'en', 'es', 'fr'. Leave blank to auto-detect.",
            default_text=self.cfg["language"],
            ok="Save", cancel="Cancel",
            dimensions=(160, 24),
        )
        resp = win.run()
        if resp.clicked:
            self.cfg["language"] = resp.text.strip()
            config.save(self.cfg)
            self.language_item.title = f"Language: {self.cfg['language'] or 'auto'}"

    def edit_prompt(self, _):
        win = rumps.Window(
            title="Context prompt",
            message="Give Whisper hints about names, jargon, style. Optional.",
            default_text=self.cfg["prompt"],
            ok="Save", cancel="Cancel",
            dimensions=(360, 100),
        )
        resp = win.run()
        if resp.clicked:
            self.cfg["prompt"] = resp.text.strip()
            config.save(self.cfg)

    def edit_history_limit(self, _):
        win = rumps.Window(
            title="History limit",
            message="How many transcriptions to keep.",
            default_text=str(self.cfg["history_limit"]),
            ok="Save", cancel="Cancel",
            dimensions=(80, 24),
        )
        resp = win.run()
        if resp.clicked:
            try:
                self.cfg["history_limit"] = max(1, int(resp.text.strip()))
                config.save(self.cfg)
                self.limit_item.title = f"History limit: {self.cfg['history_limit']}"
            except ValueError:
                rumps.alert("That doesn't look like a number.")

    def clear_history(self, _):
        history.clear()
        self._fill_history_submenu()

    def reload_env(self, _):
        load_dotenv(ENV_FILE, override=True)
        rumps.notification("SpeakrFlow", ".env reloaded", str(ENV_FILE))

    # ---------- hotkey + recording ----------

    def _start_hotkey(self):
        if self.hotkey:
            self.hotkey.stop()
        self.hotkey = HoldHotkey(
            self.cfg["hotkey"],
            on_press=self._on_hotkey_down,
            on_release=self._on_hotkey_up,
        )
        self.hotkey.start()

    def _on_hotkey_down(self):
        if self.busy:
            return
        try:
            self.recorder.start()
        except Exception as e:
            self._error(f"Mic failed: {e}")

    def _on_hotkey_up(self):
        if self.busy:
            return
        self.busy = True

        def worker():
            try:
                wav = self.recorder.stop()
                if wav is None:
                    return
                text = transcribe(
                    wav,
                    provider=self.cfg["provider"],
                    model=self.cfg["model"],
                    language=self.cfg["language"],
                    prompt=self.cfg["prompt"],
                )
                if not text:
                    return
                history.add(text, limit=self.cfg["history_limit"])
                if self.cfg["auto_paste"]:
                    paste.paste_text(text)
                else:
                    pyperclip.copy(text)
                self._fill_history_submenu()
            except TranscriptionError as e:
                self._error(str(e))
            except Exception as e:
                traceback.print_exc()
                self._error(f"Unexpected: {e}")
            finally:
                self.busy = False

        threading.Thread(target=worker, daemon=True).start()

    def _error(self, msg):
        print(f"[SpeakrFlow] {msg}")
        try:
            rumps.notification("SpeakrFlow", "Error", msg)
        except Exception:
            pass


def main():
    SpeakrFlowApp().run()


if __name__ == "__main__":
    main()
