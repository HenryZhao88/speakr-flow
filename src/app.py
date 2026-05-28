import sys
import threading
import traceback
from pathlib import Path

import rumps
import pyperclip
from dotenv import load_dotenv

from . import config, history, paste, settings_window
from .paths import ENV_FILE
from .recorder import Recorder
from .transcriber import transcribe, TranscriptionError
from .hotkey import HoldHotkey


load_dotenv(ENV_FILE)


def _resolve_asset(filename):
    """Find an asset whether running from source or inside the py2app bundle."""
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "assets" / filename,
        Path(sys.executable).resolve().parent.parent / "Resources" / "assets" / filename,
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


PROVIDERS = ["groq", "openai"]


class SpeakrFlowApp(rumps.App):
    def __init__(self):
        self.icon_idle = _resolve_asset("menubar_icon.png")
        self.icon_recording = _resolve_asset("menubar_icon_recording.png")

        super().__init__(
            "SpeakrFlow",
            icon=self.icon_idle,
            template=True,
            quit_button=None,  # type: ignore[arg-type]  # rumps stubs say str but None is valid
        )
        self.cfg = config.load()
        self.recorder = Recorder()
        self.hotkey = None
        self.busy = False

        self._build_menu()
        self._start_hotkey()

    # ---------- icon state ----------

    def _set_recording_icon(self, recording):
        if recording and self.icon_recording:
            self.icon = self.icon_recording
            self.template = False  # render the actual red, don't auto-tint
        else:
            self.icon = self.icon_idle
            self.template = True

    # ---------- menu ----------

    def _build_menu(self):
        # rumps.App auto-creates self.menu as a Menu object; we only ever call
        # .add() on it, never reassign — keeps type checkers from getting confused.
        self.provider_menu = rumps.MenuItem("Provider")
        for p in PROVIDERS:
            item = rumps.MenuItem(p.capitalize(), callback=self._make_provider_cb(p))
            item.state = 1 if self.cfg["provider"] == p else 0
            self.provider_menu.add(item)

        self.menu.add(self.provider_menu)
        self.menu.add(rumps.separator)
        self._add_history_items()
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Settings…", callback=self.open_settings))
        self.menu.add(rumps.MenuItem("Quit SpeakrFlow", callback=rumps.quit_application))

    def _add_history_items(self):
        """Append history items in their current spot in the menu."""
        entries = history.load()
        if not entries:
            empty = rumps.MenuItem("(no transcriptions yet)")
            empty.set_callback(None)
            self.menu.add(empty)
            return
        for entry in entries[:5]:
            preview = entry["text"].replace("\n", " ")
            if len(preview) > 60:
                preview = preview[:57] + "…"
            self.menu.add(rumps.MenuItem(
                preview,
                callback=lambda _, t=entry["text"]: self._copy(t),
            ))

    def _rebuild_menu(self):
        """Wipe and re-add — simplest way to refresh history without
        worrying about which keys to delete."""
        for key in list(self.menu.keys()):
            del self.menu[key]
        self._build_menu()

    def _copy(self, text):
        pyperclip.copy(text)
        rumps.notification("SpeakrFlow", "Copied to clipboard", text[:80])

    def _make_provider_cb(self, name):
        def cb(_):
            self.cfg["provider"] = name
            config.save(self.cfg)
            for item in self.provider_menu.values():
                item.state = 1 if item.title.lower() == name else 0
        return cb

    # ---------- settings ----------

    def open_settings(self, _):
        settings_window.open_settings(on_save=self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.cfg = new_cfg
        self._start_hotkey()
        self._rebuild_menu()

    # ---------- recording ----------

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
            self._set_recording_icon(True)
        except Exception as e:
            self._error(f"Mic failed: {e}")

    def _on_hotkey_up(self):
        if self.busy:
            return
        self.busy = True
        # Drop back to the idle icon as soon as the key is released — the
        # network call shouldn't keep the red mic showing.
        self._set_recording_icon(False)

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
                self._rebuild_menu()
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
