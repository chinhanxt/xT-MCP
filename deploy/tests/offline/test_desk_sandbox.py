# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Render production nginx with a dynamic Desk origin and preserve its separate frame policies."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def node() -> str:
    found = shutil.which("node")
    if found:
        return found
    installed = sorted((Path.home() / ".nvm/versions/node").glob("*/bin/node"))
    if not installed:
        raise RuntimeError("Node is required to verify the production Desk configuration")
    return str(installed[-1])


class DeskSandbox(unittest.TestCase):
    def render(self, origin: str) -> tuple[subprocess.CompletedProcess[str], str]:
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "nginx.conf"
            result = subprocess.run(
                [node(), str(ROOT / "desk/scripts/render-nginx.mjs"), str(output)],
                env={**os.environ, "DESK_XT_ORIGIN": origin},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            return result, output.read_text() if output.exists() else ""

    def test_sandbox_allows_only_configured_parent_and_main_page_stays_unframeable(self):
        result, config = self.render("https://other-desk.example:8443/path?q=ignored")
        self.assertEqual(result.returncode, 0, result.stderr)
        server, sandbox = config.split("location = /sandbox.html", 1)
        sandbox = sandbox.split("location /", 1)[0]
        self.assertIn('X-Frame-Options "DENY"', server)
        self.assertIn("frame-ancestors 'none'", server)
        self.assertIn('Referrer-Policy "strict-origin"', server)
        self.assertIn("frame-ancestors https://other-desk.example:8443;", sandbox)
        self.assertNotIn("X-Frame-Options", sandbox.split("add_header", 1)[1])
        self.assertNotIn("script-src 'self'", sandbox)
        self.assertNotIn("@@DESK_ORIGIN@@", config)

    def test_invalid_or_injectable_origins_fail_without_output(self):
        for origin in (
            "",
            "file:///etc/passwd",
            "https://u:p@example.com",
            'https://bad"host.test',
        ):
            with self.subTest(origin=origin):
                result, config = self.render(origin)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(config, "")
