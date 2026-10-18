# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Known product regressions must remain visible without hiding other failures."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e"))

from xte2e.report import write_scenario, write_summary


class ExpectedFailureEvidence(unittest.TestCase):
    def test_known_regression_is_not_reported_as_pass(self) -> None:
        results = [self.result("passed"), self.result("xfailed", "E13-BUG-01")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_scenario(root, "E13", results)
            write_summary(root, {"E13": results}, ["E13"])
            self.assertEqual(json.loads((root / "E13.json").read_text())["status"], "XFAIL")
            summary = json.loads((root / "summary.json").read_text())["E13"]
            self.assertEqual(summary["status"], "XFAIL")
            self.assertIn("E13-BUG-01", summary["failures"])

    def test_real_failure_takes_precedence_over_expected_failure(self) -> None:
        results = [self.result("xfailed", "E13-BUG-01"), self.result("failed", "new regression")]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_scenario(root, "E13", results)
            write_summary(root, {"E13": results}, ["E13"])
            self.assertEqual(json.loads((root / "E13.json").read_text())["status"], "FAIL")
            self.assertEqual(
                json.loads((root / "summary.json").read_text())["E13"]["status"], "FAIL"
            )

    @staticmethod
    def result(outcome: str, message: str = "") -> dict[str, object]:
        return {
            "test": "demo",
            "outcome": outcome,
            "duration": 1,
            "message": message,
            "steps": [],
            "gaps": [],
        }


if __name__ == "__main__":
    unittest.main()
