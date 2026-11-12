"""Native Tkinter desktop GUI for vibecheck (launched when started without CLI args)."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from vibecheck import __version__
from vibecheck.config import load_config
from vibecheck.formatters import format_json
from vibecheck.models import Finding, ScanResult, Severity
from vibecheck.scanner import scan_path

_BG = "#0f172a"
_CARD = "#1e293b"
_FG = "#f8fafc"
_MUTED = "#94a3b8"
_ACCENT = "#38bdf8"
_ERR = "#f87171"
_WARN = "#fbbf24"
_OK = "#34d399"


class VibecheckApp:
    """Desktop GUI for running vibecheck scans without a terminal window."""

    def __init__(self, root: tk.Tk, initial_dir: Path | None = None) -> None:
        self.root = root
        self.root.title(f"vibecheck {__version__}")
        self.root.geometry("980x640")
        self.root.minsize(760, 480)
        self.root.configure(bg=_BG)

        self._last_result: ScanResult | None = None
        self._findings_by_iid: dict[str, Finding] = {}
        self._target_var = tk.StringVar(value=str(initial_dir or Path.cwd()))
        self._include_tests_var = tk.BooleanVar(value=False)
        self._severity_filter_var = tk.StringVar(value="all")
        self._status_var = tk.StringVar(value="Select a project folder and click Scan.")
        self._score_var = tk.StringVar(value="Slop Score: --/100")

        self._configure_styles()
        self._build_layout()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=_BG)
        style.configure("Card.TFrame", background=_CARD)
        style.configure("TLabel", background=_BG, foreground=_FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", background=_BG, foreground=_FG, font=("Segoe UI", 15, "bold"))
        style.configure("Muted.TLabel", background=_BG, foreground=_MUTED, font=("Segoe UI", 9))
        style.configure("Card.TLabel", background=_CARD, foreground=_FG, font=("Segoe UI", 10))
        style.configure(
            "TButton",
            background=_CARD,
            foreground=_FG,
            borderwidth=0,
            padding=(12, 6),
            font=("Segoe UI", 10),
        )
        style.map("TButton", background=[("active", "#334155")])
        style.configure(
            "Accent.TButton",
            background=_ACCENT,
            foreground="#0f172a",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 6),
        )
        style.map("Accent.TButton", background=[("active", "#7dd3fc")])
        style.configure(
            "TCheckbutton",
            background=_BG,
            foreground=_FG,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview",
            background=_CARD,
            fieldbackground=_CARD,
            foreground=_FG,
            rowheight=26,
            borderwidth=0,
            font=("Segoe UI", 9),
        )
        style.configure(
            "Treeview.Heading",
            background="#334155",
            foreground=_FG,
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Treeview", background=[("selected", "#334155")])

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, padding=16)
        container.pack(fill=tk.BOTH, expand=True)

        # Header
        header = ttk.Frame(container)
        header.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(header, text="vibecheck", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(
            header,
            text="  Static analyzer for AI-generated phantom stubs, cloned logic & hallucinated imports",
            style="Muted.TLabel",
        ).pack(side=tk.LEFT, pady=(4, 0))
        self._score_label = tk.Label(
            header,
            textvariable=self._score_var,
            bg=_CARD,
            fg=_FG,
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=4,
        )
        self._score_label.pack(side=tk.RIGHT)

        # Path picker row
        picker = ttk.Frame(container)
        picker.pack(fill=tk.X, pady=(0, 10))
        path_entry = tk.Entry(
            picker,
            textvariable=self._target_var,
            bg=_CARD,
            fg=_FG,
            insertbackground=_FG,
            relief=tk.FLAT,
            font=("Consolas", 10),
        )
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6, padx=(0, 8))
        ttk.Button(picker, text="Browse...", command=self._choose_folder).pack(side=tk.LEFT, padx=(0, 8))
        self._scan_btn = ttk.Button(picker, text="Scan", style="Accent.TButton", command=self._start_scan)
        self._scan_btn.pack(side=tk.LEFT)

        # Options / Filter bar
        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(
            controls,
            text="Include test files",
            variable=self._include_tests_var,
        ).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(controls, text="Filter:", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        filter_box = ttk.Combobox(
            controls,
            textvariable=self._severity_filter_var,
            values=("all", "error", "warning", "info"),
            state="readonly",
            width=10,
        )
        filter_box.pack(side=tk.LEFT)
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_table())

        self._export_btn = ttk.Button(controls, text="Export JSON", command=self._export_json, state=tk.DISABLED)
        self._export_btn.pack(side=tk.RIGHT)

        # Findings table
        table_frame = ttk.Frame(container)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("severity", "rule", "location", "message")
        self._tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self._tree.heading("severity", text="Severity")
        self._tree.heading("rule", text="Rule")
        self._tree.heading("location", text="File : Line")
        self._tree.heading("message", text="Message")

        self._tree.column("severity", width=80, anchor=tk.CENTER, stretch=False)
        self._tree.column("rule", width=70, anchor=tk.CENTER, stretch=False)
        self._tree.column("location", width=230, anchor=tk.W, stretch=False)
        self._tree.column("message", width=520, anchor=tk.W, stretch=True)

        self._tree.tag_configure("error", foreground=_ERR)
        self._tree.tag_configure("warning", foreground=_WARN)
        self._tree.tag_configure("info", foreground=_ACCENT)

        scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # Detail preview box
        self._detail_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self._detail_var, style="Muted.TLabel", wraplength=920).pack(
            fill=tk.X, pady=(8, 4)
        )

        # Status bar
        ttk.Label(container, textvariable=self._status_var, style="Muted.TLabel").pack(fill=tk.X)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(initialdir=self._target_var.get() or str(Path.cwd()))
        if selected:
            self._target_var.set(selected)

    def _start_scan(self) -> None:
        target = Path(self._target_var.get()).expanduser()
        if not target.exists():
            messagebox.showerror("Invalid path", f"Path does not exist:\n{target}")
            return

        self._scan_btn.configure(state=tk.DISABLED)
        self._export_btn.configure(state=tk.DISABLED)
        self._status_var.set(f"Scanning {target} ...")
        self._detail_var.set("")

        include_tests = self._include_tests_var.get()

        def worker() -> None:
            try:
                config = load_config(target, include_tests=include_tests)
                result = scan_path(target, config)
                self.root.after(0, lambda: self._on_scan_complete(result))
            except Exception as exc:
                self.root.after(0, lambda: self._on_scan_error(str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_error(self, message: str) -> None:
        self._scan_btn.configure(state=tk.NORMAL)
        self._status_var.set("Scan failed.")
        messagebox.showerror("Scan error", message)

    def _on_scan_complete(self, result: ScanResult) -> None:
        self._last_result = result
        self._scan_btn.configure(state=tk.NORMAL)
        self._export_btn.configure(state=tk.NORMAL)

        score = result.slop_score
        self._score_var.set(f"Slop Score: {score}/100")
        if score == 0:
            self._score_label.configure(fg=_OK)
        elif score <= 25:
            self._score_label.configure(fg=_WARN)
        else:
            self._score_label.configure(fg=_ERR)

        counts = result.counts_by_severity()
        self._status_var.set(
            f"Scanned {result.files_scanned} file(s) ({result.files_skipped} skipped) | "
            f"{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), {counts[Severity.INFO]} info"
        )
        self._refresh_table()

    def _refresh_table(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._findings_by_iid.clear()

        if self._last_result is None:
            return

        sev_filter = self._severity_filter_var.get()
        for idx, finding in enumerate(self._last_result.findings):
            if sev_filter != "all" and finding.severity.value != sev_filter:
                continue
            try:
                rel = finding.path.relative_to(self._last_result.root).as_posix()
            except ValueError:
                rel = finding.path.as_posix()
            iid = f"f_{idx}"
            self._findings_by_iid[iid] = finding
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
                tags=(finding.severity.value,),
            )

    def _on_select_row(self, _event: object) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        finding = self._findings_by_iid.get(sel[0])
        if finding is None:
            return
        parts = []
        if finding.snippet:
            parts.append(f"Code: {finding.snippet}")
        if finding.hint:
            parts.append(f"Hint: {finding.hint}")
        self._detail_var.set("  |  ".join(parts))

    def _export_json(self) -> None:
        if self._last_result is None:
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files", "*.json")],
            initialfile="vibecheck-report.json",
        )
        if not dest:
            return
        Path(dest).write_text(format_json(self._last_result), encoding="utf-8")
        self._status_var.set(f"Exported report to {dest}")


def launch_gui(initial_dir: Path | None = None) -> int:
    """Start the Tkinter GUI application."""
    root = tk.Tk()
    VibecheckApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0
