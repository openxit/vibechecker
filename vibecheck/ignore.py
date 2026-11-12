"""Path filtering, test-file detection, and inline suppression parsing."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path, PurePosixPath

_INLINE_IGNORE_RE = re.compile(
    r"(?:#|//)\s*vibecheck:\s*ignore(?:\[([^\]]+)\])?",
    re.IGNORECASE,
)

_TEST_DIR_NAMES = frozenset({"test", "tests", "__tests__", "spec", "specs"})


def is_test_file(path: Path) -> bool:
    """Return True if *path* follows standard test file or directory conventions."""
    lower_parts = [part.lower() for part in path.parts]
    if any(part in _TEST_DIR_NAMES for part in lower_parts[:-1]):
        return True

    name = path.name.lower()
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".test.tsx", ".test.jsx"))
        or name.endswith((".spec.js", ".spec.ts", ".spec.tsx", ".spec.jsx"))
        or name == "conftest.py"
    )


def matches_any_glob(rel_path: Path, patterns: tuple[str, ...]) -> bool:
    """Check whether *rel_path* matches any glob pattern in *patterns*."""
    posix = PurePosixPath(rel_path.as_posix()).as_posix()
    parts = posix.split("/")

    for raw_pattern in patterns:
        pattern = raw_pattern.replace("\\", "/").lstrip("./")
        if not pattern:
            continue
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/")
            if posix == prefix or posix.startswith(prefix + "/"):
                return True
        if fnmatch.fnmatch(posix, pattern):
            return True
        if "/" not in pattern and any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def parse_inline_suppressions(source: str) -> dict[int, frozenset[str] | None]:
    """Map 1-based line numbers to suppressed rule IDs (None = suppress all rules)."""
    suppressions: dict[int, frozenset[str] | None] = {}
    for line_no, line in enumerate(source.splitlines(), start=1):
        match = _INLINE_IGNORE_RE.search(line)
        if not match:
            continue
        raw_rules = match.group(1)
        if not raw_rules:
            suppressions[line_no] = None
        else:
            rules = frozenset(
                token.strip().upper()
                for token in raw_rules.split(",")
                if token.strip()
            )
            suppressions[line_no] = rules
    return suppressions


def is_suppressed(
    suppressions: dict[int, frozenset[str] | None],
    line: int,
    rule_id: str,
) -> bool:
    """Return True if *rule_id* is suppressed on *line* or the preceding line."""
    for target_line in (line, line - 1):
        if target_line not in suppressions:
            continue
        rules = suppressions[target_line]
        if rules is None or rule_id.upper() in rules:
            return True
    return False
