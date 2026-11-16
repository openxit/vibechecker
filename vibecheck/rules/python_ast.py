"""AST- and token-based static analysis rules for Python source files."""

from __future__ import annotations

import ast
import hashlib
import io
import re
import sys
import tokenize
import tomllib
from dataclasses import dataclass
from pathlib import Path

from vibecheck.models import Finding, Severity
from vibecheck.rules.base import get_line_snippet

_PLACEHOLDER_COMMENT_RE = re.compile(
    r"(?-i:\b(?:TODO|FIXME|XXX|HACK|PLACEHOLDER)\b)"
    r"|implement\s+(?:this|here|later|me)\b"
    r"|add\s+your\s+\w+\s+here\b"
    r"|rest\s+of\s+(?:the\s+)?(?:implementation|code|logic)\b"
    r"|replace\s+with\s+(?:actual|real)\b"
    r"|for\s+brevity\b",
    re.IGNORECASE,
)

_DUMMY_STRING_RE = re.compile(
    r"^(?:todo|placeholder|not\s+implemented|dummy|mock(?:ed)?(?:\s+\w+)?)$",
    re.IGNORECASE,
)

_FAKE_SECRET_RE = re.compile(
    r"(?:your[-_]?api[-_]?key|replace[-_]?with|changeme|sk-[x0-9a-z]{4,}|dummy[-_]?token)",
    re.IGNORECASE,
)

_MOCK_VAR_RE = re.compile(r"^(?:mock|dummy|fake)_[a-z0-9_]+$", re.IGNORECASE)

_NARRATING_COMMENT_RE = re.compile(
    r"^(?:initialize|create|set|define|loop\s+through|iterate\s+over|return\s+the|check\s+if|increment|decrement|import\s+(?:the\s+)?necessary)\b",
    re.IGNORECASE,
)

_BOILERPLATE_PLACEHOLDER_RE = re.compile(
    r"\b(?:john\s+doe|jane\s+doe|lorem\s+ipsum|acme\s+corp|foo@bar\.com)\b|https?://(?:www\.)?example\.com\b",
    re.IGNORECASE,
)

# Common PyPI distribution name -> top-level import name mappings.
_PACKAGE_IMPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "pillow": ("PIL",),
    "beautifulsoup4": ("bs4",),
    "pyyaml": ("yaml",),
    "scikit-learn": ("sklearn",),
    "python-dotenv": ("dotenv",),
    "pyjwt": ("jwt",),
    "opencv-python": ("cv2",),
    "opencv-python-headless": ("cv2",),
    "python-dateutil": ("dateutil",),
    "attrs": ("attr", "attrs"),
    "msgpack-python": ("msgpack",),
    "psycopg2-binary": ("psycopg2",),
    "pycryptodome": ("Crypto",),
    "pymupdf": ("fitz",),
}


@dataclass(frozen=True, slots=True)
class FunctionFingerprint:
    """Normalized structural signature of a Python function for clone detection."""

    digest: str
    name: str
    path: Path
    line: int
    snippet: str


@dataclass(frozen=True, slots=True)
class TopLevelFunction:
    """Top-level function metadata used for cross-file dead-helper detection."""

    name: str
    path: Path
    line: int
    snippet: str


@dataclass(frozen=True, slots=True)
class PythonFileAnalysis:
    """Result of analyzing a single Python file."""

    findings: list[Finding]
    fingerprints: list[FunctionFingerprint]
    top_level_functions: list[TopLevelFunction]
    referenced_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class ProjectPythonContext:
    """Project-level dependency and local module metadata for Python import checks."""

    has_manifest: bool
    declared_imports: frozenset[str]
    local_modules: frozenset[str]


def _normalize_pkg_name(raw: str) -> str:
    """Extract normalized distribution name from a PEP 508 requirement string."""
    match = re.match(r"^\s*([A-Za-z0-9._-]+)", raw)
    if not match:
        return ""
    return match.group(1).lower().replace("-", "_")


