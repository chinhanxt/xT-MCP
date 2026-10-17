# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Private custom secret roots must protect container-readable leaf files on host."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "xt_secret_permissions", DEPLOY / "scripts/envfile.py"
)
envfile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envfile)


class SecretRootPermissions(unittest.TestCase):
    def test_generated_app_and_seaweed_secrets_have_private_root(self) -> None:
        for existing in (False, True):
            for generate, leaf in (
                (envfile.ensure_app_secrets, "app"),
                (envfile.ensure_seaweed_configs, "seaweed"),
            ):
                with (
                    self.subTest(existing=existing, leaf=leaf),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    root = Path(tmp) / "custom" / "xt"
                    if existing:
                        root.mkdir(parents=True)
                        root.chmod(0o700)
                    env = {key: "test-only-value" for key in envfile.SECRET_KEYS}
                    env["XT_SECRETS_DIR"] = str(root)
                    with contextlib.redirect_stdout(io.StringIO()):
                        generated = generate(env)
                    self.assertEqual(root.stat().st_mode & 0o777, 0o700)
                    self.assertEqual(generated, root / leaf)
                    self.assertEqual(generated.stat().st_mode & 0o777, 0o755)
                    self.assertTrue(list(generated.iterdir()))
                    for item in generated.iterdir():
                        self.assertEqual(item.stat().st_mode & 0o777, 0o644)

    def test_existing_insecure_root_is_rejected_without_chmod_or_secret_writes(self) -> None:
        for generate in (envfile.ensure_app_secrets, envfile.ensure_seaweed_configs):
            with self.subTest(generator=generate.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "shared"
                root.mkdir(mode=0o755)
                root.chmod(0o755)
                env = {key: "test-only-value" for key in envfile.SECRET_KEYS}
                env["XT_SECRETS_DIR"] = str(root)
                with self.assertRaisesRegex(SystemExit, "dedicated private directory"):
                    generate(env)
                self.assertEqual(root.stat().st_mode & 0o777, 0o755)
                self.assertEqual(list(root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
