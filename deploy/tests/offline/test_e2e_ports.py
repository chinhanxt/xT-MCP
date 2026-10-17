# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""APISIX_HTTP_PORT / KC_HTTP_PORT (defaults 9080/18080) are honored by every script the e2e touches."""

from __future__ import annotations

import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "e2e"))

from xte2e import config  # noqa: E402

PORT_KEYS = ("APISIX_HTTP_PORT", "KC_HTTP_PORT", "GW", "KC_ADMIN_URL")


def clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in PORT_KEYS}
    env.update(extra)
    return env


class Settings(unittest.TestCase):
    def tearDown(self) -> None:
        config.settings.cache_clear()

    def _settings(self, file_env: dict[str, str], **env: str) -> config.Settings:
        config.settings.cache_clear()
        with (
            patch.dict(os.environ, clean_env(**env), clear=True),
            patch.object(config, "load_env_file", return_value=file_env),
        ):
            return config.settings()

    def test_defaults(self) -> None:
        s = self._settings({})
        self.assertEqual(s.gateway, ("127.0.0.1", 9080))
        self.assertEqual(s.kc_admin, "http://127.0.0.1:18080")
        with patch.dict(os.environ, clean_env(), clear=True):
            self.assertNotIn("APISIX_HTTP_PORT", s.script_env())

    def test_env_file_override_reaches_script_env(self) -> None:
        s = self._settings({"APISIX_HTTP_PORT": "19080", "KC_HTTP_PORT": "28080"})
        self.assertEqual(s.gateway, ("127.0.0.1", 19080))
        self.assertEqual(s.kc_admin, "http://127.0.0.1:28080")
        with patch.dict(os.environ, clean_env(), clear=True):
            env = s.script_env()
        self.assertEqual(env["APISIX_HTTP_PORT"], "19080")
        self.assertEqual(env["KC_HTTP_PORT"], "28080")

    def test_shell_override_wins(self) -> None:
        s = self._settings({"APISIX_HTTP_PORT": "19080"}, APISIX_HTTP_PORT="29080")
        self.assertEqual(s.gateway[1], 29080)


class Scripts(unittest.TestCase):
    def test_smoke_sh_gw(self) -> None:
        line = re.search(r"^GW=.*$", (ROOT / "deploy/tests/security_smoke.sh").read_text(), re.M)
        assert line
        for extra, want in (
            ({}, "127.0.0.1:9080"),
            ({"APISIX_HTTP_PORT": "19080"}, "127.0.0.1:19080"),
            ({"GW": "10.0.0.1:1"}, "10.0.0.1:1"),
        ):
            r = subprocess.run(
                ["bash", "-c", f'{line.group(0)}; printf %s "$GW"'],
                env=clean_env(**extra),
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(r.stdout, want)

    @unittest.skipUnless(shutil.which("node"), "node missing")
    def test_mjs_gw(self) -> None:
        for rel in ("e2e/desk/desk_real.mjs", "deploy/tests/desk_oidc_e2e.mjs"):
            line = re.search(r"^const GW = .*$", (ROOT / rel).read_text(), re.M)
            assert line, rel
            for extra, want in (
                ({}, "127.0.0.1:9080"),
                ({"APISIX_HTTP_PORT": "19080"}, "127.0.0.1:19080"),
                ({"GW": "10.0.0.1:1"}, "10.0.0.1:1"),
            ):
                r = subprocess.run(
                    ["node", "-e", f"{line.group(0)}; process.stdout.write(GW)"],
                    env=clean_env(**extra),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(r.stdout, want, rel)

    def test_desk_oidc_py(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "tests").mkdir()
            shutil.copy(ROOT / "deploy/tests/desk_oidc_e2e.py", Path(td) / "tests")
            (Path(td) / ".env").write_text("XT_REALM=xt\n")
            for extra, gw, kc in (
                ({}, "127.0.0.1:9080", "http://127.0.0.1:18080"),
                (
                    {"APISIX_HTTP_PORT": "19080", "KC_HTTP_PORT": "28080"},
                    "127.0.0.1:19080",
                    "http://127.0.0.1:28080",
                ),
            ):
                with patch.dict(os.environ, clean_env(**extra), clear=True):
                    ns = runpy.run_path(str(Path(td) / "tests" / "desk_oidc_e2e.py"), run_name="x")
                self.assertEqual((ns["GW"], ns["KC"]), (gw, kc))

    def test_fetch_jwks_default_base_uses_kc_port(self) -> None:
        src = (ROOT / "deploy/scripts/fetch_jwks.py").read_text()
        self.assertIn("KC_HTTP_PORT", src)
        self.assertNotRegex(src, r"127\.0\.0\.1:18080")


class Harness(unittest.TestCase):
    def test_subprocess_callers_pass_script_env(self) -> None:
        for rel in (
            "e2e/xte2e/desk.py",
            "e2e/scenarios/test_e09_desk_oidc.py",
            "e2e/scenarios/test_e10_security.py",
        ):
            self.assertIn("script_env()", (ROOT / rel).read_text(), rel)


if __name__ == "__main__":
    unittest.main()