def build_python_context(root: Path) -> ProjectPythonContext:
    """Discover declared dependencies and local Python modules under *root*."""
    declared: set[str] = set()
    has_manifest = False

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        has_manifest = True
        try:
            with pyproject.open("rb") as fh:
                data = tomllib.load(fh)
            project = data.get("project", {})
            deps: list[str] = []
            if isinstance(project, dict):
                deps.extend(str(d) for d in project.get("dependencies", ()) if isinstance(d, str))
                opt = project.get("optional-dependencies", {})
                if isinstance(opt, dict):
                    for group in opt.values():
                        if isinstance(group, list):
                            deps.extend(str(d) for d in group if isinstance(d, str))
            for dep in deps:
                norm = _normalize_pkg_name(dep)
                if norm:
                    declared.add(norm)
                    for alias in _PACKAGE_IMPORT_ALIASES.get(norm.replace("_", "-"), ()):
                        declared.add(alias.lower())
        except (OSError, tomllib.TOMLDecodeError):
            pass

    for req_file in root.glob("requirements*.txt"):
        has_manifest = True
        try:
            for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-")):
                    continue
                norm = _normalize_pkg_name(stripped)
                if norm:
                    declared.add(norm)
                    for alias in _PACKAGE_IMPORT_ALIASES.get(norm.replace("_", "-"), ()):
                        declared.add(alias.lower())
        except OSError:
            pass

    local_modules: set[str] = set()
    search_roots = [root]
    src_dir = root / "src"
    if src_dir.is_dir():
        search_roots.append(src_dir)

    for base in search_roots:
        try:
            for entry in base.iterdir():
                if entry.is_dir() and (entry / "__init__.py").is_file():
                    local_modules.add(entry.name.lower())
                elif entry.is_file() and entry.suffix == ".py":
                    local_modules.add(entry.stem.lower())
        except OSError:
            continue

    return ProjectPythonContext(
        has_manifest=has_manifest,
        declared_imports=frozenset(declared),
        local_modules=frozenset(local_modules),
    )


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Return *body* without a leading string-literal docstring."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _decorator_name(dec: ast.expr) -> str:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        return dec.attr
    if isinstance(dec, ast.Call):
        return _decorator_name(dec.func)
    return ""


def _is_protocol_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    return False


def _is_not_implemented_raise(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    exc = stmt.exc
    if isinstance(exc, ast.Call):
        exc = exc.func
    return isinstance(exc, ast.Name) and exc.id in {"NotImplementedError", "NotImplemented"}


def _is_empty_or_mock_return(stmt: ast.stmt, param_count: int) -> str | None:
    if not isinstance(stmt, ast.Return):
        return None
    val = stmt.value
    if val is None or (isinstance(val, ast.Constant) and val.value is None):
        return "returns None" if param_count > 0 else None
    if isinstance(val, ast.Constant) and isinstance(val.value, str):
        if _DUMMY_STRING_RE.match(val.value.strip()):
            return f"returns placeholder string {val.value!r}"
        return None
    if param_count > 0 and isinstance(val, (ast.List, ast.Dict)) and len(val.elts if isinstance(val, ast.List) else val.keys) == 0:
        return "returns empty collection literal"
    return None


class _AstNormalizer(ast.NodeTransformer):
    """Canonicalize variable and function names to detect structurally identical clones."""

    def __init__(self) -> None:
        self._mapping: dict[str, str] = {}

    def _canon(self, original: str) -> str:
        if original not in self._mapping:
            self._mapping[original] = f"_v{len(self._mapping)}"
        return self._mapping[original]

    def _normalize_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        node.name = "_fn"
        node.decorator_list = []
        node.body = _strip_docstring(node.body)
        return self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._normalize_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._normalize_fn(node)

    def visit_arg(self, node: ast.arg) -> ast.AST:
        if node.arg not in {"self", "cls"}:
            node.arg = self._canon(node.arg)
        node.annotation = None
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        # Preserve builtin/call names only in Load context? Keeping local bindings
        # canonicalized while leaving attribute names intact avoids over-matching.
        if isinstance(node.ctx, ast.Store):
            node.id = self._canon(node.id)
        elif node.id in self._mapping:
            node.id = self._mapping[node.id]
        return node


def _compute_fingerprint(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: Path,
    lines: list[str],
) -> FunctionFingerprint | None:
    effective_body = _strip_docstring(list(node.body))
    if len(effective_body) < 4:
        return None
    # Clone the AST subtree safely via compile/re-parse of unparsed node or deep copy.
    import copy

    cloned = copy.deepcopy(node)
    normalized = _AstNormalizer().visit(cloned)
    dumped = ast.dump(normalized, include_attributes=False)
    digest = hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]
    return FunctionFingerprint(
        digest=digest,
        name=node.name,
        path=path,
        line=node.lineno,
        snippet=get_line_snippet(lines, node.lineno),
    )


def _is_broad_exception(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in {"Exception", "BaseException"}
    return False


def _is_swallowed_body(body: list[ast.stmt]) -> bool:
    stmts = _strip_docstring(body)
    if not stmts:
        return True
    for stmt in stmts:
        if isinstance(stmt, ast.Pass):
            continue
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
            continue
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "print"
        ):
            continue
        return False
    return True


