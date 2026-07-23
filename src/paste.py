import time
import pyperclip

V_KEYCODE = 9


def _copy_text(text):
    pyperclip.copy(text)
    # Give the pasteboard a chance to settle; verify when the platform allows.
    for _ in range(3):
        time.sleep(0.03)
        try:
            if pyperclip.paste() == text:
                return
        except Exception:
            return
        pyperclip.copy(text)


def _paste_with_quartz():
    from Quartz import (  # type: ignore[import-not-found]
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGEventFlagMaskCommand,
        kCGHIDEventTap,
    )

    down = CGEventCreateKeyboardEvent(None, V_KEYCODE, True)
    up = CGEventCreateKeyboardEvent(None, V_KEYCODE, False)
    CGEventSetFlags(down, kCGEventFlagMaskCommand)
    CGEventSetFlags(up, kCGEventFlagMaskCommand)
    CGEventPost(kCGHIDEventTap, down)
    time.sleep(0.02)
    CGEventPost(kCGHIDEventTap, up)


def _paste_with_pynput():
    from pynput.keyboard import Controller, Key

    kb = Controller()
    with kb.pressed(Key.cmd):
        kb.press("v")
        kb.release("v")


def _accessibility_trusted():
    try:
        import HIServices  # type: ignore[import-not-found]
        return bool(HIServices.AXIsProcessTrusted())
    except Exception:
        # Can't tell — assume yes rather than block pasting.
        return True


def paste_text(text):
    """Drop text on the clipboard and fire Cmd+V at whatever has focus."""
    _copy_text(text)
    # CGEventPost (and pynput's Controller) silently discard events when the
    # app lacks Accessibility permission — fail loudly instead so the caller
    # can tell the user.
    if not _accessibility_trusted():
        raise PermissionError(
            "Accessibility permission missing. Enable SpeakrFlow in "
            "System Settings → Privacy & Security → Accessibility."
        )
    try:
        _paste_with_quartz()
    except Exception:
        _paste_with_pynput()
