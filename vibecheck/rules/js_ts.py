"""Lexical and structural static analysis rules for JavaScript and TypeScript."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from vibecheck.models import Finding, Severity
from vibecheck.rules.base import get_line_snippet

_COMMENT_PLACEHOLDER_RE = re.compile(
    r"(?-i:\b(?:TODO|FIXME|XXX|HACK|PLACEHOLDER)\b)"
    r"|implement\s+(?:this|here|later|me)\b"
    r"|add\s+your\s+\w+\s+here\b"
    r"|rest\s+of\s+(?:the\s+)?(?:implementation|code|logic)\b"
    r"|replace\s+with\s+(?:actual|real)\b"
    r"|for\s+brevity\b",
    re.IGNORECASE,
)

_EMPTY_CATCH_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{(?P<body>[^}]*)\}",
    re.DOTALL,
)

_FN_STUB_RE = re.compile(
    r"(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\((?P<params>[^)]*)\)\s*(?::[^{=]+)?\{(?P<body>[^}]*)\}",
    re.DOTALL,
)

_IMPORT_RE = re.compile(
    r"""(?:^|\n)\s*import\s+(?:[\s\S]*?\s+from\s+)?['"](?P<pkg>[^'"]+)['"]"""
    r"""|(?:^|\n)\s*(?:const|let|var)\s+[\w{}\s,*:]+\s*=\s*require\(\s*['"](?P<req>[^'"]+)['"]\s*\)""",
)

_MOCK_DECL_RE = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>(?:mock|dummy|fake)[A-Z_][A-Za-z0-9_]*)\s*=",
)

_FAKE_SECRET_RE = re.compile(
    r"""['"](?:your[-_]?api[-_]?key|replace[-_]?with|changeme|sk-[x0-9a-z]{4,}|dummy[-_]?token)['"]""",
    re.IGNORECASE,
)

_NODE_BUILTINS = frozenset(
    {
        "assert", "buffer", "child_process", "cluster", "console", "crypto",
        "dgram", "dns", "domain", "events", "fs", "http", "http2", "https",
        "inspector", "module", "net", "os", "path", "perf_hooks", "process",
        "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
        "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm",
        "worker_threads", "zlib",
    }
)


@dataclass(frozen=True, slots=True)
class ProjectJsContext:
    """Declared npm/yarn/pnpm dependencies from `package.json`."""

    has_manifest: bool
    declared_packages: frozenset[str]


def build_js_context(root: Path) -> ProjectJsContext:
    """Read declared dependencies from `package.json` if present."""
    pkg_json = root / "package.json"
    if not pkg_json.is_file():
        return ProjectJsContext(has_manifest=False, declared_packages=frozenset())

    declared: set[str] = set()
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8", errors="ignore"))
        if isinstance(data, dict):
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                deps = data.get(section, {})
                if isinstance(deps, dict):
                    declared.update(str(k) for k in deps.keys())
    except (OSError, json.JSONDecodeError):
        pass

    return ProjectJsContext(has_manifest=True, declared_packages=frozenset(declared))


