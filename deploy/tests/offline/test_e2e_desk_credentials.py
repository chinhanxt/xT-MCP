# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Timeout diagnostics must not expose browser login credentials."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "e2e"))

from xte2e import desk


class BrowserCredentials(unittest.TestCase):
    def test_timeout_command_contains_no_password_or_totp(self) -> None:
        captured = {}

        def timeout(cmd, **kwargs):
            captured.update(kwargs)
            raise subprocess.TimeoutExpired(cmd, 1)

        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(desk, "settings", return_value=SimpleNamespace(script_env=dict)),
            patch.object(desk.subprocess, "run", side_effect=timeout),
            self.assertRaises(subprocess.TimeoutExpired) as failure,
        ):
            desk.run(
                "oidc",
                {"username": "test-user", "password": "secret-password", "totp": "secret-totp"},
            )
        self.assertIn("secret-password", captured["input"])
        self.assertNotIn("secret-password", str(failure.exception))
        self.assertNotIn("secret-totp", str(failure.exception))


if __name__ == "__main__":
    unittest.main()
