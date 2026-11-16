"""Core data models for vibecheck."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Severity(str, Enum):
    """Finding severity levels ordered from lowest to highest impact."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"

    @property
    def weight(self) -> int:
        """Numeric penalty weight used when computing the repository vibe score."""
        return {
            Severity.INFO: 2,
            Severity.WARNING: 5,
            Severity.ERROR: 12,
        }[self]

    @property
    def rank(self) -> int:
        """Ordering rank for threshold comparisons."""
        return {
            Severity.INFO: 0,
            Severity.WARNING: 1,
            Severity.ERROR: 2,
        }[self]


@dataclass(frozen=True, slots=True)
class RuleMeta:
    """Metadata describing a single static analysis rule."""

    rule_id: str
    name: str
    default_severity: Severity
    summary: str


@dataclass(frozen=True, slots=True)
class Finding:
    """A single static analysis finding in a source file."""

    rule_id: str
    severity: Severity
    path: Path
    line: int
    column: int
    message: str
    snippet: str = ""
    hint: str = ""


@dataclass(frozen=True, slots=True)
class Config:
    """Runtime configuration resolved from CLI flags and config files."""

    root: Path
    include: tuple[str, ...] = ()
    ignore: tuple[str, ...] = (
        ".git/**",
        ".venv/**",
        "venv/**",
        "node_modules/**",
        "__pycache__/**",
        "dist/**",
        "build/**",
        ".tox/**",
        ".mypy_cache/**",
        ".pytest_cache/**",
        ".ruff_cache/**",
    )
    disabled_rules: frozenset[str] = frozenset()
    fail_on: Severity | None = None
    max_score: int | None = None
    include_tests: bool = False


@dataclass(slots=True)
class ScanResult:
    """Aggregated result of scanning a target path."""

    root: Path
    files_scanned: int = 0
    files_skipped: int = 0
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def slop_score(self) -> int:
        """Return a bounded 0-100 score indicating AI-slop density (0 = clean)."""
        if self.files_scanned == 0 or not self.findings:
            return 0
        raw_penalty = sum(f.severity.weight for f in self.findings)
        # Normalize against project size so large clean repos are not penalized
        # by a single minor finding, while small repos with many issues spike fast.
        normalizer = max(1.0, self.files_scanned * 0.6)
        normalized = round((raw_penalty / normalizer) * 10)
        return min(100, normalized)

    @property
    def vibe_likelihood(self) -> str:
        """Heuristic likelihood ('Low', 'Medium', 'High') that code exhibits generated patterns."""
        if self.files_scanned == 0 or not self.findings:
            return "Low"
        signal_rules = {"VC001", "VC002", "VC003", "VC007", "VC008", "VC009"}
        signal_count = sum(1 for f in self.findings if f.rule_id in signal_rules)
        score = self.slop_score
        if score >= 30 or signal_count >= 5:
            return "High"
        if score >= 10 or signal_count >= 2:
            return "Medium"
        return "Low"

    def counts_by_severity(self) -> dict[Severity, int]:
        """Count findings grouped by severity."""
        counts = {Severity.ERROR: 0, Severity.WARNING: 0, Severity.INFO: 0}
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts
