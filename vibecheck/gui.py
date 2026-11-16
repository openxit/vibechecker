"""Native Tkinter desktop GUI for vibecheck."""

from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from vibecheck import __version__
from vibecheck.config import load_config
from vibecheck.formatters import format_fix_prompt, format_json
from vibecheck.models import Finding, ScanResult, Severity
from vibecheck.notify import send_windows_notification
from vibecheck.projects import ProjectProfile, load_projects, save_projects
from vibecheck.remote import parse_github_target, scan_github_repo
from vibecheck.scanner import scan_path

_INTERVAL_LABELS = {
    0: "Manual",
    5: "Every 5 min",
    15: "Every 15 min",
    30: "Every 30 min",
    60: "Every 60 min",
}


class _ScanProgressDialog:
    """Modal progress popup displaying current stage, file count, and progress bar."""

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
    """Modal window displaying a generated repair prompt with clipboard copy."""

    def __init__(self, parent: tk.Tk, prompt_text: str) -> None:
        self._prompt_text = prompt_text
        self.win = tk.Toplevel(parent)
        self.win.title("Repair Prompt")
        self.win.geometry("720x480")
        self.win.minsize(520, 340)
        self.win.transient(parent)

        frame = ttk.Frame(self.win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            frame,
            text="Copy this prompt into your editor or assistant to fix the detected issues:",
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


class _ProjectDialog:
    """Dialog to add or edit a monitored project."""

    def __init__(self, parent: tk.Tk, existing: ProjectProfile | None = None) -> None:
        self.result: ProjectProfile | None = None
        self.win = tk.Toplevel(parent)
        self.win.title("Edit Project" if existing else "Add Project")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        frame = ttk.Frame(self.win, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        self._name_var = tk.StringVar(value=existing.name if existing else "")
        self._target_var = tk.StringVar(value=existing.target if existing else "")
        self._interval_var = tk.StringVar(
            value=_INTERVAL_LABELS.get(existing.interval_minutes if existing else 30, "Every 30 min")
        )
        self._enabled_var = tk.BooleanVar(value=existing.enabled if existing else True)
        self._tests_var = tk.BooleanVar(value=existing.include_tests if existing else False)

        ttk.Label(frame, text="Name:").grid(row=0, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self._name_var, width=42).grid(
            row=0, column=1, columnspan=2, sticky=tk.EW, pady=4
        )

        ttk.Label(frame, text="Folder or GitHub URL:").grid(row=1, column=0, sticky=tk.W, pady=4)
        ttk.Entry(frame, textvariable=self._target_var, width=34).grid(
            row=1, column=1, sticky=tk.EW, pady=4, padx=(0, 6)
        )
        ttk.Button(frame, text="Browse...", command=self._browse).grid(row=1, column=2, pady=4)

        ttk.Label(frame, text="Auto-check:").grid(row=2, column=0, sticky=tk.W, pady=4)
        ttk.Combobox(
            frame,
            textvariable=self._interval_var,
            values=tuple(_INTERVAL_LABELS.values()),
            state="readonly",
            width=18,
        ).grid(row=2, column=1, sticky=tk.W, pady=4)

        ttk.Checkbutton(frame, text="Enable background monitor", variable=self._enabled_var).grid(
            row=3, column=1, sticky=tk.W, pady=2
        )
        ttk.Checkbutton(frame, text="Include test files", variable=self._tests_var).grid(
            row=4, column=1, sticky=tk.W, pady=2
        )

        btn_row = ttk.Frame(frame)
        btn_row.grid(row=5, column=0, columnspan=3, sticky=tk.E, pady=(12, 0))
        ttk.Button(btn_row, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="Save", command=self._save).pack(side=tk.RIGHT, padx=(0, 6))

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
    """Native desktop window for running vibecheck scans and scheduled checks."""

    def __init__(self, root: tk.Tk, initial_dir: Path | None = None) -> None:
        self.root = root
        self.root.title(f"vibecheck {__version__}")
        self.root.geometry("1120x660")
        self.root.minsize(840, 500)

        self._last_result: ScanResult | None = None
        self._findings_by_iid: dict[str, Finding] = {}
        self._projects: list[ProjectProfile] = load_projects()
        self._last_run_ts: dict[int, float] = {}
        self._bg_running: set[int] = set()

        self._target_var = tk.StringVar(value=str(initial_dir or Path.cwd()))
        self._include_tests_var = tk.BooleanVar(value=False)
        self._severity_filter_var = tk.StringVar(value="all")
        self._status_var = tk.StringVar(value="Ready")
        self._score_var = tk.StringVar(value="Score: --/100")
        self._vibe_var = tk.StringVar(value="Vibe Signal: --")

        self._configure_theme()
        self._build_layout()
        self._refresh_sidebar()
        self.root.after(15_000, self._scheduler_tick)

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
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 2))

        # Left sidebar: Projects & automation
        sidebar = ttk.Frame(paned, padding=(0, 0, 6, 0))
        paned.add(sidebar, weight=0)

        side_header = ttk.Frame(sidebar)
        side_header.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(side_header, text="Projects", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        side_btns = ttk.Frame(sidebar)
        side_btns.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(side_btns, text="+ Add", width=7, command=self._add_project).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(side_btns, text="Edit", width=6, command=self._edit_project).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(side_btns, text="Remove", width=8, command=self._remove_project).pack(side=tk.LEFT)

        self._project_list = tk.Listbox(sidebar, width=30, activestyle="none", font=("Segoe UI", 9))
        self._project_list.pack(fill=tk.BOTH, expand=True)
        self._project_list.bind("<<ListboxSelect>>", self._on_select_project)
        self._project_list.bind("<Double-Button-1>", lambda _e: self._start_scan())

        ttk.Label(
            sidebar,
            text="Enabled projects auto-scan in the\nbackground and notify only on issues.",
            font=("Segoe UI", 8),
        ).pack(anchor=tk.W, pady=(6, 0))

        # Right main pane
        main = ttk.Frame(paned, padding=(6, 0, 0, 0))
        paned.add(main, weight=1)

        toolbar = ttk.Frame(main)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(toolbar, text="Folder or GitHub URL:").pack(side=tk.LEFT, padx=(0, 6))
        path_entry = ttk.Entry(toolbar, textvariable=self._target_var)
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(toolbar, text="Browse...", command=self._choose_folder).pack(side=tk.LEFT, padx=(0, 6))
        self._scan_btn = ttk.Button(toolbar, text="Scan", command=self._start_scan)
        self._scan_btn.pack(side=tk.LEFT)

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

        self._vibe_label = ttk.Label(options, textvariable=self._vibe_var, font=("Segoe UI", 9))
        self._vibe_label.pack(side=tk.RIGHT, padx=(0, 10))

        self._score_label = ttk.Label(options, textvariable=self._score_var, font=("Segoe UI", 9, "bold"))
        self._score_label.pack(side=tk.RIGHT, padx=(0, 10))

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
        self._tree.column("message", width=500, anchor=tk.W, stretch=True)

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

        details = ttk.LabelFrame(main, text="Details", padding=6)
        details.pack(fill=tk.X, pady=(8, 4))
        self._snippet_var = tk.StringVar(value="Select a row to inspect the code snippet.")
        self._hint_var = tk.StringVar(value="")
        ttk.Label(details, textvariable=self._snippet_var, font=("Consolas", 9)).pack(anchor=tk.W)
        ttk.Label(details, textvariable=self._hint_var, font=("Segoe UI", 9)).pack(anchor=tk.W)

        status_bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding=(8, 2))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(status_bar, textvariable=self._status_var, font=("Segoe UI", 9)).pack(side=tk.LEFT)

    def _refresh_sidebar(self) -> None:
        self._project_list.delete(0, tk.END)
        for p in self._projects:
            tag = f"{p.interval_minutes}m" if (p.enabled and p.interval_minutes > 0) else "manual"
            score = f" [{p.last_score}/100]" if p.last_score is not None else ""
            self._project_list.insert(tk.END, f"[{tag}] {p.name}{score}")

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
        self._score_var.set(f"Slop Score: {score}/100")
        self._vibe_var.set(f"Vibe Signal: {result.vibe_likelihood}")
        if score == 0:
            self._score_label.configure(foreground="#15803d")
        elif score <= 25:
            self._score_label.configure(foreground="#b45309")
        else:
            self._score_label.configure(foreground="#b91c1c")

        # Update matching saved project score if present.
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

        counts = result.counts_by_severity()
        status = (
            f"Scanned {result.files_scanned} file(s) ({result.files_skipped} skipped) | "
            f"{counts[Severity.ERROR]} error(s), {counts[Severity.WARNING]} warning(s), {counts[Severity.INFO]} info"
        )
        if score > 25:
            status += " | High score: click 'Fix Prompt...' to copy a repair prompt."
        if result.errors:
            status += f" | {result.errors[0]}"
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

        # Silent when clean; notify only when issues are detected.
        if result.findings:
            send_windows_notification(
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
