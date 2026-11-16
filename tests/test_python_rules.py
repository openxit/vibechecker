"""Unit tests for Python AST and token analysis rules."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibecheck.models import Config
from vibecheck.scanner import scan_path


class TestPythonRules(unittest.TestCase):
    def _scan_files(self, files: dict[str, str], *, include_tests: bool = False) -> list[tuple[str, str]]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, content in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            cfg = Config(root=root, include_tests=include_tests)
            result = scan_path(root, cfg)
            return [(f.rule_id, f.message) for f in result.findings]

    def test_vc001_detects_phantom_stubs_and_skips_protocols(self) -> None:
        findings = self._scan_files(
            {
                "app.py": """
from abc import ABC, abstractmethod
from typing import Protocol

class Store(Protocol):
    def get(self, key: str) -> str: ...

class BaseRepo(ABC):
    @abstractmethod
    def save(self, item: dict) -> None:
        pass

def process_payment(amount: float, currency: str) -> bool:
    pass

def fetch_users(org_id: str) -> list:
    return []

def build_report(user_id: int) -> str:
    raise NotImplementedError
"""
            }
        )
        vc001 = [msg for rid, msg in findings if rid == "VC001"]
        self.assertEqual(len(vc001), 3)
        self.assertTrue(any("process_payment" in m for m in vc001))
        self.assertTrue(any("fetch_users" in m for m in vc001))
        self.assertTrue(any("build_report" in m for m in vc001))

    def test_vc002_ignores_strings_and_catches_real_comments(self) -> None:
        findings = self._scan_files(
            {
                "service.py": '''
def banner() -> str:
    return "TODO: this is just UI text, not a comment"

def run() -> int:
    # implement this later
    return 1
'''
            }
        )
        vc002 = [msg for rid, msg in findings if rid == "VC002"]
        self.assertEqual(len(vc002), 1)
        self.assertIn("implement this later", vc002[0])

    def test_vc003_detects_cross_file_cloned_logic(self) -> None:
        body_a = """
def compute_alpha(items, tax_rate):
    total = 0
    for item in items:
        total += item * (1 + tax_rate)
    rounded = round(total, 2)
    return rounded
"""
        body_b = """
def calculate_beta(entries, rate):
    acc = 0
    for entry in entries:
        acc += entry * (1 + rate)
    final_val = round(acc, 2)
    return final_val
"""
        findings = self._scan_files({"a.py": body_a, "b.py": body_b})
        vc003 = [msg for rid, msg in findings if rid == "VC003"]
        self.assertEqual(len(vc003), 1)
        self.assertIn("calculate_beta", vc003[0])

    def test_vc004_detects_swallowed_broad_exceptions(self) -> None:
        findings = self._scan_files(
            {
                "worker.py": """
def risky() -> None:
    try:
        do_work()
    except Exception:
        pass

def handled() -> int:
    try:
        return do_work()
    except ValueError:
        return 0
"""
            }
        )
        vc004 = [msg for rid, msg in findings if rid == "VC004"]
        self.assertEqual(len(vc004), 1)

    def test_vc005_detects_hallucinated_imports_with_manifest(self) -> None:
        findings = self._scan_files(
            {
                "pyproject.toml": '[project]\nname = "demo"\ndependencies = ["pyyaml>=6.0"]\n',
                "helper.py": "VALUE = 1\n",
                "main.py": """
import json
import yaml
import helper
import definitely_not_a_real_pkg
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    import phantom_type_only
""",
            }
        )
        vc005 = [msg for rid, msg in findings if rid == "VC005"]
        self.assertEqual(len(vc005), 1)
        self.assertIn("definitely_not_a_real_pkg", vc005[0])

    def test_vc006_flags_mock_data_in_source_but_skips_tests(self) -> None:
        findings = self._scan_files(
            {
                "api.py": 'mock_users = [{"id": 1}]\nAPI_KEY = "your-api-key"\n',
                "tests/test_api.py": 'mock_users = [{"id": 1}]\n',
            }
        )
        vc006 = [msg for rid, msg in findings if rid == "VC006"]
        self.assertEqual(len(vc006), 2)

    def test_vc007_vc008_vc009_vibe_signals(self) -> None:
        findings = self._scan_files(
            {
                "a.py": """
def used_helper(x: int) -> int:
    # Initialize the result
    val = x + 1
    return val

def main() -> int:
    return used_helper(5)
""",
                "b.py": """
def unused_orphan(x: int) -> str:
    return "Contact John Doe at https://example.com"
""",
            }
        )
        rule_ids = [rid for rid, _ in findings]
        self.assertIn("VC007", rule_ids)
        self.assertIn("VC008", rule_ids)
        self.assertIn("VC009", rule_ids)


if __name__ == "__main__":
    unittest.main()