def _collect_ignored_import_lines(tree: ast.Module) -> set[int]:
    """Return line numbers inside `if TYPE_CHECKING:` or `try: ... except ImportError:`."""
    ignored: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        ignored.add(child.lineno)
        elif isinstance(node, ast.Try):
            has_import_err = any(
                isinstance(h.type, ast.Name) and h.type.id in {"ImportError", "ModuleNotFoundError"}
                for h in node.handlers
            )
            if has_import_err:
                for child in node.body:
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        ignored.add(child.lineno)
    return ignored


def analyze_python_file(
    path: Path,
    source: str,
    *,
    is_test: bool,
    py_ctx: ProjectPythonContext,
) -> PythonFileAnalysis:
    """Analyze a single Python file and return findings plus cross-file metadata."""
    findings: list[Finding] = []
    fingerprints: list[FunctionFingerprint] = []
    top_level_functions: list[TopLevelFunction] = []
    referenced_names: set[str] = set()
    lines = source.splitlines()

    # 1. Comment scan via tokenize (never matches inside string literals).
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for tok in tokens:
            if tok.type == tokenize.COMMENT:
                comment_text = tok.string.lstrip("#").strip()
                if "vibecheck:" in comment_text.lower():
                    continue
                line_no = tok.start[0]
                if _PLACEHOLDER_COMMENT_RE.search(comment_text):
                    findings.append(
                        Finding(
                            rule_id="VC002",
                            severity=Severity.WARNING,
                            path=path,
                            line=line_no,
                            column=tok.start[1] + 1,
                            message=f"Placeholder comment left in code: {comment_text[:70]}",
                            snippet=get_line_snippet(lines, line_no),
                            hint="Complete the implementation or remove stale placeholder comments.",
                        )
                    )
                elif _NARRATING_COMMENT_RE.match(comment_text) and len(comment_text) <= 60:
                    findings.append(
                        Finding(
                            rule_id="VC007",
                            severity=Severity.INFO,
                            path=path,
                            line=line_no,
                            column=tok.start[1] + 1,
                            message=f"Narrating comment restates obvious code: {comment_text[:70]}",
                            snippet=get_line_snippet(lines, line_no),
                            hint="Remove redundant 'what' comments; explain 'why' only when non-obvious.",
                        )
                    )
    except tokenize.TokenError:
        pass

    # 2. AST structural analysis.
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        findings.append(
            Finding(
                rule_id="VC001",
                severity=Severity.ERROR,
                path=path,
                line=exc.lineno or 1,
                column=exc.offset or 1,
                message=f"SyntaxError prevents analysis: {exc.msg}",
                snippet=get_line_snippet(lines, exc.lineno or 1),
            )
        )
        return PythonFileAnalysis(
            findings=findings,
            fingerprints=fingerprints,
            top_level_functions=top_level_functions,
            referenced_names=frozenset(),
        )

    # Track parent class to skip Protocol methods.
    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent

    ignored_import_lines = _collect_ignored_import_lines(tree)
    stdlib_modules = {m.lower() for m in sys.stdlib_module_names}

    # Collect __all__ exports so intentionally exported helpers are not flagged by VC008.
    exported_names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "__all__" and isinstance(stmt.value, (ast.List, ast.Tuple)):
                    for elt in stmt.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported_names.add(elt.value)

    for stmt in tree.body:
        if (
            not is_test
            and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not stmt.decorator_list
            and not stmt.name.startswith(("__", "test_"))
            and stmt.name not in {"main"}
            and stmt.name not in exported_names
        ):
            top_level_functions.append(
                TopLevelFunction(
                    name=stmt.name,
                    path=path,
                    line=stmt.lineno,
                    snippet=get_line_snippet(lines, stmt.lineno),
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)
        elif isinstance(node, ast.alias):
            referenced_names.add(node.asname or node.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                referenced_names.add(alias.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parent = parent_map.get(id(node))
            if isinstance(parent, ast.ClassDef) and _is_protocol_class(parent):
                continue

            decorators = {_decorator_name(d) for d in node.decorator_list}
            if decorators & {"abstractmethod", "overload"}:
                continue

            fp = _compute_fingerprint(node, path, lines)
            if fp is not None:
                fingerprints.append(fp)

            if is_test:
                continue

            body = _strip_docstring(node.body)
            is_dunder = node.name.startswith("__") and node.name.endswith("__")
            non_self_params = [
                a.arg for a in node.args.args if a.arg not in {"self", "cls"}
            ] + [a.arg for a in node.args.kwonlyargs]

            if len(body) == 1:
                only_stmt = body[0]
                is_pass_or_ellipsis = isinstance(only_stmt, ast.Pass) or (
                    isinstance(only_stmt, ast.Expr)
                    and isinstance(only_stmt.value, ast.Constant)
                    and only_stmt.value.value is Ellipsis
                )
                if is_pass_or_ellipsis and not is_dunder:
                    findings.append(
                        Finding(
                            rule_id="VC001",
                            severity=Severity.ERROR,
                            path=path,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            message=f"Phantom stub function '{node.name}' has an empty body.",
                            snippet=get_line_snippet(lines, node.lineno),
                            hint="Implement the function or mark abstract methods with @abstractmethod.",
                        )
                    )
                elif _is_not_implemented_raise(only_stmt) and not is_dunder:
                    findings.append(
                        Finding(
                            rule_id="VC001",
                            severity=Severity.ERROR,
                            path=path,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            message=f"Function '{node.name}' raises NotImplementedError.",
                            snippet=get_line_snippet(lines, node.lineno),
                        )
                    )
                else:
                    reason = _is_empty_or_mock_return(only_stmt, len(non_self_params))
                    if reason and not is_dunder:
                        findings.append(
                            Finding(
                                rule_id="VC001",
                                severity=Severity.ERROR,
                                path=path,
                                line=node.lineno,
                                column=node.col_offset + 1,
                                message=f"Stub-like function '{node.name}' {reason} without using parameters.",
                                snippet=get_line_snippet(lines, node.lineno),
                            )
                        )

        elif isinstance(node, ast.ExceptHandler):
            if _is_broad_exception(node) and _is_swallowed_body(node.body):
                findings.append(
                    Finding(
                        rule_id="VC004",
                        severity=Severity.ERROR,
                        path=path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        message="Broad exception handler silently swallows errors.",
                        snippet=get_line_snippet(lines, node.lineno),
                        hint="Catch a specific exception, log with context, or re-raise.",
                    )
                )

        elif isinstance(node, (ast.Import, ast.ImportFrom)) and py_ctx.has_manifest:
            if node.lineno in ignored_import_lines:
                continue
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                continue
            if isinstance(node, ast.Import):
                top_names = [alias.name.split(".")[0] for alias in node.names]
            else:
                top_names = [node.module.split(".")[0]] if node.module else []

            for mod_name in top_names:
                lower = mod_name.lower()
                if (
                    lower in stdlib_modules
                    or lower in py_ctx.local_modules
                    or lower in py_ctx.declared_imports
                ):
                    continue
                findings.append(
                    Finding(
                        rule_id="VC005",
                        severity=Severity.ERROR,
                        path=path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        message=f"Import '{mod_name}' is not in stdlib, local modules, or project dependencies.",
                        snippet=get_line_snippet(lines, node.lineno),
                        hint="Verify the package name wasn't hallucinated and add it to pyproject.toml / requirements.txt.",
                    )
                )

        elif not is_test and isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and _MOCK_VAR_RE.match(target.id):
                    findings.append(
                        Finding(
                            rule_id="VC006",
                            severity=Severity.WARNING,
                            path=path,
                            line=node.lineno,
                            column=node.col_offset + 1,
                            message=f"Mock/dummy variable '{target.id}' found in non-test source file.",
                            snippet=get_line_snippet(lines, node.lineno),
                            hint="Move test fixtures to tests/ or replace with real data loading.",
                        )
                    )

        elif not is_test and isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if len(val) <= 120 and "(?:" not in val and _FAKE_SECRET_RE.search(val):
                findings.append(
                    Finding(
                        rule_id="VC006",
                        severity=Severity.WARNING,
                        path=path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        message="Placeholder credential or API key string left in source code.",
                        snippet=get_line_snippet(lines, node.lineno),
                        hint="Load credentials from environment variables or a secret manager.",
                    )
                )
            elif len(val) <= 160 and "(?:" not in val and _BOILERPLATE_PLACEHOLDER_RE.search(val):
                findings.append(
                    Finding(
                        rule_id="VC009",
                        severity=Severity.WARNING,
                        path=path,
                        line=node.lineno,
                        column=node.col_offset + 1,
                        message="Generated boilerplate placeholder string left in non-test source.",
                        snippet=get_line_snippet(lines, node.lineno),
                        hint="Replace example/dummy entity strings with real configuration or test fixtures.",
                    )
                )

    return PythonFileAnalysis(
        findings=findings,
        fingerprints=fingerprints,
        top_level_functions=top_level_functions,
        referenced_names=frozenset(referenced_names),
    )
