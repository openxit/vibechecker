"""Lightweight desktop notifications for Windows, Linux, and macOS."""

from __future__ import annotations

import shutil
import subprocess
import sys


def send_notification(title: str, message: str) -> None:
    """Display a non-blocking desktop notification when supported."""
    if sys.platform == "win32":
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
        return

    if sys.platform.startswith("linux"):
        notify_bin = shutil.which("notify-send")
        if notify_bin:
            try:
                subprocess.Popen(
                    [notify_bin, "--app-name=vibecheck", title[:80], message[:220]],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                pass
        return

    if sys.platform == "darwin":
        safe_title = title.replace('"', '\\"')[:80]
        safe_msg = message.replace('"', '\\"')[:220]
        try:
            subprocess.Popen(
                ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
