import os
import sys
import subprocess
import threading
import traceback

import rumps
import pyperclip
from dotenv import load_dotenv

from . import config, history, paste
from .paths import ENV_FILE, PROJECT_ROOT
from .recorder import Recorder
from .transcriber import transcribe, TranscriptionError
from .hotkey import HoldHotkey


load_dotenv(ENV_FILE)


IDLE_TITLE = "🎙"
RECORDING_TITLE = "🔴"
WORKING_TITLE = "…"


class SpeakrFlowApp(rumps.App):
    def __init__(self):
        super().__init__("SpeakrFlow", title=IDLE_TITLE, quit_button=None)
        self.cfg = config.load()
        self.recorder = Recorder()
        self.hotkey = None
        self.busy = False

        # Build the menu skeleton; history items get spliced in dynamically.
        self.status_item = rumps.MenuItem(self._status_text())
        self.status_item.set_callback(None)

        self.history_header = rumps.MenuItem("History")
        self.history_header.set_callback(None)

        self.clear_history_item = rumps.MenuItem(
            "Clear History", callback=self.clear_history,
        )

        self.menu = [
            self.status_item,
            None,
            self.history_header,
            # history entries injected here
        ]
        self._render_history()

        self._start_hotkey()

    # ---------- menu rendering ----------

    def _status_text(self):
        return f"Hold {self.cfg['hotkey'].replace('_', ' ')} to talk"

    def _render_history(self):
        # Wipe everything below the history header and rebuild.
        keys_to_remove = []
        seen_header = False
        for key in list(self.menu.keys()):
            if key == "History":
                seen_header = True
                continue
            if seen_header:
                keys_to_remove.append(key)
        for key in keys_to_remove:
            del self.menu[key]

        entries = history.load()
        if not entries:
            empty = rumps.MenuItem("  (no transcriptions yet)")
            empty.set_callback(None)
            self.menu.add(empty)
        else:
            for i, entry in enumerate(entries[:15]):
                preview = entry["text"].replace("\n", " ")
                if len(preview) > 50:
                    preview = preview[:47] + "…"
                item = rumps.MenuItem(
                    f"  {preview}",
                    callback=lambda sender, text=entry["text"]: self._copy(text),
                )
                self.menu.add(item)

        self.menu.add(rumps.separator)
        self.menu.add(self.clear_history_item)
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Open Settings…", callback=self.open_settings))
        self.menu.add(rumps.MenuItem("Reload .env", callback=self.reload_env))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit SpeakrFlow", callback=rumps.quit_application))

        self.status_item.title = self._status_text()

    def _copy(self, text):
        pyperclip.copy(text)
        rumps.notification("SpeakrFlow", "Copied to clipboard", text[:80])

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
            self.title = RECORDING_TITLE
        except Exception as e:
            self._error(f"Mic failed: {e}")

    def _on_hotkey_up(self):
        if self.busy:
            return
        self.busy = True
        self.title = WORKING_TITLE

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
                self._render_history()
            except TranscriptionError as e:
                self._error(str(e))
            except Exception as e:
                traceback.print_exc()
                self._error(f"Unexpected: {e}")
            finally:
                self.busy = False
                self.title = IDLE_TITLE

        threading.Thread(target=worker, daemon=True).start()

    def _error(self, msg):
        print(f"[SpeakrFlow] {msg}")
        try:
            rumps.notification("SpeakrFlow", "Error", msg)
        except Exception:
            pass

    # ---------- menu callbacks ----------

    def clear_history(self, _):
        history.clear()
        self._render_history()

    def reload_env(self, _):
        load_dotenv(ENV_FILE, override=True)
        rumps.notification("SpeakrFlow", ".env reloaded", str(ENV_FILE))

    def open_settings(self, _):
        # Run the settings window in a subprocess so its tkinter event loop
        # doesn't fight the rumps event loop. Reload config when it closes.
        def runner():
            try:
                subprocess.run(
                    [sys.executable, "-m", "src.settings_window"],
                    cwd=str(PROJECT_ROOT),
                    check=False,
                )
            except Exception as e:
                self._error(f"Settings window failed: {e}")
                return
            self.cfg = config.load()
            self._start_hotkey()
            self._render_history()

        threading.Thread(target=runner, daemon=True).start()


def main():
    SpeakrFlowApp().run()


if __name__ == "__main__":
    main()
