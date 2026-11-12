"""Rule registry and shared helper utilities."""

from __future__ import annotations

from vibecheck.models import RuleMeta, Severity

RULES: dict[str, RuleMeta] = {
    "VC001": RuleMeta(
        rule_id="VC001",
        name="phantom-stub",
        default_severity=Severity.ERROR,
        summary="Function or method is an unimplemented stub or returns hardcoded dummy data.",
    ),
    "VC002": RuleMeta(
        rule_id="VC002",
        name="ai-placeholder-comment",
        default_severity=Severity.WARNING,
        summary="Unfinished placeholder comment or LLM deferral marker (TODO/FIXME/implement here).",
    ),
    "VC003": RuleMeta(
        rule_id="VC003",
        name="cloned-logic",
        default_severity=Severity.WARNING,
        summary="Structurally identical function body duplicated under a different name.",
    ),
    "VC004": RuleMeta(
        rule_id="VC004",
        name="swallowed-exception",
        default_severity=Severity.ERROR,
        summary="Exception handler silently discards errors without re-raising or returning failure.",
    ),
    "VC005": RuleMeta(
        rule_id="VC005",
        name="unverified-import",
        default_severity=Severity.ERROR,
        summary="Import is not in standard library, local project modules, or declared dependencies.",
    ),
    "VC006": RuleMeta(
        rule_id="VC006",
        name="mock-data-in-source",
        default_severity=Severity.WARNING,
        summary="Hardcoded mock/dummy data or placeholder credential detected in non-test code.",
    ),
}


def get_line_snippet(lines: list[str], line_no: int) -> str:
    """Return a trimmed single-line snippet for 1-based *line_no*."""
    if 1 <= line_no <= len(lines):
        return lines[line_no - 1].strip()[:120]
    return ""
