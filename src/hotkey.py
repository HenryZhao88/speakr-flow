import Quartz
from pynput import keyboard

# Map friendly names from the settings UI to pynput Key values.
KEY_MAP = {
    "right_option": keyboard.Key.alt_r,
    "left_option": keyboard.Key.alt_l,
    "right_command": keyboard.Key.cmd_r,
    "left_command": keyboard.Key.cmd_l,
    "right_control": keyboard.Key.ctrl_r,
    "left_control": keyboard.Key.ctrl_l,
    "right_shift": keyboard.Key.shift_r,
    "left_shift": keyboard.Key.shift_l,
    "caps_lock": keyboard.Key.caps_lock,
    "f13": keyboard.Key.f13,
    "f14": keyboard.Key.f14,
    "f15": keyboard.Key.f15,
}


def available_keys():
    return list(KEY_MAP.keys())


class ResilientListener(keyboard.Listener):
    """keyboard.Listener that survives macOS disabling its event tap.

    After system sleep/wake (or when the process is throttled and stops
    servicing events quickly enough), macOS disables the CGEventTap and posts
    kCGEventTapDisabledByTimeout to the callback. pynput ignores that event,
    so its listener thread stays alive but never receives another keystroke —
    which also means thread-liveness watchdogs can't detect the failure.
    Re-enabling the tap when the notification arrives keeps the hotkey working.
    """

    _DISABLED_EVENTS = (
        Quartz.kCGEventTapDisabledByTimeout,
        Quartz.kCGEventTapDisabledByUserInput,
    )

    def _create_event_tap(self):
        self._tap = super()._create_event_tap()
        return self._tap

    def _handle_message(self, proxy, event_type, event, refcon, injected):
        if event_type in self._DISABLED_EVENTS:
            tap = getattr(self, "_tap", None)
            if tap is not None:
                Quartz.CGEventTapEnable(tap, True)
                print("[SpeakrFlow] event tap was disabled by macOS; re-enabled")
            return
        super()._handle_message(proxy, event_type, event, refcon, injected)


class HoldHotkey:
    """Fires on_press when the chosen key goes down, on_release when it goes up.
    Ignores repeats while the key is held."""

    def __init__(self, key_name, on_press, on_release):
        self.target = KEY_MAP.get(key_name, keyboard.Key.alt_r)
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self.is_down = False
        self.listener = None

    def _handle_press(self, key):
        if key == self.target and not self.is_down:
            self.is_down = True
            try:
                self.on_press_cb()
            except Exception as e:
                print(f"hotkey press handler error: {e}")

    def _handle_release(self, key):
        if key == self.target and self.is_down:
            self.is_down = False
            try:
                self.on_release_cb()
            except Exception as e:
                print(f"hotkey release handler error: {e}")

    def start(self):
        if self.listener and self.listener.is_alive():
            return
        self.is_down = False
        self.listener = ResilientListener(
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()
            try:
                self.listener.join(1)
            except RuntimeError:
                pass
            self.listener = None
        self.is_down = False

    def is_alive(self):
        return bool(self.listener and self.listener.is_alive())
