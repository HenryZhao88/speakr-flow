import sys
import threading
import time
from pathlib import Path

import objc
import rumps
import pyperclip
from dotenv import load_dotenv
from AppKit import NSWorkspace, NSWorkspaceDidWakeNotification
from Foundation import (
    NSObject,
    NSProcessInfo,
    NSActivityUserInitiatedAllowingIdleSystemSleep,
)
from PyObjCTools import AppHelper

from . import config, history, paste, settings_window, status
from .diagnostics import logger
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


class _WakeObserver(NSObject):
    """Bridges NSWorkspaceDidWakeNotification to a plain Python callback."""

    def initWithHandler_(self, handler):
        self = objc.super(_WakeObserver, self).init()
        if self is None:
            return None
        self._handler = handler
        return self

    def workspaceDidWake_(self, _notification):
        self._handler()


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
        logger.info("Configuration provider=%s model=%s hotkey=%s env=%s",
                    self.cfg["provider"], self.cfg["model"], self.cfg["hotkey"], ENV_FILE)
        self.recorder = Recorder()
        self.hotkey = None
        self.busy = False
        self.recording = False
        self._state_lock = threading.RLock()

        self._transcribe_started = None
        self.status_item = rumps.MenuItem(status.status_line("idle"))
        self.status_item.set_callback(None)
        self.last_item = rumps.MenuItem("Last: —")
        self.last_item.set_callback(None)
        self._status_timer = rumps.Timer(self._tick_transcribing, 1)

        self._build_menu()
        self._start_hotkey()
        self.hotkey_watchdog = rumps.Timer(self._check_hotkey, 30)
        self.hotkey_watchdog.start()

        self._activity_token = None
        self._prevent_app_nap()
        self._wake_observer = _WakeObserver.alloc().initWithHandler_(self._on_wake)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self._wake_observer,
            "workspaceDidWake:",
            NSWorkspaceDidWakeNotification,
            None,
        )

    def _prevent_app_nap(self):
        """App Nap can throttle us enough that macOS kills the event tap."""
        try:
            self._activity_token = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
                NSActivityUserInitiatedAllowingIdleSystemSleep,
                "SpeakrFlow listens for a global hotkey",
            )
        except Exception as e:
            logger.warning("Could not disable App Nap: %s", e)

    def _on_wake(self):
        # The old event tap may have been disabled during sleep; a fresh
        # listener is cheap and guaranteed to work.
        logger.info("System woke from sleep; restarting hotkey listener")
        self._start_hotkey()

    # ---------- icon state ----------

    def _set_recording_icon(self, recording):
        if recording and self.icon_recording:
            self.icon = self.icon_recording
            self.template = False  # render the actual red, don't auto-tint
        else:
            self.icon = self.icon_idle
            self.template = True

    # ---------- status ----------

    def _set_state(self, state):
        """Main-thread only: sync icon, menu bar title, and status menu item."""
        self.status_item.title = status.status_line(state)
        self._set_recording_icon(state == "recording")
        if state in ("finishing", "transcribing"):
            self._transcribe_started = time.monotonic()
            self.title = status.transcribing_title(0)
            self._status_timer.start()
        else:
            self._status_timer.stop()
            self._transcribe_started = None
            self.title = None

    def _set_state_later(self, state):
        AppHelper.callAfter(self._set_state, state)

    def _tick_transcribing(self, _):
        if self._transcribe_started is not None:
            elapsed = time.monotonic() - self._transcribe_started
            self.title = status.transcribing_title(elapsed)

    def _finish_attempt(self, ok, detail):
        self.last_item.title = status.last_line(ok, detail, time.time())
        self._set_state("idle")

    # ---------- menu ----------

    def _build_menu(self):
        # rumps.App auto-creates self.menu as a Menu object; we only ever call
        # .add() on it, never reassign — keeps type checkers from getting confused.
        self.menu.add(self.status_item)
        self.menu.add(self.last_item)
        self.menu.add(rumps.separator)

        self.provider_menu = rumps.MenuItem("Provider")
        for p in PROVIDERS:
            item = rumps.MenuItem(p.capitalize(), callback=self._make_provider_cb(p))
            item.state = 1 if self.cfg["provider"] == p else 0
            self.provider_menu.add(item)

        self.menu.add(self.provider_menu)
        self.menu.add(rumps.separator)
        self._add_history_items()
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Clear History", callback=self.clear_history))
        self.menu.add(rumps.MenuItem("Reload .env", callback=self.reload_env))
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
            self.cfg = config.save(self.cfg)
            for item in self.provider_menu.values():
                item.state = 1 if item.title.lower() == name else 0
        return cb

    def clear_history(self, _):
        history.clear()
        self._rebuild_menu()

    def reload_env(self, _):
        load_dotenv(ENV_FILE, override=True)
        rumps.notification("SpeakrFlow", "Reloaded .env", str(ENV_FILE))

    # ---------- settings ----------

    def open_settings(self, _):
        settings_window.open_settings(
            on_save=self._on_settings_saved,
            on_history_change=self._rebuild_menu,
        )

    def _on_settings_saved(self, new_cfg):
        self.cfg = config.normalize(new_cfg)
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

    def _check_hotkey(self, _):
        with self._state_lock:
            if self.recording:
                return
            hotkey = self.hotkey
        if not hotkey or not hotkey.is_alive():
            logger.warning("Hotkey listener stopped; restarting")
            self._start_hotkey()

    def _on_hotkey_down(self):
        with self._state_lock:
            if self.busy or self.recording:
                return
            self.recording = True
        self._set_state_later("recording")
        try:
            self.recorder.start()
            logger.info("Recording started")
        except Exception as e:
            with self._state_lock:
                self.recording = False
            self._set_state_later("idle")
            self._error(f"Mic failed: {e}")

    def _on_hotkey_up(self):
        with self._state_lock:
            if self.busy or not self.recording:
                return
            self.recording = False
            self.busy = True
            cfg = dict(self.cfg)
        # Closing the mic is a separate stage from the network request.
        self._set_state_later("finishing")

        def worker():
            ok = False
            detail = ""
            try:
                wav, skip_reason = self.recorder.stop()
                if wav is None:
                    logger.info("Recording skipped: %s", skip_reason)
                    detail = f"skipped: {skip_reason}"
                    return

                # Save a copy to Downloads so the user can see it while processing
                from .paths import RECORDING_FILE
                try:
                    with open(RECORDING_FILE, "wb") as f:
                        f.write(wav.getvalue())
                except Exception as e:
                    logger.warning("Failed to save recording copy: %s", e)

                self._set_state_later("transcribing")
                text = transcribe(
                    wav,
                    provider=cfg["provider"],
                    model=cfg["model"],
                    language=cfg["language"],
                    prompt=cfg["prompt"],
                )
                if not text:
                    logger.info("Skipped: empty transcript")
                    detail = "skipped: empty transcript"
                    return
                ok = True
                logger.info("Transcription succeeded: %d characters", len(text))
                detail = text
                history.add(text, limit=cfg["history_limit"])
                # NSMenu changes from a background thread don't flush reliably.
                # Bounce the rebuild onto the main run loop so Cocoa sees it.
                AppHelper.callAfter(self._rebuild_menu)
                if cfg["auto_paste"]:
                    try:
                        paste.paste_text(text)
                    except Exception as e:
                        pyperclip.copy(text)
                        self._error(f"Paste failed; copied to clipboard instead: {e}")
                else:
                    pyperclip.copy(text)
            except TranscriptionError as e:
                detail = str(e)
                self._error(detail)
            except Exception as e:
                logger.exception("Recording worker failed")
                detail = f"Unexpected: {e}"
                self._error(detail)
            finally:
                # Clean up the recording copy now that we have a response (or error)
                from .paths import RECORDING_FILE
                try:
                    if RECORDING_FILE.exists():
                        RECORDING_FILE.unlink()
                except Exception as e:
                    logger.warning("Failed to delete recording copy: %s", e)

                with self._state_lock:
                    self.busy = False
                AppHelper.callAfter(self._finish_attempt, ok, detail)

        threading.Thread(target=worker, daemon=True).start()

    def _error(self, msg):
        logger.error("%s", msg)
        AppHelper.callAfter(self._notify_error, msg)

    def _notify_error(self, msg):
        try:
            rumps.notification("SpeakrFlow", "Error", msg)
        except Exception:
            pass


def _hide_dock_icon():
    """Make this a menu-bar-only app at runtime.

    The py2app bundle uses LSUIElement, but `python run.py` launches under
    the Python framework binary, which shows a rocket in the Dock. Switching
    the activation policy to Accessory hides it for source runs too.
    """
    try:
        from AppKit import NSApplication  # type: ignore[import-not-found]
        # NSApplicationActivationPolicyAccessory = 1
        NSApplication.sharedApplication().setActivationPolicy_(1)
    except Exception:
        pass


def main():
    _hide_dock_icon()
    SpeakrFlowApp().run()


if __name__ == "__main__":
    main()
