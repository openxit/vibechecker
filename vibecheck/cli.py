"""Command-line interface for vibecheck."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from vibecheck import __version__
from vibecheck.config import DEFAULT_CONFIG_TEMPLATE, load_config
from vibecheck.formatters import format_github, format_json, format_text
from vibecheck.models import ScanResult, Severity
from vibecheck.rules import RULES
from vibecheck.scanner import scan_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibecheck",
        description="Static analyzer that catches AI-generated phantom stubs, cloned logic, and hallucinated imports.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    scan_p = subparsers.add_parser("scan", help="Scan a file or directory (default command).")
    _add_scan_args(scan_p)

    subparsers.add_parser("rules", help="List all available analysis rules.")

    init_p = subparsers.add_parser("init", help="Create a .vibecheck.toml config file.")
    init_p.add_argument(
        "path",
        nargs="?",
        default=".",
        help="Directory where .vibecheck.toml should be created (default: current directory).",
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing .vibecheck.toml file.",
    )

    return parser


def _add_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="File or directory to scan (default: current directory).",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json", "github"),
        default="text",
        help="Output format (default: text).",
    )
    parser.add_argument(
        "--disable",
        action="append",
        default=[],
        metavar="RULE_ID",
        help="Disable a rule ID (repeatable, e.g. --disable VC002).",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="GLOB",
        help="Additional glob pattern to ignore (repeatable).",
    )
    parser.add_argument(
        "--fail-on",
        choices=("info", "warning", "error"),
        default=None,
        help="Exit with code 1 if any finding meets or exceeds this severity.",
    )
    parser.add_argument(
        "--max-score",
        type=int,
        default=None,
        help="Exit with code 1 if the repository slop score exceeds this value (0-100).",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files in the scan.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output.",
    )


def _should_fail(result: ScanResult, fail_on: Severity | None, max_score: int | None) -> bool:
    if result.errors:
        return True
    if max_score is not None and result.slop_score > max_score:
        return True
    if fail_on is not None:
        return any(f.severity.rank >= fail_on.rank for f in result.findings)
    return False


def _cmd_rules() -> int:
    for rule in RULES.values():
        print(f"{rule.rule_id}  [{rule.default_severity.value:<7}]  {rule.name:<24}  {rule.summary}")
    return 0


def _cmd_init(target_dir: Path, force: bool) -> int:
    cfg_path = target_dir / ".vibecheck.toml"
    if cfg_path.exists() and not force:
        print(f"Refusing to overwrite existing {cfg_path} (pass --force to overwrite).", file=sys.stderr)
        return 1
    cfg_path.write_text(DEFAULT_CONFIG_TEMPLATE, encoding="utf-8")
    print(f"Created {cfg_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # Allow `vibecheck [path] [flags]` without requiring explicit `scan` subcommand.
    if not raw_args or raw_args[0] not in {"scan", "rules", "init", "-h", "--help", "--version"}:
        raw_args = ["scan", *raw_args]

    parser = _build_parser()
    args = parser.parse_args(raw_args)

    if args.command == "rules":
        return _cmd_rules()
    if args.command == "init":
        return _cmd_init(Path(args.path), args.force)

    target = Path(args.target).resolve()
    fail_on = Severity(args.fail_on) if args.fail_on else None
    config = load_config(
        target,
        disabled_rules=frozenset(r.upper() for r in args.disable),
        extra_ignore=tuple(args.ignore),
        fail_on=fail_on,
        max_score=args.max_score,
        include_tests=args.include_tests,
    )

    result = scan_path(target, config)

    use_color = (
        not args.no_color
        and sys.stdout.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )

    if args.format == "json":
        print(format_json(result))
    elif args.format == "github":
        output = format_github(result)
        if output:
            print(output)
    else:
        print(format_text(result, use_color=use_color))

    if _should_fail(result, config.fail_on, config.max_score):
        return 1
    return 0
