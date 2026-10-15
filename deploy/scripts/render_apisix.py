#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sinh deploy/apisix/rendered/apisix.yaml từ apisix.yaml.tmpl + deploy/.env (hostname không hard-code)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from envfile import load_env  # noqa: E402


def main() -> int:
    env = {**load_env(DEPLOY / ".env"), **os.environ}
    tmpl = (DEPLOY / "apisix" / "apisix.yaml.tmpl").read_text()
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        v = env.get(m.group(1), "").strip()
        if not v:
            missing.append(m.group(1))
        return v

    out = re.sub(r"@@([A-Z0-9_]+)@@", sub, tmpl)
    if missing:
        sys.exit(f"thiếu biến: {sorted(set(missing))} (deploy/.env)")
    dest = DEPLOY / "apisix" / "rendered" / "apisix.yaml"
    dest.parent.mkdir(exist_ok=True)
    with dest.open("w") as f:  # ghi tại chỗ: giữ inode cho bind mount
        f.write(out)
    print(f"đã sinh {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
