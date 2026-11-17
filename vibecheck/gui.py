"""Native high-DPI Tkinter desktop GUI for vibecheck."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from vibecheck import __version__
from vibecheck.models import Finding, ScanResult, Severity
from vibecheck.notify import send_notification
from vibecheck.projects import ProjectProfile, load_projects, save_projects

_UI_FONT = "Segoe UI" if sys.platform == "win32" else ("Helvetica Neue" if sys.platform == "darwin" else "DejaVu Sans")
_MONO_FONT = "Consolas" if sys.platform == "win32" else ("Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono")

_INTERVAL_LABELS = {
    0: "Manual",
    5: "Every 5 min",
    15: "Every 15 min",
    30: "Every 30 min",
    60: "Every 60 min",
}

# Clean light-neutral desktop palette (GitHub Desktop / Linear style)
_BG_MAIN = "#ffffff"
_BG_SIDEBAR = "#f6f8fa"
_BG_CARD = "#ffffff"
_BG_SUBTLE = "#f6f8fa"
_BORDER = "#d0d7de"
_TEXT = "#1f2328"
_MUTED = "#656d76"
_PRIMARY = "#0969da"
_PRIMARY_HOVER = "#0550ae"
_SEL_BG = "#ddf4ff"
_ERR = "#cf222e"
_WARN = "#9a6700"
_OK = "#1a7f37"


def _enable_high_dpi() -> None:
    """Enable sharp High-DPI text and borders on Windows."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


