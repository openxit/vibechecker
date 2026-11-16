"""Lightweight Windows desktop notifications without external dependencies."""

from __future__ import annotations

import subprocess
import sys


def send_windows_notification(title: str, message: str) -> None:
    """Display a non-blocking Windows tray balloon notification."""
    if sys.platform != "win32":
        return

    safe_title = title.replace("'", "''")[:80]
    safe_msg = message.replace("'", "''")[:220]
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Warning; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(6000, '{safe_title}', '{safe_msg}', [System.Windows.Forms.ToolTipIcon]::Warning); "
        "Start-Sleep -Seconds 7; "
        "$n.Dispose()"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError:
        pass
