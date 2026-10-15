# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Test offline: Postgres log an toàn (docs/model-engine.md §8). Chỉ phân tích tệp, không Docker/DB."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2]
REQUIRED = (
    "log_statement=none", "log_parameter_max_length=0", "log_parameter_max_length_on_error=0",
    "log_error_verbosity=terse", "log_min_duration_statement=-1", "log_min_duration_sample=-1",
    "log_transaction_sample_rate=0",
)


def pg_command() -> list[str]:
    text = (DEPLOY / "compose.yaml").read_text(encoding="utf-8")
    block = text.split("\n  postgres:\n", 1)[1].split("\n  keycloak:\n", 1)[0]
    m = re.search(r"^\s*command:\s*(\[.*\])\s*$", block, re.M)
    assert m, "postgres thiếu command"
    return ast.literal_eval(m.group(1))


class PostgresLogging(unittest.TestCase):
    def test_compose_flags(self):
        cmd = pg_command()
        self.assertEqual(cmd[0], "postgres")
        settings = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
        for flag in REQUIRED:
            self.assertIn(flag, settings)
        for s in settings:
            self.assertFalse(s.startswith(("shared_preload_libraries", "session_preload_libraries")) and "auto_explain" in s)
        self.assertNotIn("auto_explain", " ".join(cmd))

    def test_compose_no_auto_explain(self):
        self.assertNotIn("auto_explain", (DEPLOY / "compose.yaml").read_text(encoding="utf-8"))

    def test_init_db_role_settings(self):
        tree = ast.parse((DEPLOY / "scripts" / "init_db.py").read_text(encoding="utf-8"))
        consts = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
                consts[n.targets[0].id] = ast.literal_eval(n.value)
        self.assertEqual(set(consts["LOG_ROLES"]), {"xt_app", "xt_migrator"})
        self.assertEqual(dict(consts["ROLE_LOG_SETTINGS"]), {
            "log_statement": "'none'", "log_parameter_max_length": "0",
            "log_parameter_max_length_on_error": "0", "log_error_verbosity": "'terse'"})
        src = (DEPLOY / "scripts" / "init_db.py").read_text(encoding="utf-8")
        self.assertIn("ALTER ROLE {} SET {} = ", src)
        self.assertIn("for role in LOG_ROLES", src)
        self.assertLess(src.index("SET log_statement = 'none'"), src.index("bootstrap(\n"))


if __name__ == "__main__":
    unittest.main()
