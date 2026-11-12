"""Configuration discovery and loading for vibecheck."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from vibecheck.models import Config, Severity

DEFAULT_CONFIG_TEMPLATE = """\
# vibecheck configuration
# Disable specific rules globally:
# disable = ["VC002"]

# Additional glob patterns to ignore:
ignore = [
    ".git/**",
    ".venv/**",
    "node_modules/**",
    "dist/**",
    "build/**",
]

# Fail CI when findings meet or exceed this severity ("info", "warning", "error"):
# fail_on = "error"

# Fail CI when the repository slop score exceeds this threshold (0-100):
# max_score = 15
"""


def _read_toml_section(root: Path) -> dict[str, Any]:
    """Read vibecheck settings from `.vibecheck.toml` or `pyproject.toml`."""
    vibecheck_toml = root / ".vibecheck.toml"
    if vibecheck_toml.is_file():
        with vibecheck_toml.open("rb") as fh:
            data = tomllib.load(fh)
        if isinstance(data, dict):
            return data

    pyproject_toml = root / "pyproject.toml"
    if pyproject_toml.is_file():
        with pyproject_toml.open("rb") as fh:
            data = tomllib.load(fh)
        tool_section = data.get("tool", {})
        if isinstance(tool_section, dict):
            vc_section = tool_section.get("vibecheck", {})
            if isinstance(vc_section, dict):
                return vc_section

    return {}


def load_config(
    target: Path,
    *,
    disabled_rules: frozenset[str] = frozenset(),
    extra_ignore: tuple[str, ...] = (),
    fail_on: Severity | None = None,
    max_score: int | None = None,
    include_tests: bool = False,
) -> Config:
    """Resolve configuration from disk and merge CLI overrides."""
    root = target if target.is_dir() else target.parent
    raw = _read_toml_section(root)
    base = Config(root=root)

    file_ignore = tuple(str(x) for x in raw.get("ignore", ()) if isinstance(x, str))
    file_include = tuple(str(x) for x in raw.get("include", ()) if isinstance(x, str))
    file_disabled = frozenset(
        str(x).upper() for x in raw.get("disable", ()) if isinstance(x, str)
    )

    resolved_fail_on = fail_on
    if resolved_fail_on is None and isinstance(raw.get("fail_on"), str):
        resolved_fail_on = Severity(raw["fail_on"].lower())

    resolved_max_score = max_score
    if resolved_max_score is None and isinstance(raw.get("max_score"), int):
        resolved_max_score = raw["max_score"]

    resolved_include_tests = include_tests or bool(raw.get("include_tests", False))

    merged_ignore = base.ignore + file_ignore + extra_ignore
    merged_disabled = file_disabled | frozenset(r.upper() for r in disabled_rules)

    return Config(
        root=root,
        include=file_include,
        ignore=merged_ignore,
        disabled_rules=merged_disabled,
        fail_on=resolved_fail_on,
        max_score=resolved_max_score,
        include_tests=resolved_include_tests,
    )
