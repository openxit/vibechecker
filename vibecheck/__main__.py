"""Module and frozen-executable entry point for vibecheck."""

from __future__ import annotations

import os
import sys


def _ensure_safe_streams() -> None:
    """Prevent crashes in `--noconsole` Windows builds where stdio may be None."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _entry() -> int:
    _ensure_safe_streams()
    args = sys.argv[1:]

    # Double-clicking the .exe passes no arguments -> open the desktop GUI.
    if not args:
        try:
            from vibecheck.gui import launch_gui

            return launch_gui()
        except Exception as exc:
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("vibecheck error", f"Failed to start GUI:\n{exc}")
                root.destroy()
            except Exception as ui_exc:
                sys.stderr.write(f"vibecheck GUI startup failed: {exc} ({ui_exc})\n")
            return 1

    from vibecheck.cli import main

    return main(args)


if __name__ == "__main__":
    raise SystemExit(_entry())
