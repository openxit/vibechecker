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
    out.append(f"Slop Score: {_score_bar(result.slop_score, pal)} | Vibe Signal: {result.vibe_likelihood}")
    return "\n".join(out)


def format_json(result: ScanResult) -> str:
    """Format *result* as structured JSON."""
    counts = result.counts_by_severity()
    payload = {
        "root": result.root.as_posix(),
        "files_scanned": result.files_scanned,
        "files_skipped": result.files_skipped,
        "slop_score": result.slop_score,
        "vibe_likelihood": result.vibe_likelihood,
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


_RULE_FIX_GUIDANCE: dict[str, str] = {
    "VC001": "Replace phantom stubs and dummy returns with real implementations (or @abstractmethod if intentionally abstract).",
    "VC002": "Resolve all placeholder/TODO/AI-deferral comments and complete the missing logic.",
    "VC003": "Extract duplicated functions into a single shared helper and reuse it.",
    "VC004": "Handle exceptions explicitly with context or re-raise instead of silently swallowing them.",
    "VC005": "Verify every flagged import: install/declare valid dependencies in pyproject.toml/package.json or remove hallucinated packages.",
    "VC006": "Remove mock/dummy variables and placeholder credentials from non-test source files; load secrets from environment variables.",
    "VC007": "Remove redundant narrating comments that only restate the next line of code.",
    "VC008": "Remove unused top-level helper functions or wire them into the active code path.",
    "VC009": "Replace generated placeholder names, sample domains, or filler text with real config or test fixtures.",
}


def format_fix_prompt(result: ScanResult) -> str:
    """Generate a ready-to-paste AI repair prompt for all findings in *result*."""
    if not result.findings:
        return "No issues found (Slop Score: 0/100)."

    by_file: dict[Path, list[Finding]] = defaultdict(list)
    seen_rules: dict[str, None] = {}
    for finding in result.findings:
        by_file[finding.path].append(finding)
        seen_rules[finding.rule_id] = None

    lines: list[str] = [
        f"Fix the following static analysis issues found by vibecheck (Slop Score: {result.slop_score}/100).",
        "Make minimal, production-grade changes without introducing new stubs, placeholders, or duplicated helpers.",
        "",
        "## Issues by file",
    ]

    for file_path, findings in by_file.items():
        rel = _display_path(file_path, result.root)
        lines.append(f"\n### {rel}")
        for f in findings:
            lines.append(f"- Line {f.line} [{f.rule_id} / {f.severity.value}]: {f.message}")
            if f.snippet:
                lines.append(f"  Code: `{f.snippet}`")
            if f.hint:
                lines.append(f"  Hint: {f.hint}")

    lines.append("\n## Required fix guidelines")
    for rule_id in seen_rules:
        guidance = _RULE_FIX_GUIDANCE.get(rule_id)
        if guidance:
            lines.append(f"- {rule_id}: {guidance}")

    lines.append("\nAfter fixing, ensure all existing tests pass and no new stub functions or TODOs remain.")
    return "\n".join(lines)
