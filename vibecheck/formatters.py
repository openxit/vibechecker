"""Output formatters for terminal, JSON, and GitHub Actions annotations."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from vibecheck.models import Finding, ScanResult, Severity


class _Palette:
    """ANSI color helper that disables escapes when color output is off."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def wrap(self, code: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def bold(self, text: str) -> str:
        return self.wrap("1", text)

    def dim(self, text: str) -> str:
        return self.wrap("2", text)

    def red(self, text: str) -> str:
        return self.wrap("31", text)

    def yellow(self, text: str) -> str:
        return self.wrap("33", text)

    def green(self, text: str) -> str:
        return self.wrap("32", text)

    def cyan(self, text: str) -> str:
        return self.wrap("36", text)


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _severity_label(severity: Severity, pal: _Palette) -> str:
    if severity == Severity.ERROR:
        return pal.red("ERR ")
    if severity == Severity.WARNING:
        return pal.yellow("WARN")
    return pal.cyan("INFO")


def _score_bar(score: int, pal: _Palette) -> str:
    filled = round(score / 5)
    bar = "#" * filled + "-" * (20 - filled)
    if score == 0:
        return pal.green(f"[{bar}] {score}/100 (clean)")
    if score <= 25:
        return pal.yellow(f"[{bar}] {score}/100 (minor slop)")
    return pal.red(f"[{bar}] {score}/100 (high slop)")


def format_text(result: ScanResult, *, use_color: bool) -> str:
    """Format *result* as a human-readable terminal report."""
    pal = _Palette(enabled=use_color)
    out: list[str] = []

    by_file: dict[Path, list[Finding]] = defaultdict(list)
    for finding in result.findings:
        by_file[finding.path].append(finding)

    for file_path, findings in by_file.items():
        rel = _display_path(file_path, result.root)
        out.append(pal.bold(rel))
        for f in findings:
            loc = pal.dim(f"{f.line}:{f.column}")
            sev = _severity_label(f.severity, pal)
            rule = pal.dim(f"[{f.rule_id}]")
            out.append(f"  {loc:<12} {sev} {rule} {f.message}")
            if f.snippet:
                out.append(f"               {pal.dim('>')} {pal.dim(f.snippet)}")
            if f.hint:
                out.append(f"               {pal.dim('hint:')} {f.hint}")
        out.append("")

    for err in result.errors:
        out.append(pal.red(f"error: {err}"))

    counts = result.counts_by_severity()
    summary = (
        f"Scanned {result.files_scanned} file(s) "
        f"({result.files_skipped} skipped) | "
        f"{counts[Severity.ERROR]} error(s), "
        f"{counts[Severity.WARNING]} warning(s), "
        f"{counts[Severity.INFO]} info"
    )
    out.append(summary)
    out.append(f"Slop Score: {_score_bar(result.slop_score, pal)}")
    return "\n".join(out)


def format_json(result: ScanResult) -> str:
    """Format *result* as structured JSON."""
    counts = result.counts_by_severity()
    payload = {
        "root": result.root.as_posix(),
        "files_scanned": result.files_scanned,
        "files_skipped": result.files_skipped,
        "slop_score": result.slop_score,
        "summary": {sev.value: count for sev, count in counts.items()},
        "errors": result.errors,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "path": _display_path(f.path, result.root),
                "line": f.line,
                "column": f.column,
                "message": f.message,
                "snippet": f.snippet,
                "hint": f.hint,
            }
            for f in result.findings
        ],
    }
    return json.dumps(payload, indent=2)


def format_github(result: ScanResult) -> str:
    """Format *result* as GitHub Actions workflow commands."""
    lines: list[str] = []
    for f in result.findings:
        rel = _display_path(f.path, result.root)
        level = "error" if f.severity == Severity.ERROR else "warning"
        if f.severity == Severity.INFO:
            level = "notice"
        lines.append(
            f"::{level} file={rel},line={f.line},col={f.column},title={f.rule_id}::{f.message}"
        )
    return "\n".join(lines)
