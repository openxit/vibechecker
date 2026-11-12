"""Native Tkinter desktop GUI for vibecheck."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from vibecheck import __version__
from vibecheck.config import load_config
from vibecheck.formatters import format_fix_prompt, format_json
from vibecheck.models import Finding, ScanResult, Severity
from vibecheck.scanner import scan_path


class _ScanProgressDialog:
    """Modal progress popup displaying current file, count, and progress bar."""

    def __init__(self, parent: tk.Tk, cancel_event: threading.Event) -> None:
        self._cancel_event = cancel_event
        self.win = tk.Toplevel(parent)
        self.win.title("Scanning...")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()
        self.win.protocol("WM_DELETE_WINDOW", self._on_cancel)

        frame = ttk.Frame(self.win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        self._count_var = tk.StringVar(value="Discovering files...")
        self._file_var = tk.StringVar(value="")

        ttk.Label(frame, textvariable=self._count_var, font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, pady=(0, 6)
        )
        self._bar = ttk.Progressbar(frame, orient=tk.HORIZONTAL, length=380, mode="indeterminate")
        self._bar.pack(fill=tk.X, pady=(0, 6))
        self._bar.start(12)

        ttk.Label(frame, textvariable=self._file_var, width=54).pack(anchor=tk.W, pady=(0, 12))

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X)
        ttk.Button(btn_row, text="Cancel", command=self._on_cancel).pack(side=tk.RIGHT)

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
        if len(display) > 58:
            display = "..." + display[-55:]
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
    """Modal window displaying a generated AI repair prompt with clipboard copy."""

    def __init__(self, parent: tk.Tk, prompt_text: str) -> None:
        self._prompt_text = prompt_text
        self.win = tk.Toplevel(parent)
        self.win.title("AI Fix Prompt")
        self.win.geometry("720x480")
        self.win.minsize(520, 340)
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="Copy this prompt into your IDE / AI assistant to fix the detected issues:",
        ).pack(anchor=tk.W, pady=(0, 6))

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True)

        text_box = tk.Text(text_frame, wrap=tk.WORD, font=("Consolas", 9))
        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=text_box.yview)
        text_box.configure(yscrollcommand=y_scroll.set)
        text_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        text_box.insert("1.0", prompt_text)
        text_box.configure(state=tk.DISABLED)

        btn_row = ttk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        self._copied_var = tk.StringVar(value="")
        ttk.Label(btn_row, textvariable=self._copied_var, foreground="#15803d").pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Close", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Save .txt...", command=self._save_txt).pack(side=tk.RIGHT, padx=(0, 6))
        ttk.Button(btn_row, text="Copy to Clipboard", command=self._copy).pack(side=tk.RIGHT, padx=(0, 6))

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


class VibecheckApp:
    """Native desktop window for running vibecheck scans."""

    def __init__(self, root: tk.Tk, initial_dir: Path | None = None) -> None:
        self.root = root
        self.root.title(f"vibecheck {__version__}")
        self.root.geometry("960x620")
        self.root.minsize(740, 460)

        self._last_result: ScanResult | None = None
        self._findings_by_iid: dict[str, Finding] = {}
        self._target_var = tk.StringVar(value=str(initial_dir or Path.cwd()))
        self._include_tests_var = tk.BooleanVar(value=False)
        self._severity_filter_var = tk.StringVar(value="all")
        self._status_var = tk.StringVar(value="Ready")
        self._score_var = tk.StringVar(value="Score: --/100")

        self._configure_theme()
        self._build_layout()

    def _configure_theme(self) -> None:
        style = ttk.Style(self.root)
        available = set(style.theme_names())
        if "vista" in available:
            style.theme_use("vista")
        elif "winnative" in available:
            style.theme_use("winnative")
        else:
            style.theme_use("clam")

        style.configure("Treeview", rowheight=23, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))

    def _build_layout(self) -> None:
        main = ttk.Frame(self.root, padding=(10, 10, 10, 4))
        main.pack(fill=tk.BOTH, expand=True)

        # Toolbar: target path + browse + scan
        toolbar = ttk.Frame(main)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(toolbar, text="Folder:").pack(side=tk.LEFT, padx=(0, 6))
        path_entry = ttk.Entry(toolbar, textvariable=self._target_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(toolbar, text="Browse...", command=self._choose_folder).pack(side=tk.LEFT, padx=(0, 6))
        self._scan_btn = ttk.Button(toolbar, text="Scan", command=self._start_scan)
        self._scan_btn.pack(side=tk.LEFT)

        # Filter / options row
        options = ttk.Frame(main)
        options.pack(fill=tk.X, pady=(0, 8))

        ttk.Checkbutton(
            options,
            text="Include test files",
            variable=self._include_tests_var,
        ).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(options, text="Severity:").pack(side=tk.LEFT, padx=(0, 4))
        filter_box = ttk.Combobox(
            options,
            textvariable=self._severity_filter_var,
            values=("all", "error", "warning", "info"),
            state="readonly",
            width=10,
        )
        filter_box.pack(side=tk.LEFT)
        filter_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_table())

        self._export_btn = ttk.Button(options, text="Export JSON...", command=self._export_json, state=tk.DISABLED)
        self._export_btn.pack(side=tk.RIGHT)

        self._prompt_btn = ttk.Button(options, text="Fix Prompt...", command=self._show_fix_prompt, state=tk.DISABLED)
        self._prompt_btn.pack(side=tk.RIGHT, padx=(0, 6))

        self._score_label = ttk.Label(options, textvariable=self._score_var, font=("Segoe UI", 9, "bold"))
        self._score_label.pack(side=tk.RIGHT, padx=(0, 12))

        # Findings table
        table_frame = ttk.Frame(main)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("severity", "rule", "location", "message")
        self._tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        self._tree.heading("severity", text="Severity")
        self._tree.heading("rule", text="Rule")
        self._tree.heading("location", text="File : Line")
        self._tree.heading("message", text="Message")

        self._tree.column("severity", width=75, anchor=tk.CENTER, stretch=False)
        self._tree.column("rule", width=65, anchor=tk.CENTER, stretch=False)
        self._tree.column("location", width=230, anchor=tk.W, stretch=False)
        self._tree.column("message", width=520, anchor=tk.W, stretch=True)

        self._tree.tag_configure("odd", background="#f8fafc")
        self._tree.tag_configure("even", background="#ffffff")
        self._tree.tag_configure("error", foreground="#b91c1c")
        self._tree.tag_configure("warning", foreground="#92400e")
        self._tree.tag_configure("info", foreground="#1d4ed8")

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=y_scroll.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._on_select_row)

        # Details box
        details = ttk.LabelFrame(main, text="Details", padding=6)
        details.pack(fill=tk.X, pady=(8, 4))
        self._snippet_var = tk.StringVar(value="Select a row to inspect the code snippet.")
        self._hint_var = tk.StringVar(value="")
        ttk.Label(details, textvariable=self._snippet_var, font=("Consolas", 9)).pack(anchor=tk.W)
        ttk.Label(details, textvariable=self._hint_var, font=("Segoe UI", 9)).pack(anchor=tk.W)

        # Status bar
        status_bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding=(8, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(status_bar, textvariable=self._status_var, font=("Segoe UI", 9)).pack(side=tk.LEFT)

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
        self._prompt_btn.configure(state=tk.DISABLED)
        self._status_var.set(f"Scanning {target}...")
        self._snippet_var.set("")
        self._hint_var.set("")

        cancel_event = threading.Event()
        dialog = _ScanProgressDialog(self.root, cancel_event)
        include_tests = self._include_tests_var.get()

        def on_progress(current: int, total: int, rel_path: Path) -> None:
            self.root.after(0, lambda c=current, t=total, p=rel_path: dialog.update_progress(c, t, p))

        def worker() -> None:
            try:
                config = load_config(target, include_tests=include_tests)
                result = scan_path(
                    target,
                    config,
                    progress_callback=on_progress,
                    cancel_event=cancel_event,
                )
                self.root.after(0, lambda: self._on_scan_complete(result, dialog))
            except Exception as exc:
                self.root.after(0, lambda err=str(exc): self._on_scan_error(err, dialog))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_error(self, message: str, dialog: _ScanProgressDialog) -> None:
        dialog.close()
        self._scan_btn.configure(state=tk.NORMAL)
        self._status_var.set("Scan failed")
        messagebox.showerror("Scan error", message)

    def _on_scan_complete(self, result: ScanResult, dialog: _ScanProgressDialog) -> None:
        dialog.close()
        self._last_result = result
        self._scan_btn.configure(state=tk.NORMAL)
        self._export_btn.configure(state=tk.NORMAL)
        self._prompt_btn.configure(state=tk.NORMAL if result.findings else tk.DISABLED)

        score = result.slop_score
        self._score_var.set(f"Slop Score: {score}/100")
        if score == 0:
            self._score_label.configure(foreground="#15803d")
        elif score <= 25:
            self._score_label.configure(foreground="#b45309")
        else:
            self._score_label.configure(foreground="#b91c1c")

        counts = result.counts_by_severity()
        status = (
            f"Scanned {result.files_scanned} file(s) ({result.files_skipped} skipped) | "
            f"{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), {counts[Severity.INFO]} info"
        )
        if score > 25:
            status += " | High score: click 'Fix Prompt...' to copy an AI repair prompt."
        if result.errors:
            status += f" | {result.errors[0]}"
        self._status_var.set(status)
        self._refresh_table()

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
        _FixPromptDialog(self.root, format_fix_prompt(self._last_result))


def launch_gui(initial_dir: Path | None = None) -> int:
    """Start the Tkinter GUI application."""
    root = tk.Tk()
    VibecheckApp(root, initial_dir=initial_dir)
    root.mainloop()
    return 0