class _ScanProgressDialog:
    """Modal progress popup displaying current stage, file count, and progress bar."""

    def __init__(self, parent: tk.Tk, cancel_event: threading.Event) -> None:
        self._cancel_event = cancel_event
        self.win = tk.Toplevel(parent)
        self.win.title("Scanning")
        self.win.resizable(False, False)
        self.win.configure(bg=_BG_MAIN)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        frame = tk.Frame(self.win, bg=_BG_MAIN, padx=20, pady=18)
        frame.pack(fill=tk.BOTH, expand=True)

        self._count_var = tk.StringVar(value="Discovering files...")
        self._file_var = tk.StringVar(value="")

        tk.Label(
            frame,
            textvariable=self._count_var,
            bg=_BG_MAIN,
            fg=_TEXT,
            font=(_UI_FONT, 10, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 8))

        self._bar = ttk.Progressbar(
            frame,
            orient=tk.HORIZONTAL,
            length=400,
            mode="indeterminate",
            style="Modern.Horizontal.TProgressbar",
        )
        self._bar.pack(fill=tk.X, pady=(0, 8))
        self._bar.start(12)

        tk.Label(
            frame,
            textvariable=self._file_var,
            bg=_BG_MAIN,
            fg=_MUTED,
            font=(_MONO_FONT, 9),
            width=54,
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 14))

        btn_row = tk.Frame(frame, bg=_BG_MAIN)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", style="Secondary.TButton", command=self._on_cancel).pack(
            side=tk.RIGHT
        )

        self._center_over_parent(parent)

    def _center_over_parent(self, parent: tk.Tk) -> None:
        self.win.update_idletasks()
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        ww = self.win.winfo_reqwidth()
        wh = self.win.winfo_reqheight()
        x = px + max(0, (pw - ww) // 2)
        y = py + max(0, (ph - wh) // 2)
        self.win.geometry(f"+{x}+{y}")

    def set_status(self, text: str) -> None:
        if self.win.winfo_exists():
            self._count_var.set(text)

    def update_progress(self, current: int, total: int, rel_path: Path) -> None:
        if not self.win.winfo_exists():
            return
        if str(self._bar.cget("mode")) != "determinate":
            self._bar.stop()
            self._bar.configure(mode="determinate", maximum=max(1, total))
        self._bar["value"] = current
        pct = round((current / total) * 100) if total > 0 else 0
        self._count_var.set(f"Checking file {current} of {total} ({pct}%)")
        display = rel_path.as_posix()
        if len(display) > 56:
            display = "..." + display[-53:]
        self._file_var.set(display)

    def _on_cancel(self) -> None:
        self._cancel_event.set()
        self._count_var.set("Cancelling...")

    def close(self) -> None:
        if self.win.winfo_exists():
            self._bar.stop()
            self.win.grab_release()
            self.win.destroy()


class _FixPromptDialog:
    """Modal window displaying a generated repair prompt with clipboard copy."""

    def __init__(self, parent: tk.Tk, prompt_text: str) -> None:
        self._prompt_text = prompt_text
        self.win = tk.Toplevel(parent)
        self.win.title("Repair Prompt")
        self.win.geometry("740x500")
        self.win.minsize(540, 360)
        self.win.configure(bg=_BG_MAIN)
        self.win.transient(parent)

        frame = tk.Frame(self.win, bg=_BG_MAIN, padx=16, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text="Copy this prompt into your editor or assistant to resolve the findings:",
            bg=_BG_MAIN,
            fg=_TEXT,
            font=(_UI_FONT, 9),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 8))

        border = tk.Frame(frame, bg=_BORDER, padx=1, pady=1)
        border.pack(fill=tk.BOTH, expand=True)

        text_frame = tk.Frame(border, bg=_BG_SUBTLE)
        text_frame.pack(fill=tk.BOTH, expand=True)

        text_box = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=(_MONO_FONT, 9),
            bg=_BG_SUBTLE,
            fg=_TEXT,
            relief=tk.FLAT,
            padx=10,
            pady=8,
        )
        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_box.yview)
        text_box.configure(yscrollcommand=y_scroll.set)
        text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        text_box.insert("1.0", prompt_text)
        text_box.configure(state=tk.DISABLED)

        btn_row = tk.Frame(frame, bg=_BG_MAIN)
        btn_row.pack(fill=tk.X, pady=(10, 0))
        self._copied_var = tk.StringVar(value="")
        tk.Label(
            btn_row,
            textvariable=self._copied_var,
            bg=_BG_MAIN,
            fg=_OK,
            font=(_UI_FONT, 9),
        ).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", style="Secondary.TButton", command=self.win.destroy).pack(
            side=tk.RIGHT
        )
        ttk.Button(btn_row, text="Save .txt...", style="Secondary.TButton", command=self._save_txt).pack(
            side=tk.RIGHT, padx=(0, 6)
        )
        ttk.Button(btn_row, text="Copy to Clipboard", style="Primary.TButton", command=self._copy).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

    def _copy(self) -> None:
        self.win.clipboard_clear()
        self.win.clipboard_append(self._prompt_text)
        self.win.update()
        self._copied_var.set("Copied to clipboard.")

    def _save_txt(self) -> None:
        dest = filedialog.asksaveasfilename(
            parent=self.win,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("Markdown files", "*.md")],
            initialfile="vibecheck-fix-prompt.txt",
        )
        if not dest:
            return
        Path(dest).write_text(self._prompt_text, encoding="utf-8")
        self._copied_var.set(f"Saved to {Path(dest).name}")