def _line_at_offset(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _extract_comments(source: str) -> list[tuple[int, str]]:
    """Extract `(line_no, comment_text)` pairs while ignoring string literals."""
    comments: list[tuple[int, str]] = []
    i = 0
    n = len(source)
    line = 1
    in_string: str | None = None

    while i < n:
        ch = source[i]
        if ch == "\n":
            line += 1
            if in_string in {"'", '"'}:
                in_string = None
            i += 1
            continue

        if in_string is not None:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue

        if ch in {"'", '"', "`"}:
            in_string = ch
            i += 1
            continue

        if ch == "/" and i + 1 < n:
            nxt = source[i + 1]
            if nxt == "/":
                end = source.find("\n", i + 2)
                if end == -1:
                    end = n
                comments.append((line, source[i + 2 : end].strip()))
                i = end
                continue
            if nxt == "*":
                end = source.find("*/", i + 2)
                if end == -1:
                    end = n
                block = source[i + 2 : end]
                comments.append((line, block.strip()))
                line += block.count("\n")
                i = end + 2
                continue

        i += 1
    return comments


def _package_root(specifier: str) -> str:
    if specifier.startswith("@"):
        parts = specifier.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else specifier
    return specifier.split("/")[0]


def analyze_js_ts_file(
    path: Path,
    source: str,
    *,
    is_test: bool,
    js_ctx: ProjectJsContext,
) -> list[Finding]:
    """Analyze a JavaScript or TypeScript file and return findings."""
    findings: list[Finding] = []
    lines = source.splitlines()

    # 1. Comments (VC002)
    for line_no, comment in _extract_comments(source):
        if "vibecheck:" in comment.lower():
            continue
        if _COMMENT_PLACEHOLDER_RE.search(comment):
            findings.append(
                Finding(
                    rule_id="VC002",
                    severity=Severity.WARNING,
                    path=path,
                    line=line_no,
                    column=1,
                    message=f"Placeholder comment left in code: {comment[:70]}",
                    snippet=get_line_snippet(lines, line_no),
                    hint="Complete the implementation or remove stale AI instructions.",
                )
            )

    # 2. Empty or print-only catch blocks (VC004)
    for match in _EMPTY_CATCH_RE.finditer(source):
        body = re.sub(r"//.*|/\*[\s\S]*?\*/", "", match.group("body")).strip()
        is_noop = body == "" or re.fullmatch(r"console\.(?:log|warn|error)\([^;]*\);?", body) is not None
        if is_noop:
            line_no = _line_at_offset(source, match.start())
            findings.append(
                Finding(
                    rule_id="VC004",
                    severity=Severity.ERROR,
                    path=path,
                    line=line_no,
                    column=1,
                    message="Catch block silently swallows or only logs the error.",
                    snippet=get_line_snippet(lines, line_no),
                    hint="Handle the error explicitly or rethrow it.",
                )
            )

    # 3. Stub functions (VC001)
    if not is_test:
        for match in _FN_STUB_RE.finditer(source):
            name = match.group("name")
            params = match.group("params").strip()
            body = re.sub(r"//.*|/\*[\s\S]*?\*/", "", match.group("body")).strip()
            line_no = _line_at_offset(source, match.start())

            if body == "" or "Not implemented" in body or "not implemented" in body:
                findings.append(
                    Finding(
                        rule_id="VC001",
                        severity=Severity.ERROR,
                        path=path,
                        line=line_no,
                        column=1,
                        message=f"Phantom stub function '{name}' is empty or throws Not Implemented.",
                        snippet=get_line_snippet(lines, line_no),
                    )
                )
            elif params and re.fullmatch(r"return\s+(?:null|undefined|\[\]|\{\})\s*;?", body):
                findings.append(
                    Finding(
                        rule_id="VC001",
                        severity=Severity.ERROR,
                        path=path,
                        line=line_no,
                        column=1,
                        message=f"Stub-like function '{name}' returns an empty literal without using parameters.",
                        snippet=get_line_snippet(lines, line_no),
                    )
                )

    # 4. Unverified imports (VC005)
    if js_ctx.has_manifest:
        for match in _IMPORT_RE.finditer(source):
            spec = match.group("pkg") or match.group("req") or ""
            if not spec or spec.startswith((".", "/", "@/", "~/")):
                continue
            if spec.startswith("node:"):
                continue
            pkg = _package_root(spec)
            if pkg in _NODE_BUILTINS or pkg in js_ctx.declared_packages:
                continue
            line_no = _line_at_offset(source, match.start())
            findings.append(
                Finding(
                    rule_id="VC005",
                    severity=Severity.ERROR,
                    path=path,
                    line=line_no,
                    column=1,
                    message=f"Package '{pkg}' is not declared in package.json.",
                    snippet=get_line_snippet(lines, line_no),
                    hint="Verify the package exists and install it via npm/pnpm/yarn.",
                )
            )

    # 5. Mock data & placeholder secrets in non-test source (VC006)
    if not is_test:
        for match in _MOCK_DECL_RE.finditer(source):
            line_no = _line_at_offset(source, match.start())
            findings.append(
                Finding(
                    rule_id="VC006",
                    severity=Severity.WARNING,
                    path=path,
                    line=line_no,
                    column=1,
                    message=f"Mock/dummy variable '{match.group('name')}' found in non-test source.",
                    snippet=get_line_snippet(lines, line_no),
                )
            )
        for match in _FAKE_SECRET_RE.finditer(source):
            line_no = _line_at_offset(source, match.start())
            findings.append(
                Finding(
                    rule_id="VC006",
                    severity=Severity.WARNING,
                    path=path,
                    line=line_no,
                    column=1,
                    message="Placeholder credential or API key string left in source code.",
                    snippet=get_line_snippet(lines, line_no),
                )
            )

    return findings
