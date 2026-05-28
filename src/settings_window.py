import tkinter as tk
from tkinter import ttk, messagebox

# Support running both as `python -m src.settings_window` (subprocess from the
# menu bar app) and as a normal import.
try:
    from . import config, hotkey
except ImportError:
    from src import config, hotkey


def open_settings(on_save=None):
    """Show a tkinter settings window. on_save(new_cfg) runs after Save."""
    cfg = config.load()

    root = tk.Tk()
    root.title("SpeakrFlow Settings")
    root.geometry("460x420")
    root.resizable(False, False)

    pad = {"padx": 12, "pady": 6}

    def row(label, widget, r):
        ttk.Label(root, text=label).grid(row=r, column=0, sticky="w", **pad)
        widget.grid(row=r, column=1, sticky="ew", **pad)

    provider_var = tk.StringVar(value=cfg["provider"])
    provider_box = ttk.Combobox(
        root, textvariable=provider_var,
        values=["groq", "openai"], state="readonly",
    )
    row("Provider", provider_box, 0)

    model_var = tk.StringVar(value=cfg["model"])
    model_entry = ttk.Entry(root, textvariable=model_var)
    row("Model", model_entry, 1)

    hotkey_var = tk.StringVar(value=cfg["hotkey"])
    hotkey_box = ttk.Combobox(
        root, textvariable=hotkey_var,
        values=hotkey.available_keys(), state="readonly",
    )
    row("Hold-to-talk key", hotkey_box, 2)

    language_var = tk.StringVar(value=cfg["language"])
    lang_entry = ttk.Entry(root, textvariable=language_var)
    row("Language (blank = auto)", lang_entry, 3)

    prompt_var = tk.StringVar(value=cfg["prompt"])
    prompt_entry = ttk.Entry(root, textvariable=prompt_var)
    row("Context prompt", prompt_entry, 4)

    limit_var = tk.IntVar(value=cfg["history_limit"])
    limit_spin = ttk.Spinbox(root, from_=10, to=500, textvariable=limit_var)
    row("History limit", limit_spin, 5)

    paste_var = tk.BooleanVar(value=cfg["auto_paste"])
    ttk.Checkbutton(
        root, text="Auto-paste transcription at cursor", variable=paste_var,
    ).grid(row=6, column=0, columnspan=2, sticky="w", **pad)

    sounds_var = tk.BooleanVar(value=cfg["play_sounds"])
    ttk.Checkbutton(
        root, text="Play start/stop sounds", variable=sounds_var,
    ).grid(row=7, column=0, columnspan=2, sticky="w", **pad)

    hint = (
        "API keys live in the .env file in the project folder.\n"
        "Grant Microphone, Accessibility, and Input Monitoring permissions\n"
        "in System Settings → Privacy & Security for hotkeys + pasting to work."
    )
    ttk.Label(root, text=hint, foreground="#666", justify="left").grid(
        row=8, column=0, columnspan=2, sticky="w", **pad,
    )

    def save_and_close():
        new_cfg = {
            "provider": provider_var.get(),
            "model": model_var.get().strip() or cfg["model"],
            "hotkey": hotkey_var.get(),
            "language": language_var.get().strip(),
            "prompt": prompt_var.get().strip(),
            "history_limit": int(limit_var.get()),
            "auto_paste": bool(paste_var.get()),
            "play_sounds": bool(sounds_var.get()),
        }
        config.save(new_cfg)
        if on_save is not None:
            try:
                on_save(new_cfg)
            except Exception as e:
                messagebox.showerror("SpeakrFlow", f"Failed to apply settings:\n{e}")
        root.destroy()

    btns = ttk.Frame(root)
    btns.grid(row=9, column=0, columnspan=2, pady=14)
    ttk.Button(btns, text="Cancel", command=root.destroy).pack(side="left", padx=6)
    ttk.Button(btns, text="Save", command=save_and_close).pack(side="left", padx=6)

    root.columnconfigure(1, weight=1)
    root.mainloop()


if __name__ == "__main__":
    open_settings()

