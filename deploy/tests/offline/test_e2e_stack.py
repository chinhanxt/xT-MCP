# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Failure-path checks for the stack gate; no Docker daemon or real credentials."""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e"))

from xte2e import stack


class StackLifecycle(unittest.TestCase):
    def test_failed_clean_stops_before_build(self) -> None:
        def clean(*args, **kwargs):
            if kwargs.get("check", True):
                raise RuntimeError("clean failed")
            return subprocess.CompletedProcess([], 1, "", "clean failed")

        with (
            patch.object(stack, "preflight"),
            patch.object(stack.compose, "dc", side_effect=clean) as dc,
            patch.object(stack.subprocess, "run") as render,
            self.assertRaisesRegex(RuntimeError, "clean failed"),
        ):
            stack.up()
        render.assert_called_once()
        dc.assert_called_once()

    def test_render_runs_before_compose_parses_required_secrets(self) -> None:
        events = []
        with (
            patch.object(stack, "preflight"),
            patch.object(
                stack.subprocess, "run", side_effect=lambda *a, **k: events.append("render")
            ),
            patch.object(stack.compose, "dc", side_effect=RuntimeError("stop")),
            self.assertRaisesRegex(RuntimeError, "stop"),
        ):
            stack.up()
        self.assertEqual(events, ["render"])

    def test_failed_start_records_issue_and_fails_gate(self) -> None:
        failed = subprocess.CompletedProcess([], 23, "", "start failed")
        with tempfile.TemporaryDirectory() as tmp:
            issues = Path(tmp) / "issues.json"
            with (
                patch.object(stack, "preflight"),
                patch.object(stack.compose, "dc", side_effect=[None, None, failed]),
                patch.object(stack.compose, "override_file", return_value=Path(tmp) / "override"),
                patch.object(stack.compose, "ps", return_value="xt-api\texited"),
                patch.object(stack, "issues_file", return_value=issues),
                patch.object(stack.subprocess, "run"),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaisesRegex(RuntimeError, "rc=23"),
            ):
                stack.up()
            self.assertIn("rc=23", issues.read_text(encoding="utf-8"))

    def test_cleanup_requires_success(self) -> None:
        with patch.object(stack.compose, "dc") as dc:
            stack.down()
        self.assertTrue(dc.call_args.kwargs.get("check", True))

    def test_heavy_preflight_does_not_treat_docker_failure_as_idle(self) -> None:
        with patch.object(stack.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 1, "", "daemon unavailable")
            with self.assertRaisesRegex(RuntimeError, "docker ps"):
                stack.other_heavy()


if __name__ == "__main__":
    unittest.main()
