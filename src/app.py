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


class SpeakrFlowApp(rumps.App):
    def __init__(self):
        super().__init__(
            "SpeakrFlow",
            icon=_icon_path(),
            template=True,
            quit_button=None,  # we add our own at the bottom
        )
        self.cfg = config.load()
        self.recorder = Recorder()
        self.hotkey = None
        self.busy = False

        self._build_menu()
        self._start_hotkey()

    # ---------- menu ----------

    def _build_menu(self):
        self.provider_menu = rumps.MenuItem("Provider")
        for p in PROVIDERS:
            item = rumps.MenuItem(p.capitalize(), callback=self._make_provider_cb(p))
            item.state = 1 if self.cfg["provider"] == p else 0
            self.provider_menu.add(item)

        self.menu = [
            self.provider_menu,
            None,
            # history items get injected here
        ]
        self._render_history_items()
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Settings…", callback=self.open_settings))
        self.menu.add(rumps.MenuItem("Quit SpeakrFlow", callback=rumps.quit_application))

    def _render_history_items(self):
        """Replace the inline history items (positions between Provider and Settings)."""
        # Wipe everything below the first separator and rebuild the bottom.
        keys = list(self.menu.keys())
        # Keep "Provider" + the separator after it; drop the rest.
        keep = {"Provider"}
        for k in keys:
            if k in keep:
                continue
            try:
                del self.menu[k]
            except KeyError:
                pass

        # Re-add the separator after Provider
        self.menu.add(rumps.separator)

        entries = history.load()
        if not entries:
            empty = rumps.MenuItem("(no transcriptions yet)")
            empty.set_callback(None)
            self.menu.add(empty)
        else:
            for entry in entries[:5]:
                preview = entry["text"].replace("\n", " ")
                if len(preview) > 60:
                    preview = preview[:57] + "…"
                self.menu.add(rumps.MenuItem(
                    preview,
                    callback=lambda _, t=entry["text"]: self._copy(t),
                ))

        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Settings…", callback=self.open_settings))
        self.menu.add(rumps.MenuItem("Quit SpeakrFlow", callback=rumps.quit_application))

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

    # ---------- settings window ----------

    def open_settings(self, _):
        settings_window.open_settings(on_save=self._on_settings_saved)

    def _on_settings_saved(self, new_cfg):
        self.cfg = new_cfg
        self._start_hotkey()
        # history limit might have shrunk
        self._render_history_items()

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
                self._render_history_items()
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
