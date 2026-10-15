# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đặt sys.path tới 3 spike engine thật (không sửa chúng) và gốc của các pack."""
import os, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
PACKS_ROOT = pathlib.Path(os.environ.get("XT_PACKS_ROOT", HERE.parents[2] / "packages"))  # pack đã chuyển sang packages/
RESEARCH = pathlib.Path(os.environ.get("XT_RESEARCH", HERE.parents[1] if (HERE.parents[1] / "spike-iam").exists() else "/home/chinhan/xT/research"))
for p in (HERE, RESEARCH / "spike-model-engine", RESEARCH / "spike-spiffworkflow", RESEARCH / "spike-iam"):
    if str(p) not in sys.path: sys.path.insert(0, str(p))
