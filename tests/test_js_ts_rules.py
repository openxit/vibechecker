"""Unit tests for JavaScript and TypeScript rules."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibecheck.models import Config
from vibecheck.scanner import scan_path


class TestJsTsRules(unittest.TestCase):
    def test_js_ts_rules_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "package.json").write_text(
                '{"name": "app", "dependencies": {"zod": "^3.0.0"}}',
                encoding="utf-8",
            )
            (root / "index.ts").write_text(
                """
import fs from "node:fs";
import { z } from "zod";
import { magic } from "hallucinated-ai-lib";
import { localFn } from "./local";

// TODO: wire up real billing
export function chargeCard(amount: number) {
  return null;
}

export async function syncData() {
  try {
    await localFn();
  } catch (err) {
    console.log(err);
  }
}

const mockCustomer = { id: 1 };
""",
                encoding="utf-8",
            )
            result = scan_path(root, Config(root=root))
            rule_ids = [f.rule_id for f in result.findings]
            self.assertIn("VC001", rule_ids)
            self.assertIn("VC002", rule_ids)
            self.assertIn("VC004", rule_ids)
            self.assertIn("VC005", rule_ids)
            self.assertIn("VC006", rule_ids)
            vc005_msgs = [f.message for f in result.findings if f.rule_id == "VC005"]
            self.assertEqual(len(vc005_msgs), 1)
            self.assertIn("hallucinated-ai-lib", vc005_msgs[0])


if __name__ == "__main__":
    unittest.main()
