"""Remote GitHub repository fetching and scanning without requiring git."""

from __future__ import annotations

import os
import re
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Event

from vibecheck.config import load_config
from vibecheck.models import ScanResult, Severity
from vibecheck.scanner import scan_path

_GITHUB_URL_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?(?:/tree/(?P<branch>[^/?#]+))?(?:/.*)?/?$",
    re.IGNORECASE,
)

_GITHUB_SHORTHAND_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)$"
)


@dataclass(frozen=True, slots=True)
class GitHubTarget:
    """Parsed GitHub repository reference."""

    owner: str
    repo: str
    branch: str | None = None

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def archive_url(self) -> str:
        base = f"https://api.github.com/repos/{self.owner}/{self.repo}/zipball"
        return f"{base}/{self.branch}" if self.branch else base


def parse_github_target(raw: str) -> GitHubTarget | None:
    """Parse a GitHub URL or `owner/repo` shorthand; return None for local paths."""
    cleaned = raw.strip()
    if not cleaned:
        return None

    match = _GITHUB_URL_RE.match(cleaned)
    if match:
        return GitHubTarget(
            owner=match.group("owner"),
            repo=match.group("repo"),
            branch=match.group("branch"),
        )

    # Only treat `owner/repo` as GitHub shorthand if it does not exist locally.
    if not Path(cleaned).exists():
        short = _GITHUB_SHORTHAND_RE.match(cleaned)
        if short:
            return GitHubTarget(
                owner=short.group("owner"),
                repo=short.group("repo"),
            )

    return None


def _safe_extract_zip(archive_path: Path, dest_dir: Path) -> Path:
    """Extract *archive_path* into *dest_dir* safely and return the repo root."""
    dest_resolved = dest_dir.resolve()
    with zipfile.ZipFile(archive_path, "r") as zf:
        for member in zf.infolist():
            member_path = (dest_resolved / member.filename).resolve()
            if not member_path.is_relative_to(dest_resolved):
                raise ValueError(f"Unsafe zip entry path: {member.filename}")
        zf.extractall(dest_resolved)

    subdirs = [p for p in dest_resolved.iterdir() if p.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]
    return dest_resolved


def scan_github_repo(
    gh_target: GitHubTarget,
    *,
    disabled_rules: frozenset[str] = frozenset(),
    extra_ignore: tuple[str, ...] = (),
    fail_on: Severity | None = None,
    max_score: int | None = None,
    include_tests: bool = False,
    progress_callback: Callable[[int, int, Path], None] | None = None,
    cancel_event: Event | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> ScanResult:
    """Download a GitHub repository archive into a temp directory and scan it."""
    if status_callback is not None:
        status_callback(f"Downloading {gh_target.slug}...")

    headers = {
        "User-Agent": "vibecheck-cli",
        "Accept": "application/vnd.github+json",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with tempfile.TemporaryDirectory(prefix="vibecheck_gh_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        zip_path = tmp_root / "repo.zip"
        req = urllib.request.Request(gh_target.archive_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                zip_path.write_bytes(resp.read())
        except urllib.error.HTTPError as exc:
            result = ScanResult(root=Path(gh_target.slug))
            if exc.code == 404:
                result.errors.append(
                    f"GitHub repository not found or private: {gh_target.slug}"
                )
            elif exc.code == 403:
                result.errors.append(
                    "GitHub API rate limit reached (set GITHUB_TOKEN to increase limits)."
                )
            else:
                result.errors.append(f"GitHub download failed (HTTP {exc.code}).")
            return result
        except urllib.error.URLError as exc:
            result = ScanResult(root=Path(gh_target.slug))
            result.errors.append(f"Network error downloading {gh_target.slug}: {exc.reason}")
            return result

        if cancel_event is not None and cancel_event.is_set():
            res = ScanResult(root=Path(gh_target.slug))
            res.errors.append("Scan cancelled by user.")
            return res

        if status_callback is not None:
            status_callback(f"Extracting {gh_target.slug}...")

        extracted_root = _safe_extract_zip(zip_path, tmp_root / "src")
        config = load_config(
            extracted_root,
            disabled_rules=disabled_rules,
            extra_ignore=extra_ignore,
            fail_on=fail_on,
            max_score=max_score,
            include_tests=include_tests,
        )
        result = scan_path(
            extracted_root,
            config,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )

        # Normalize paths so reports show clean repo-relative paths instead of temp dir paths.
        display_root = Path(gh_target.slug)
        normalized_findings = []
        for f in result.findings:
            try:
                rel = f.path.relative_to(extracted_root)
            except ValueError:
                rel = f.path
            normalized_findings.append(replace(f, path=display_root / rel))

        result.root = display_root
        result.findings = normalized_findings
        return result
