# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Khởi tạo DB cho xT, idempotent. Chạy bằng service `xt-init` (ảnh xt, mount tệp này).

1. `bootstrap()` (role xt_migrator/xt_app, schema nền, dấu vân tay khóa audit) nếu DB chưa có.
2. Đặt lại mật khẩu xt_migrator/xt_app/xt_control, `ALTER ROLE xt_app/xt_migrator SET` 4 tham số log an toàn theo .env; tạo xt_control (queue) và nhóm xt_job nếu thiếu.
3. `xt db migrate` (role xt_migrator) và `xt worker --install-schema` (schema hàng đợi) nếu chưa có.
Thiếu biến thì thoát mã 2 với danh sách lỗi.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import psycopg
from psycopg import sql

from xt.adapters.db.tenancy import bootstrap

# docs/model-engine.md §8: không để câu lệnh/tham số (mật khẩu, PII) vào log Postgres.
ROLE_LOG_SETTINGS = (
    ("log_statement", "'none'"),
    ("log_parameter_max_length", "0"),
    ("log_parameter_max_length_on_error", "0"),
    ("log_error_verbosity", "'terse'"),
)
LOG_ROLES = ("xt_app", "xt_migrator")

NEED = (
    "XT_INIT_SUPERUSER_URL", "XT_DB_APP_PASSWORD", "XT_DB_MIGRATOR_PASSWORD", "XT_DB_CONTROL_PASSWORD",
    "XT_AUDIT_KEY_FILE", "XT_AUDIT_KEY_ID", "XT_DB_MIGRATOR_URL",
)


def main() -> int:
    env = os.environ
    missing = [k for k in NEED if not env.get(k, "").strip()]
    if missing:
        print("xt-init: thiếu cấu hình: " + ", ".join(missing), file=sys.stderr)
        return 2
    try:
        key = Path(env["XT_AUDIT_KEY_FILE"]).read_bytes().strip()
    except OSError as e:
        print(f"xt-init: không đọc được XT_AUDIT_KEY_FILE ({e.strerror})", file=sys.stderr)
        return 2
    db = psycopg.conninfo.conninfo_to_dict(env["XT_INIT_SUPERUSER_URL"]).get("dbname", "xt")
    with psycopg.connect(env["XT_INIT_SUPERUSER_URL"], autocommit=True) as su:
        su.execute("SET log_statement = 'none'")
        su.execute("SET log_parameter_max_length = 0")
        su.execute("SET log_parameter_max_length_on_error = 0")
        made = bootstrap(
            su,
            migrator_password=env["XT_DB_MIGRATOR_PASSWORD"],
            app_password=env["XT_DB_APP_PASSWORD"],
            pepper=key,
            pepper_kid=int(env["XT_AUDIT_KEY_ID"]),
        )
        print("xt-init: bootstrap", "đã chạy" if made else "đã có từ trước")
        for role, pw in (("xt_migrator", "XT_DB_MIGRATOR_PASSWORD"), ("xt_app", "XT_DB_APP_PASSWORD")):
            su.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(env[pw])))
        for role in LOG_ROLES:
            for name, val in ROLE_LOG_SETTINGS:
                su.execute(sql.SQL("ALTER ROLE {} SET {} = " + val).format(sql.Identifier(role), sql.SQL(name)))
        exists = su.execute("SELECT 1 FROM pg_roles WHERE rolname='xt_control'").fetchone()
        verb = "ALTER" if exists else "CREATE"
        su.execute(sql.SQL(verb + " ROLE xt_control LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
            sql.Literal(env["XT_DB_CONTROL_PASSWORD"])))
        su.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO xt_control").format(sql.Identifier(db)))
        if not su.execute("SELECT 1 FROM pg_roles WHERE rolname='xt_job'").fetchone():
            su.execute("CREATE ROLE xt_job NOLOGIN NOBYPASSRLS")
    subprocess.run(["xt", "db", "migrate"], check=True)
    with psycopg.connect(env["XT_INIT_SUPERUSER_URL"], autocommit=True) as su:
        has_queue = su.execute("SELECT to_regclass('xt_queue.requests') IS NOT NULL").fetchone()[0]
    if has_queue:
        print("xt-init: schema hàng đợi đã có")
    else:
        subprocess.run(["xt", "worker", "--install-schema"], check=True,
                       env={**env, "XT_QUEUE_MIGRATOR_DATABASE_URL": env["XT_INIT_SUPERUSER_URL"]})
    print("xt-init: xong")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