class _ProjectDialog:
    """Dialog to add or edit a monitored project."""

    def __init__(self, parent: tk.Tk, existing: ProjectProfile | None = None) -> None:
        self.result: ProjectProfile | None = None
        self.win = tk.Toplevel(parent)
        self.win.title("Edit Project" if existing else "Add Project")
        self.win.resizable(False, False)
        self.win.configure(bg=_BG_MAIN)
        self.win.transient(parent)
        self.win.grab_set()

        frame = tk.Frame(self.win, bg=_BG_MAIN, padx=18, pady=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self._name_var = tk.StringVar(value=existing.name if existing else "")
        self._target_var = tk.StringVar(value=existing.target if existing else "")
        self._interval_var = tk.StringVar(
            value=_INTERVAL_LABELS.get(existing.interval_minutes if existing else 30, "Every 30 min")
        )
        self._enabled_var = tk.BooleanVar(value=existing.enabled if existing else True)
        self._tests_var = tk.BooleanVar(value=existing.include_tests if existing else False)

        tk.Label(frame, text="Name", bg=_BG_MAIN, fg=_TEXT, font=("Segoe UI", 9)).grid(
            row=0, column=0, sticky=tk.W, pady=5, padx=(0, 10)
        )
        ttk.Entry(frame, textvariable=self._name_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky=tk.EW, pady=5
        )

        tk.Label(frame, text="Folder or GitHub URL", bg=_BG_MAIN, fg=_TEXT, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky=tk.W, pady=5, padx=(0, 10)
        )
        ttk.Entry(frame, textvariable=self._target_var, width=34).grid(
            row=1, column=1, sticky=tk.EW, pady=5, padx=(0, 6)
        )
        ttk.Button(frame, text="Browse...", style="Secondary.TButton", command=self._browse).grid(
            row=1, column=2, pady=5
        )

        tk.Label(frame, text="Auto-check", bg=_BG_MAIN, fg=_TEXT, font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky=tk.W, pady=5, padx=(0, 10)
        )
        ttk.Combobox(
            frame,
            textvariable=self._interval_var,
            values=tuple(_INTERVAL_LABELS.values()),
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky=tk.W, pady=5)

        ttk.Checkbutton(
            frame,
            text="Enable background monitor",
            variable=self._enabled_var,
            style="White.TCheckbutton",
        ).grid(row=3, column=1, sticky=tk.W, pady=3)
        ttk.Checkbutton(
            frame,
            text="Include test files",
            variable=self._tests_var,
            style="White.TCheckbutton",
        ).grid(row=4, column=1, sticky=tk.W, pady=3)

        btn_row = tk.Frame(frame, bg=_BG_MAIN)
        btn_row.grid(row=5, column=0, columnspan=3, sticky=tk.E, pady=(14, 0))
        ttk.Button(btn_row, text="Cancel", style="Secondary.TButton", command=self.win.destroy).pack(
            side=tk.RIGHT
        )
        ttk.Button(btn_row, text="Save", style="Primary.TButton", command=self._save).pack(
            side=tk.RIGHT, padx=(0, 6)
        )

        self.win.wait_window()

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self.win)
        if selected:
            self._target_var.set(selected)
            if not self._name_var.get().strip():
                self._name_var.set(Path(selected).name or selected)

    def _save(self) -> None:
        target = self._target_var.get().strip()
        name = self._name_var.get().strip()
        if not target:
            messagebox.showerror("Missing target", "Enter a local folder or GitHub URL.", parent=self.win)
            return
        if not name:
            from vibecheck.remote import parse_github_target

            gh = parse_github_target(target)
            name = gh.slug if gh else (Path(target).name or target)

        label = self._interval_var.get()
        interval = 30
        for mins, lbl in _INTERVAL_LABELS.items():
            if lbl == label:
                interval = mins
                break

        self.result = ProjectProfile(
            name=name,
            target=target,
            interval_minutes=interval,
            enabled=self._enabled_var.get(),
            include_tests=self._tests_var.get(),
        )
        self.win.destroy()


