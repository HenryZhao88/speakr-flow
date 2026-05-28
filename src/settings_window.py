"""Native AppKit settings window. Lives in the same process as the menu bar
app, so it works inside the py2app bundle (unlike a tkinter subprocess).
"""
import objc
from Foundation import NSObject, NSMakeRect
from AppKit import (
    NSWindow,
    NSWindowStyleMaskTitled,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSBackingStoreBuffered,
    NSTextField,
    NSPopUpButton,
    NSButton,
    NSApp,
    NSFloatingWindowLevel,
)

from . import config, history, hotkey
from .paths import ENV_FILE


# Module-level reference so the window doesn't get garbage collected while open.
_open_controller = None

NSSwitchButton = 3      # NSButtonType
NSBezelRounded = 1      # NSBezelStyle


class SettingsController(NSObject):
    def initWithOnSave_(self, on_save):
        self = objc.super(SettingsController, self).init()
        if self is None:
            return None
        self.cfg = config.load()
        self.on_save = on_save
        self._build_window()
        return self

    # ---------- view helpers ----------

    @objc.python_method
    def _label(self, text, x, y, w=130):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 20))
        f.setStringValue_(text)
        f.setBordered_(False)
        f.setDrawsBackground_(False)
        f.setEditable_(False)
        f.setSelectable_(False)
        return f

    @objc.python_method
    def _text(self, value, x, y, w=260):
        f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
        f.setStringValue_(value or "")
        return f

    @objc.python_method
    def _popup(self, options, current, x, y, w=200):
        p = NSPopUpButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 26))
        for o in options:
            p.addItemWithTitle_(o)
        if current in options:
            p.selectItemWithTitle_(current)
        return p

    @objc.python_method
    def _checkbox(self, label, on, x, y, w=260):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 22))
        b.setButtonType_(NSSwitchButton)
        b.setTitle_(label)
        b.setState_(1 if on else 0)
        return b

    @objc.python_method
    def _button(self, label, x, y, action, w=130, default=False):
        b = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, 28))
        b.setTitle_(label)
        b.setBezelStyle_(NSBezelRounded)
        b.setTarget_(self)
        b.setAction_(action)
        if default:
            b.setKeyEquivalent_("\r")
        return b

    # ---------- window layout ----------

    @objc.python_method
    def _build_window(self):
        W, H = 480, 420
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, W, H), style, NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("SpeakrFlow Settings")
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setReleasedWhenClosed_(False)
        cv = self.window.contentView()

        # Layout top-down. AppKit origin is bottom-left, so we count down from H.
        y = H - 40

        cv.addSubview_(self._label("Hotkey", 24, y))
        self.hotkey_box = self._popup(
            hotkey.available_keys(), self.cfg["hotkey"], 160, y - 4,
        )
        cv.addSubview_(self.hotkey_box)

        y -= 38
        cv.addSubview_(self._label("Model", 24, y))
        self.model_field = self._text(self.cfg["model"], 160, y - 2)
        cv.addSubview_(self.model_field)

        y -= 38
        cv.addSubview_(self._label("Language", 24, y))
        self.lang_field = self._text(self.cfg["language"], 160, y - 2, w=120)
        cv.addSubview_(
            self._label("(blank = auto-detect)", 290, y, w=180),
        )
        cv.addSubview_(self.lang_field)

        y -= 38
        cv.addSubview_(self._label("Context prompt", 24, y))
        self.prompt_field = self._text(self.cfg["prompt"], 160, y - 2)
        cv.addSubview_(self.prompt_field)

        y -= 38
        cv.addSubview_(self._label("History limit", 24, y))
        self.limit_field = self._text(str(self.cfg["history_limit"]), 160, y - 2, w=80)
        cv.addSubview_(self.limit_field)

        y -= 38
        self.autopaste = self._checkbox(
            "Auto-paste at cursor", self.cfg["auto_paste"], 24, y,
        )
        cv.addSubview_(self.autopaste)

        y -= 26
        self.sounds = self._checkbox(
            "Play sounds", self.cfg["play_sounds"], 24, y,
        )
        cv.addSubview_(self.sounds)

        y -= 50
        cv.addSubview_(self._button(
            "Clear History", 24, y, action="clearHistory:",
        ))
        cv.addSubview_(self._button(
            "Reload .env", 160, y, action="reloadEnv:",
        ))

        # Bottom row: Cancel / Save
        cv.addSubview_(self._button("Cancel", W - 270, 18, action="cancel:"))
        cv.addSubview_(self._button("Save", W - 130, 18, action="save:", default=True))

        self.window.center()

    # ---------- actions ----------

    @objc.python_method
    def show(self):
        NSApp.activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)

    def save_(self, sender):
        new_cfg = dict(self.cfg)
        new_cfg["hotkey"] = str(self.hotkey_box.titleOfSelectedItem())
        new_cfg["model"] = str(self.model_field.stringValue()).strip() or self.cfg["model"]
        new_cfg["language"] = str(self.lang_field.stringValue()).strip()
        new_cfg["prompt"] = str(self.prompt_field.stringValue()).strip()
        try:
            new_cfg["history_limit"] = max(1, int(str(self.limit_field.stringValue()).strip()))
        except ValueError:
            pass
        new_cfg["auto_paste"] = bool(self.autopaste.state())
        new_cfg["play_sounds"] = bool(self.sounds.state())

        config.save(new_cfg)
        if self.on_save:
            try:
                self.on_save(new_cfg)
            except Exception as e:
                print(f"on_save callback failed: {e}")
        self._close()

    def cancel_(self, sender):
        self._close()

    def clearHistory_(self, sender):
        history.clear()

    def reloadEnv_(self, sender):
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=True)

    @objc.python_method
    def _close(self):
        global _open_controller
        self.window.close()
        _open_controller = None


def open_settings(on_save):
    """Open (or focus) the settings window."""
    global _open_controller
    if _open_controller is not None:
        _open_controller.show()
        return
    _open_controller = SettingsController.alloc().initWithOnSave_(on_save)
    _open_controller.show()
