#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cài lớp PostgREST cộng thêm trên DB đã bootstrap, bằng kết nối quản trị tường minh.

XT_DATA_SUPERUSER_URL nằm trong môi trường hoặc secret file của người vận hành; không nhận
DSN qua argv, không log DSN/mật khẩu. Dùng cùng XT_DATA_CLIENTS với provision_data.py.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from envfile import load_env

from xt.adapters.db.tenancy import install_postgrest_role


def main() -> int:
    env = {**load_env(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    dsn = env.get("XT_DATA_SUPERUSER_URL", "")
    password = env.get("XT_PGRST_PASSWORD", "")
    clients = [c.strip() for c in env.get("XT_DATA_CLIENTS", "").split(",") if c.strip()]
    issuer = env.get("XT_PGRST_ISSUER", "")
    if not issuer and env.get("XT_DOMAIN_AUTH"):
        issuer = f"https://{env['XT_DOMAIN_AUTH']}/realms/{env.get('XT_REALM') or 'xt'}"
    missing = [
        name
        for name, value in (
            ("XT_DATA_SUPERUSER_URL", dsn),
            ("XT_PGRST_PASSWORD", password),
            ("XT_DATA_CLIENTS", clients),
            ("XT_PGRST_ISSUER hoặc XT_DOMAIN_AUTH", issuer),
        )
        if not value
    ]
    if missing:
        raise SystemExit("configure_data: thiếu " + ", ".join(missing))
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            install_postgrest_role(
                conn, authenticator_password=password, issuer=issuer, clients=clients
            )
    except (psycopg.Error, ValueError):
        # Exception kết nối có thể chứa host/DSN: không đưa thông tin đăng nhập ra terminal.
        raise SystemExit(
            "configure_data: cài đặt thất bại; kiểm DB đã bootstrap và kết nối quản trị"
        ) from None
    print(f"configure_data: đã cài PostgREST, issuer={issuer}, {len(clients)} client được duyệt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
