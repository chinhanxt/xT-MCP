# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_import_linter_passes() -> None:
    exe = shutil.which("lint-imports", path=str(Path(sys.executable).parent))
    assert exe, "lint-imports chưa cài (uv sync)"
    r = subprocess.run([exe], cwd=ROOT, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
