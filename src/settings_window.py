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
    NSScrollView,
    NSView,
    NSColor,
    NSFont,
    NSApp,
    NSFloatingWindowLevel,
)

from . import config, history, hotkey
from .paths import ENV_FILE


# Module-level reference so the window doesn't get garbage collected while open.
_open_controller = None

NSSwitchButton = 3      # NSButtonType
NSBezelRounded = 1      # NSBezelStyle
NSBezelBorder = 2       # NSBorderType (for the history scroll view)


class _FlippedView(NSView):
    """Document view whose origin is top-left, so we can lay history rows
    out top-down with simple increasing y values."""

    def isFlipped(self):
        return True


class SettingsController(NSObject):
    def initWithOnSave_onHistoryChange_(self, on_save, on_history_change):
        self = objc.super(SettingsController, self).init()
        if self is None:
            return None
        self.cfg = config.load()
        self.on_save = on_save
        self.on_history_change = on_history_change
        self.history_rows = []
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
        W, H = 540, 620
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
        self.window.setDelegate_(self)
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

        # ---- History section: editable list of past transcriptions ----
        y -= 34
        cv.addSubview_(self._label("History", 24, y, w=200))

        # Scroll view spans from just below the bottom button rows up to the
        # History label. Bottom rows are pinned: Cancel/Save at y=18,
        # Clear History / Reload .env at y=56.
        scroll_y, scroll_top = 94, y - 6
        scroll = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(24, scroll_y, W - 48, scroll_top - scroll_y),
        )
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutohidesScrollers_(True)
        self.history_scroll = scroll

        doc_w = scroll.contentSize().width
        self.history_doc = _FlippedView.alloc().initWithFrame_(
            NSMakeRect(0, 0, doc_w, scroll.contentSize().height),
        )
        scroll.setDocumentView_(self.history_doc)
        cv.addSubview_(scroll)
        self._populate_history()

        cv.addSubview_(self._button(
            "Clear History", 24, 56, action="clearHistory:",
        ))
        cv.addSubview_(self._button(
            "Reload .env", 160, 56, action="reloadEnv:",
        ))

        # Bottom row: Cancel / Save
        cv.addSubview_(self._button("Cancel", W - 270, 18, action="cancel:"))
        cv.addSubview_(self._button("Save", W - 130, 18, action="save:", default=True))

        self.window.center()

    # ---------- history list ----------

    @objc.python_method
    def _populate_history(self):
        """(Re)build the rows inside the history scroll view from disk."""
        doc = self.history_doc
        for sub in list(doc.subviews()):
            sub.removeFromSuperview()
        self.history_rows = []

        entries = history.load()
        row_h = 72
        width = self.history_scroll.contentSize().width
        visible_h = self.history_scroll.contentSize().height
        total_h = max(len(entries) * row_h, visible_h)
        doc.setFrame_(NSMakeRect(0, 0, width, total_h))

        if not entries:
            empty = self._label("(no transcriptions yet)", 8, 8, w=width - 16)
            empty.setTextColor_(NSColor.secondaryLabelColor())
            doc.addSubview_(empty)
            return

        for i, entry in enumerate(entries):
            y = i * row_h
            time_lbl = self._label(entry.get("time", ""), 6, y + 6, w=width - 16)
            time_lbl.setFont_(NSFont.systemFontOfSize_(10))
            time_lbl.setTextColor_(NSColor.secondaryLabelColor())
            doc.addSubview_(time_lbl)

            field = NSTextField.alloc().initWithFrame_(
                NSMakeRect(6, y + 24, width - 96, 40),
            )
            field.setStringValue_(entry.get("text", ""))
            field.setUsesSingleLineMode_(False)
            field.cell().setWraps_(True)
            doc.addSubview_(field)

            btn = NSButton.alloc().initWithFrame_(
                NSMakeRect(width - 84, y + 30, 78, 26),
            )
            btn.setTitle_("Delete")
            btn.setBezelStyle_(NSBezelRounded)
            btn.setTarget_(self)
            btn.setAction_("deleteEntry:")
            btn.setTag_(i)
            doc.addSubview_(btn)

            self.history_rows.append((field, i))

    @objc.python_method
    def _save_history_edits(self):
        """Write any edited text in the visible rows back to disk."""
        entries = history.load()
        changed = False
        for field, idx in self.history_rows:
            if 0 <= idx < len(entries):
                new_text = str(field.stringValue())
                if entries[idx].get("text") != new_text:
                    entries[idx]["text"] = new_text
                    changed = True
        if changed:
            history.save(entries)
        return changed

    @objc.python_method
    def _notify_history_changed(self):
        if self.on_history_change:
            try:
                self.on_history_change()
            except Exception as e:
                print(f"on_history_change callback failed: {e}")

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

        self._save_history_edits()
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
        self._populate_history()
        self._notify_history_changed()

    def deleteEntry_(self, sender):
        # Persist any pending edits first so reindexing doesn't drop them.
        self._save_history_edits()
        history.delete(sender.tag())
        self._populate_history()
        self._notify_history_changed()

    def reloadEnv_(self, sender):
        from dotenv import load_dotenv
        load_dotenv(ENV_FILE, override=True)

    @objc.python_method
    def _close(self):
        global _open_controller
        self.window.close()
        _open_controller = None

    def windowWillClose_(self, notification):
        global _open_controller
        _open_controller = None


def open_settings(on_save, on_history_change=None):
    """Open (or focus) the settings window."""
    global _open_controller
    if _open_controller is not None:
        _open_controller.show()
        return
    _open_controller = SettingsController.alloc().initWithOnSave_onHistoryChange_(
        on_save, on_history_change,
    )
    _open_controller.show()
