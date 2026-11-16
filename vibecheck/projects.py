"""Local persistence for saved projects and background monitoring intervals."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

_STORE_PATH = Path.home() / ".vibecheck" / "projects.json"


@dataclass(slots=True)
class ProjectProfile:
    """Saved local folder or GitHub repository with optional scheduled checks."""

    name: str
    target: str
    interval_minutes: int = 30
    enabled: bool = True
    include_tests: bool = False
    last_score: int | None = None
    last_checked: str | None = None


def load_projects(store_path: Path = _STORE_PATH) -> list[ProjectProfile]:
    """Load saved project profiles from disk."""
    if not store_path.is_file():
        return []
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        projects: list[ProjectProfile] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            target = str(item.get("target", "")).strip()
            if not name or not target:
                continue
            projects.append(
                ProjectProfile(
                    name=name,
                    target=target,
                    interval_minutes=max(0, int(item.get("interval_minutes", 30))),
                    enabled=bool(item.get("enabled", True)),
                    include_tests=bool(item.get("include_tests", False)),
                    last_score=item.get("last_score"),
                    last_checked=item.get("last_checked"),
                )
            )
        return projects
    except (OSError, ValueError, TypeError):
        return []


def save_projects(projects: list[ProjectProfile], store_path: Path = _STORE_PATH) -> None:
    """Persist *projects* to disk cleanly."""
    store_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(p) for p in projects]
    store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
