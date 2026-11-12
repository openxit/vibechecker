"""Repository scanner and cross-file analysis orchestrator."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from vibecheck.ignore import (
    is_suppressed,
    is_test_file,
    matches_any_glob,
    parse_inline_suppressions,
)
from vibecheck.models import Config, Finding, ScanResult, Severity
from vibecheck.rules.js_ts import analyze_js_ts_file, build_js_context
from vibecheck.rules.python_ast import (
    FunctionFingerprint,
    analyze_python_file,
    build_python_context,
)

_PYTHON_EXTS = frozenset({".py"})
_JS_TS_EXTS = frozenset({".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"})
_SUPPORTED_EXTS = _PYTHON_EXTS | _JS_TS_EXTS


def _discover_files(target: Path, config: Config) -> list[Path]:
    """Collect supported source files under *target*, respecting ignore globs."""
    if target.is_file():
        return [target] if target.suffix.lower() in _SUPPORTED_EXTS else []

    collected: list[Path] = []
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        rel = path.relative_to(config.root)
        if matches_any_glob(rel, config.ignore):
            continue
        if config.include and not matches_any_glob(rel, config.include):
            continue
        collected.append(path)
    return collected


def scan_path(target: Path, config: Config) -> ScanResult:
    """Scan *target* (file or directory) and return a `ScanResult`."""
    resolved = target.resolve()
    result = ScanResult(root=config.root)

    if not resolved.exists():
        result.errors.append(f"Target path does not exist: {target}")
        return result

    files = _discover_files(resolved, config)
    py_ctx = build_python_context(config.root)
    js_ctx = build_js_context(config.root)

    all_fingerprints: list[FunctionFingerprint] = []
    file_suppressions: dict[Path, dict[int, frozenset[str] | None]] = {}
    raw_findings: list[Finding] = []

    for file_path in files:
        rel_path = file_path.relative_to(config.root) if file_path.is_relative_to(config.root) else file_path
        test_file = is_test_file(rel_path)
        if test_file and not config.include_tests:
            result.files_skipped += 1
            continue

        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            result.errors.append(f"Could not read {rel_path}: {exc}")
            result.files_skipped += 1
            continue

        result.files_scanned += 1
        suppressions = parse_inline_suppressions(source)
        file_suppressions[file_path] = suppressions

        ext = file_path.suffix.lower()
        if ext in _PYTHON_EXTS:
            file_findings, fps = analyze_python_file(
                file_path,
                source,
                is_test=test_file,
                py_ctx=py_ctx,
            )
            raw_findings.extend(file_findings)
            all_fingerprints.extend(fps)
        elif ext in _JS_TS_EXTS:
            file_findings = analyze_js_ts_file(
                file_path,
                source,
                is_test=test_file,
                js_ctx=js_ctx,
            )
            raw_findings.extend(file_findings)

    # Cross-file duplicate function detection (VC003)
    by_digest: dict[str, list[FunctionFingerprint]] = defaultdict(list)
    for fp in all_fingerprints:
        by_digest[fp.digest].append(fp)

    for clones in by_digest.values():
        if len(clones) < 2:
            continue
        canonical = clones[0]
        canon_rel = (
            canonical.path.relative_to(config.root)
            if canonical.path.is_relative_to(config.root)
            else canonical.path
        )
        for dup in clones[1:]:
            raw_findings.append(
                Finding(
                    rule_id="VC003",
                    severity=Severity.WARNING,
                    path=dup.path,
                    line=dup.line,
                    column=1,
                    message=(
                        f"Function '{dup.name}' is structurally identical to "
                        f"'{canonical.name}' at {canon_rel}:{canonical.line}."
                    ),
                    snippet=dup.snippet,
                    hint="Extract a shared helper instead of duplicating logic.",
                )
            )

    # Filter disabled rules and inline suppressions, then sort deterministically.
    filtered: list[Finding] = []
    for finding in raw_findings:
        if finding.rule_id.upper() in config.disabled_rules:
            continue
        sup = file_suppressions.get(finding.path, {})
        if is_suppressed(sup, finding.line, finding.rule_id):
            continue
        filtered.append(finding)

    filtered.sort(key=lambda f: (str(f.path), f.line, f.column, f.rule_id))
    result.findings = filtered
    return result