def _execute_scan(
    target_str: str,
    *,
    include_tests: bool,
    progress_callback=None,
    cancel_event: threading.Event | None = None,
    status_callback=None,
) -> ScanResult:
    """Run either a GitHub archive scan or a local directory/file scan."""
    from vibecheck.config import load_config
    from vibecheck.remote import parse_github_target, scan_github_repo
    from vibecheck.scanner import scan_path

    gh_target = parse_github_target(target_str)
    if gh_target is not None:
        return scan_github_repo(
            gh_target,
            include_tests=include_tests,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            status_callback=status_callback,
        )

    target = Path(target_str).expanduser()
    config = load_config(target, include_tests=include_tests)
    return scan_path(
        target,
        config,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


class VibecheckApp:
    """Modern light-neutral desktop window for running vibecheck scans."""

    def __init__(self, root: tk.Tk, initial_dir: Path | None = None) -> None:
        self.root = root
        self.root.title(f"vibecheck {__version__}")
        self.root.geometry("1160x700")
        self.root.minsize(880, 540)
        self.root.configure(bg=_BG_MAIN)

        self._last_result: ScanResult | None = None
        self._findings_by_iid: dict[str, Finding] = {}
        self._projects: list[ProjectProfile] = load_projects()
        self._last_run_ts: dict[int, float] = {}
        self._bg_running: set[int] = set()

        self._target_var = tk.StringVar(value=str(initial_dir or Path.cwd()))
        self._include_tests_var = tk.BooleanVar(value=False)
        self._severity_filter_var = tk.StringVar(value="all")
        self._status_var = tk.StringVar(value="Ready")

        self._score_val_var = tk.StringVar(value="--")
        self._errors_val_var = tk.StringVar(value="--")
        self._warnings_val_var = tk.StringVar(value="--")
        self._vibe_val_var = tk.StringVar(value="--")

        self._configure_styles()
        self._build_layout()
        self._refresh_sidebar()
        self.root.after(15_000, self._scheduler_tick)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("TFrame", background=_BG_MAIN)
        style.configure("Sidebar.TFrame", background=_BG_SIDEBAR)
        style.configure("TLabel", background=_BG_MAIN, foreground=_TEXT, font=("Segoe UI", 9))
        style.configure("White.TCheckbutton", background=_BG_MAIN, foreground=_TEXT, font=("Segoe UI", 9))
        style.map("White.TCheckbutton", background=[("active", _BG_MAIN)])

        style.configure(
            "Primary.TButton",
            background=_PRIMARY,
            foreground="#ffffff",
            borderwidth=0,
            padding=(14, 6),
            font=(_UI_FONT, 9, "bold"),
        )
        style.map(
            "Primary.TButton",
            background=[("disabled", "#94a3b8"), ("active", _PRIMARY_HOVER)],
        )

        style.configure(
            "Secondary.TButton",
            background=_BG_SUBTLE,
            foreground=_TEXT,
            borderwidth=1,
            bordercolor=_BORDER,
            padding=(10, 5),
            font=(_UI_FONT, 9),
        )
        style.map(
            "Secondary.TButton",
            background=[("disabled", "#f1f5f9"), ("active", "#eaeef2")],
            foreground=[("disabled", "#94a3b8")],
        )

        style.configure(
            "Modern.Horizontal.TProgressbar",
            troughcolor=_BG_SUBTLE,
            background=_PRIMARY,
            bordercolor=_BORDER,
            lightcolor=_PRIMARY,
            darkcolor=_PRIMARY,
            thickness=10,
        )

        style.configure(
            "Treeview",
            background=_BG_MAIN,
            fieldbackground=_BG_MAIN,
            foreground=_TEXT,
            rowheight=28,
            borderwidth=0,
            font=(_UI_FONT, 9),
        )
        style.configure(
            "Treeview.Heading",
            background=_BG_SUBTLE,
            foreground=_MUTED,
            relief=tk.FLAT,
            borderwidth=0,
            padding=(8, 6),
            font=(_UI_FONT, 9, "bold"),
        )
        style.map("Treeview", background=[("selected", _SEL_BG)], foreground=[("selected", _TEXT)])
        style.map("Treeview.Heading", background=[("active", "#eaeef2")])

    def _build_layout(self) -> None:
        shell = tk.Frame(self.root, bg=_BG_MAIN)
        shell.pack(fill=tk.BOTH, expand=True)

        # Left sidebar
        sidebar = tk.Frame(shell, bg=_BG_SIDEBAR, width=250)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        # 1px vertical hairline divider
        tk.Frame(shell, bg=_BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y)

        side_inner = tk.Frame(sidebar, bg=_BG_SIDEBAR, padx=14, pady=14)
        side_inner.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            side_inner,
            text="PROJECTS",
            bg=_BG_SIDEBAR,
            fg=_MUTED,
            font=(_UI_FONT, 8, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 8))

        side_btns = tk.Frame(side_inner, bg=_BG_SIDEBAR)
        side_btns.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(side_btns, text="+ Add", style="Secondary.TButton", command=self._add_project).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(side_btns, text="Edit", style="Secondary.TButton", command=self._edit_project).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(side_btns, text="Remove", style="Secondary.TButton", command=self._remove_project).pack(
            side=tk.LEFT
        )

        list_border = tk.Frame(side_inner, bg=_BORDER, padx=1, pady=1)
        list_border.pack(fill=tk.BOTH, expand=True)
        self._project_list = tk.Listbox(
            list_border,
            bg=_BG_MAIN,
            fg=_TEXT,
            selectbackground=_SEL_BG,
            selectforeground=_TEXT,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            activestyle="none",
            font=(_UI_FONT, 9),
        )
        self._project_list.pack(fill=tk.BOTH, expand=True)
        self._project_list.bind("<<ListboxSelect>>", self._on_select_project)
        self._project_list.bind("<Double-Button-1>", lambda _e: self._start_scan())

        tk.Label(
            side_inner,
            text="Enabled projects check in the\nbackground and notify on issues.",
            bg=_BG_SIDEBAR,
            fg=_MUTED,
            font=(_UI_FONT, 8),
            justify=tk.LEFT,
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(10, 0))

        # Right main workspace
        main = tk.Frame(shell, bg=_BG_MAIN, padx=18, pady=14)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Top target bar
        toolbar = tk.Frame(main, bg=_BG_MAIN)
        toolbar.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            toolbar,
            text="Target",
            bg=_BG_MAIN,
            fg=_TEXT,
            font=(_UI_FONT, 9, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 8))

        entry_border = tk.Frame(toolbar, bg=_BORDER, padx=1, pady=1)
        entry_border.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        path_entry = tk.Entry(
            entry_border,
            textvariable=self._target_var,
            bg=_BG_MAIN,
            fg=_TEXT,
            relief=tk.FLAT,
            font=(_MONO_FONT, 9),
            highlightthickness=0,
        )
        path_entry.pack(fill=tk.X, ipady=5, padx=6)

        ttk.Button(toolbar, text="Browse...", style="Secondary.TButton", command=self._choose_folder).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self._scan_btn = ttk.Button(toolbar, text="Scan", style="Primary.TButton", command=self._start_scan)
        self._scan_btn.pack(side=tk.LEFT)

        # Compact 4-card summary strip
        cards_row = tk.Frame(main, bg=_BG_MAIN)
        cards_row.pack(fill=tk.X, pady=(0, 12))
        self._score_val_lbl = self._make_metric_card(cards_row, "SLOP SCORE", self._score_val_var, padx=(0, 8))
        self._errors_val_lbl = self._make_metric_card(cards_row, "ERRORS", self._errors_val_var, padx=(0, 8))
        self._warnings_val_lbl = self._make_metric_card(cards_row, "WARNINGS", self._warnings_val_var, padx=(0, 8))
        self._vibe_val_lbl = self._make_metric_card(cards_row, "VIBE SIGNAL", self._vibe_val_var, padx=(0, 0))

        # Filter & actions row
        options = tk.Frame(main, bg=_BG_MAIN)
        options.pack(fill=tk.X, pady=(0, 8))

        ttk.Checkbutton(
            options,
            text="Include test files",
            variable=self._include_tests_var,
            style="White.TCheckbutton",
        ).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(options, text="Severity:", bg=_BG_MAIN, fg=_MUTED, font=("Segoe UI", 9)).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        filter_box = ttk.Combobox(
            options,
            textvariable=self._severity_filter_var,
            values=("all", "error", "warning", "info"),
            state="readonly",
            width=10,
        )
        filter_box.pack(side=tk.LEFT)
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_table())

        self._export_btn = ttk.Button(
            options,
            text="Export JSON...",
            style="Secondary.TButton",
            command=self._export_json,
            state=tk.DISABLED,
        )
        self._export_btn.pack(side=tk.RIGHT)

        self._prompt_btn = ttk.Button(
            options,
            text="Fix Prompt...",
            style="Secondary.TButton",
            command=self._show_fix_prompt,
            state=tk.DISABLED,
        )
        self._prompt_btn.pack(side=tk.RIGHT, padx=(0, 6))

        # Findings table inside 1px border
        table_border = tk.Frame(main, bg=_BORDER, padx=1, pady=1)
        table_border.pack(fill=tk.BOTH, expand=True)

        table_frame = tk.Frame(table_border, bg=_BG_MAIN)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("severity", "rule", "location", "message")
        self._tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self._tree.heading("severity", text="Severity")
        self._tree.heading("rule", text="Rule")
        self._tree.heading("location", text="File : Line")
        self._tree.heading("message", text="Message")

        self._tree.column("severity", width=80, anchor=tk.CENTER, stretch=False)
        self._tree.column("rule", width=70, anchor=tk.CENTER, stretch=False)
        self._tree.column("location", width=240, anchor=tk.W, stretch=False)
        self._tree.column("message", width=480, anchor=tk.W, stretch=True)

        self._tree.tag_configure("odd", background=_BG_SUBTLE)
        self._tree.tag_configure("even", background=_BG_MAIN)
        self._tree.tag_configure("error", foreground=_ERR)
        self._tree.tag_configure("warning", foreground=_WARN)
        self._tree.tag_configure("info", foreground=_PRIMARY)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=y_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # Details card
        detail_border = tk.Frame(main, bg=_BORDER, padx=1, pady=1)
        detail_border.pack(fill=tk.X, pady=(10, 0))
        details = tk.Frame(detail_border, bg=_BG_SUBTLE, padx=10, pady=8)
        details.pack(fill=tk.X)

        self._snippet_var = tk.StringVar(value="Select a row to inspect the code snippet.")
        self._hint_var = tk.StringVar(value="")
        tk.Label(
            details,
            textvariable=self._snippet_var,
            bg=_BG_SUBTLE,
            fg=_TEXT,
            font=(_MONO_FONT, 9),
            anchor=tk.W,
        ).pack(fill=tk.X)
        tk.Label(
            details,
            textvariable=self._hint_var,
            bg=_BG_SUBTLE,
            fg=_MUTED,
            font=(_UI_FONT, 9),
            anchor=tk.W,
        ).pack(fill=tk.X)

        # Bottom status bar
        tk.Frame(self.root, bg=_BORDER, height=1).pack(fill=tk.X, side=tk.BOTTOM)
        status_bar = tk.Frame(self.root, bg=_BG_SIDEBAR, padx=14, pady=4)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        tk.Label(
            status_bar,
            textvariable=self._status_var,
            bg=_BG_SIDEBAR,
            fg=_MUTED,
            font=(_UI_FONT, 8),
            anchor=tk.W,
        ).pack(side=tk.LEFT)

    def _make_metric_card(
        self,
        parent: tk.Frame,
        title: str,
        var: tk.StringVar,
        *,
        padx: tuple[int, int],
    ) -> tk.Label:
        border = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
        border.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=padx)
        card = tk.Frame(border, bg=_BG_CARD, padx=12, pady=8)
        card.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            card,
            text=title,
            bg=_BG_CARD,
            fg=_MUTED,
            font=(_UI_FONT, 8, "bold"),
            anchor=tk.W,
        ).pack(fill=tk.X)
        val_lbl = tk.Label(
            card,
            textvariable=var,
            bg=_BG_CARD,
            fg=_TEXT,
            font=(_UI_FONT, 13, "bold"),
            anchor=tk.W,
        )
        val_lbl.pack(fill=tk.X)
        return val_lbl

    def _refresh_sidebar(self) -> None:
        self._project_list.delete(0, tk.END)
        for p in self._projects:
            tag = f"{p.interval_minutes}m" if (p.enabled and p.interval_minutes > 0) else "manual"
            score = f"  ({p.last_score}/100)" if p.last_score is not None else ""
            self._project_list.insert(tk.END, f" {p.name}  [{tag}]{score}")

    def _add_project(self) -> None:
        dlg = _ProjectDialog(self.root)
        if dlg.result is None:
            return
        self._projects.append(dlg.result)
        save_projects(self._projects)
        self._refresh_sidebar()

    def _edit_project(self) -> None:
        sel = self._project_list.curselection()
        if not sel:
            return
        idx = sel[0]
        dlg = _ProjectDialog(self.root, existing=self._projects[idx])
        if dlg.result is None:
            return
        dlg.result.last_score = self._projects[idx].last_score
        dlg.result.last_checked = self._projects[idx].last_checked
        self._projects[idx] = dlg.result
        save_projects(self._projects)
        self._refresh_sidebar()

    def _remove_project(self) -> None:
        sel = self._project_list.curselection()
        if not sel:
            return
        idx = sel[0]
        del self._projects[idx]
        self._last_run_ts.pop(idx, None)
        save_projects(self._projects)
        self._refresh_sidebar()

    def _on_select_project(self, _event: object) -> None:
        sel = self._project_list.curselection()
        if not sel:
            return
        proj = self._projects[sel[0]]
        self._target_var.set(proj.target)
        self._include_tests_var.set(proj.include_tests)

    def _choose_folder(self) -> None:
        current = self._target_var.get().strip()
        initial = current if current and Path(current).exists() else str(Path.cwd())
        selected = filedialog.askdirectory(initialdir=initial)
        if selected:
            self._target_var.set(selected)

    def _start_scan(self) -> None:
        from vibecheck.remote import parse_github_target

        target_str = self._target_var.get().strip()
        if not target_str:
            messagebox.showerror("Invalid target", "Enter a local folder or GitHub repository URL.")
            return
        if parse_github_target(target_str) is None and not Path(target_str).expanduser().exists():
            messagebox.showerror("Invalid path", f"Path does not exist:\n{target_str}")
            return

        self._scan_btn.configure(state=tk.DISABLED)
        self._export_btn.configure(state=tk.DISABLED)
        self._prompt_btn.configure(state=tk.DISABLED)
        self._status_var.set(f"Scanning {target_str}...")
        self._snippet_var.set("")
        self._hint_var.set("")

        cancel_event = threading.Event()
        dialog = _ScanProgressDialog(self.root, cancel_event)
        include_tests = self._include_tests_var.get()

        def on_progress(current: int, total: int, rel_path: Path) -> None:
            self.root.after(0, lambda c=current, t=total, p=rel_path: dialog.update_progress(c, t, p))

        def on_status(msg: str) -> None:
            self.root.after(0, lambda m=msg: dialog.set_status(m))

        def worker() -> None:
            try:
                result = _execute_scan(
                    target_str,
                    include_tests=include_tests,
                    progress_callback=on_progress,
                    cancel_event=cancel_event,
                    status_callback=on_status,
                )
                self.root.after(0, lambda: self._on_scan_complete(result, dialog, target_str))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): self._on_scan_error(err, dialog))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_error(self, message: str, dialog: _ScanProgressDialog) -> None:
        dialog.close()
        self._scan_btn.configure(state=tk.NORMAL)
        self._status_var.set("Scan failed")
        messagebox.showerror("Scan error", message)

    def _on_scan_complete(
        self,
        result: ScanResult,
        dialog: _ScanProgressDialog,
        target_str: str,
    ) -> None:
        dialog.close()
        self._last_result = result
        self._scan_btn.configure(state=tk.NORMAL)
        self._export_btn.configure(state=tk.NORMAL)
        self._prompt_btn.configure(state=tk.NORMAL if result.findings else tk.DISABLED)

        score = result.slop_score
        counts = result.counts_by_severity()

        self._score_val_var.set(f"{score}/100")
        self._errors_val_var.set(str(counts[Severity.ERROR]))
        self._warnings_val_var.set(str(counts[Severity.WARNING]))
        self._vibe_val_var.set(result.vibe_likelihood)

        self._score_val_lbl.configure(fg=_OK if score == 0 else (_WARN if score <= 25 else _ERR))
        self._errors_val_lbl.configure(fg=_ERR if counts[Severity.ERROR] > 0 else _TEXT)
        self._warnings_val_lbl.configure(fg=_WARN if counts[Severity.WARNING] > 0 else _TEXT)
        self._vibe_val_lbl.configure(
            fg=_ERR if result.vibe_likelihood == "High" else (_WARN if result.vibe_likelihood == "Medium" else _OK)
        )

        now_str = datetime.now().strftime("%H:%M")
        updated = False
        for proj in self._projects:
            if proj.target == target_str:
                proj.last_score = score
                proj.last_checked = now_str
                updated = True
        if updated:
            save_projects(self._projects)
            self._refresh_sidebar()

        status = (
            f"Scanned {result.files_scanned} file(s) ({result.files_skipped} skipped)"
        )
        if score > 25:
            status += "  |  High score: click 'Fix Prompt...' to copy a repair prompt."
        if result.errors:
            status += f"  |  {result.errors[0]}"
        self._status_var.set(status)
        self._refresh_table()

    def _scheduler_tick(self) -> None:
        now = time.monotonic()
        for idx, proj in enumerate(self._projects):
            if not proj.enabled or proj.interval_minutes <= 0 or idx in self._bg_running:
                continue
            last = self._last_run_ts.get(idx, 0.0)
            if now - last >= proj.interval_minutes * 60:
                self._bg_running.add(idx)
                self._last_run_ts[idx] = now
                threading.Thread(
                    target=self._run_background_project_scan,
                    args=(idx, proj),
                    daemon=True,
                ).start()

        self.root.after(30_000, self._scheduler_tick)

    def _run_background_project_scan(self, idx: int, proj: ProjectProfile) -> None:
        try:
            result = _execute_scan(proj.target, include_tests=proj.include_tests)
            self.root.after(0, lambda r=result: self._on_background_complete(idx, r))
        except OSError:
            self.root.after(0, lambda: self._bg_running.discard(idx))

    def _on_background_complete(self, idx: int, result: ScanResult) -> None:
        self._bg_running.discard(idx)
        if idx >= len(self._projects):
            return
        proj = self._projects[idx]
        proj.last_score = result.slop_score
        proj.last_checked = datetime.now().strftime("%H:%M")
        save_projects(self._projects)
        self._refresh_sidebar()

        if result.findings:
            send_notification(
                f"vibecheck: {proj.name}",
                f"Slop Score {result.slop_score}/100 ({len(result.findings)} issue(s), Vibe Signal: {result.vibe_likelihood}).",
            )

    def _refresh_table(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._findings_by_iid.clear()

        if self._last_result is None:
            return

        sev_filter = self._severity_filter_var.get()
        row_idx = 0
        for idx, finding in enumerate(self._last_result.findings):
            if sev_filter != "all" and finding.severity.value != sev_filter:
                continue
            try:
                rel = finding.path.relative_to(self._last_result.root).as_posix()
            except ValueError:
                rel = finding.path.as_posix()
            iid = f"f_{idx}"
            self._findings_by_iid[iid] = finding
            stripe = "odd" if row_idx % 2 else "even"
            self._tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    finding.severity.value.upper(),
                    finding.rule_id,
                    f"{rel}:{finding.line}",
                    finding.message,
                ),
                tags=(stripe, finding.severity.value),
            )
            row_idx += 1

    def _on_select_row(self, _event: object) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        finding = self._findings_by_iid.get(sel[0])
        if finding is None:
            return
        self._snippet_var.set(finding.snippet or "(no snippet)")
        self._hint_var.set(f"Hint: {finding.hint}" if finding.hint else "")

    def _export_json(self) -> None:
        if self._last_result is None:
            return
        from vibecheck.formatters import format_json

        dest = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="vibecheck-report.json",
        )
        if not dest:
            return
        Path(dest).write_text(format_json(self._last_result), encoding="utf-8")
        self._status_var.set(f"Exported report to {dest}")

    def _show_fix_prompt(self) -> None:
        if self._last_result is None or not self._last_result.findings:
            return
        from vibecheck.formatters import format_fix_prompt

        _FixPromptDialog(self.root, format_fix_prompt(self._last_result))


def launch_gui(initial_dir: Path | None = None) -> int:
    """Start the Tkinter GUI application."""
    _enable_high_dpi()
    root = tk.Tk()
    VibecheckApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0
