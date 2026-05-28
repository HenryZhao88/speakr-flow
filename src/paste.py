import time
import pyperclip
from pynput.keyboard import Controller, Key

_kb = Controller()


def paste_text(text):
    """Drop text on the clipboard and fire Cmd+V at whatever has focus."""
    pyperclip.copy(text)
    # Tiny delay so the clipboard write actually settles before the paste.
    time.sleep(0.05)
    with _kb.pressed(Key.cmd):
        _kb.press("v")
        _kb.release("v")
