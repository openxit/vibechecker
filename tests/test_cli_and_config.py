"""Unit tests for CLI commands, config loading, and inline suppressions."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibecheck.cli import main


class TestCliAndConfig(unittest.TestCase):
    def test_inline_ignore_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.py").write_text(
                """
def intentional_stub(x: int) -> None:  # vibecheck: ignore[VC001]
    pass

def real_stub(y: int) -> None:
    pass
""",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                exit_code = main([str(root), "--format", "json", "--fail-on", "error"])
            self.assertEqual(exit_code, 1)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["summary"]["error"], 1)
            self.assertEqual(len(payload["findings"]), 1)
            self.assertIn("real_stub", payload["findings"][0]["message"])

    def test_rules_and_init_commands(self) -> None:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["rules"])
        self.assertEqual(code, 0)
        self.assertIn("VC001", buf.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            buf_init = io.StringIO()
            with redirect_stdout(buf_init):
                code_init = main(["init", tmp])
            self.assertEqual(code_init, 0)
            self.assertTrue((Path(tmp) / ".vibecheck.toml").is_file())

    def test_prompt_output_includes_findings_and_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text(
                "def broken(x: int) -> None:\n    pass\n",
                encoding="utf-8",
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main([str(root), "--format", "prompt"])
            self.assertEqual(code, 0)
            out = buf.getvalue()
            self.assertIn("VC001", out)
            self.assertIn("bad.py", out)
            self.assertIn("Required fix guidelines", out)


if __name__ == "__main__":
    unittest.main()
