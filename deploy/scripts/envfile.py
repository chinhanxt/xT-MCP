# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đọc/ghi deploy/.env và sinh bí mật còn trống (stdlib)."""
from __future__ import annotations

import re
import secrets
import shutil
from pathlib import Path

SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
    "XT_IDP_INTROSPECTION_CLIENT_SECRET",
    "XT_IDP_ADMIN_CLIENT_SECRET",
    "OPENBAO_DEV_TOKEN",
)
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def _value(raw: str) -> str:
    return raw.split(" #", 1)[0].strip().strip("'\"") if not raw.startswith(("'", '"')) else raw.strip().strip("'\"")


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = _LINE.match(line)
            if m:
                out[m.group(1)] = _value(m.group(2))
    return out


def ensure_env(deploy: Path) -> dict[str, str]:
    """Tạo .env từ .env.example nếu chưa có; điền bí mật trống. Không bao giờ ghi đè giá trị đã có."""
    env_path = deploy / ".env"
    if not env_path.exists():
        shutil.copyfile(deploy / ".env.example", env_path)
        env_path.chmod(0o600)
    lines = env_path.read_text().splitlines()
    changed = False
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m and m.group(1) in SECRET_KEYS and not _value(m.group(2)):
            lines[i] = f"{m.group(1)}={secrets.token_urlsafe(24)}"
            changed = True
            print(f"đã sinh {m.group(1)} vào {env_path}")
    if changed:
        env_path.write_text("\n".join(lines) + "\n")
    return load_env(env_path)
