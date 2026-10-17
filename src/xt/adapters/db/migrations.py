# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Migration nền tảng sau bootstrap (`sql/migrations/NNNN_*.sql`), chạy bằng xt_migrator.

Mỗi file một transaction; tên file đã áp ghi ở `xt_meta.schema_migration`. File đã áp không chạy
lại; thứ tự theo tên. Không có "down": sửa lỗi bằng migration mới.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Any

import psycopg

LEDGER = (
    "CREATE TABLE IF NOT EXISTS xt_meta.schema_migration ("
    " name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT clock_timestamp())"
)


def available() -> list[tuple[str, str]]:
    root = files("xt.adapters.db").joinpath("sql/migrations")
    out = [
        (p.name, p.read_text(encoding="utf-8"))
        for p in root.iterdir()
        if p.name.endswith(".sql") and p.name[:4].isdigit()
    ]
    return sorted(out)


def applied(migrator: psycopg.Connection[Any]) -> set[str]:
    exists = migrator.execute("SELECT to_regclass('xt_meta.schema_migration')").fetchone()
    if not exists or exists[0] is None:
        return set()
    return {str(r[0]) for r in migrator.execute("SELECT name FROM xt_meta.schema_migration")}


def pending(migrator: psycopg.Connection[Any]) -> list[str]:
    done = applied(migrator)
    return [name for name, _ in available() if name not in done]


def migrate(migrator: psycopg.Connection[Any]) -> list[str]:
    """Áp các migration còn thiếu; trả tên file vừa áp. Kết nối phải là xt_migrator, autocommit."""
    row = migrator.execute(
        "SELECT current_user, to_regclass('xt_meta.platform') IS NOT NULL"
    ).fetchone()
    if row is None or row[0] != "xt_migrator":
        raise RuntimeError("migration phải chạy bằng role xt_migrator")
    if not row[1]:
        raise RuntimeError("chưa bootstrap nền tảng (xt_meta.platform không tồn tại)")
    with migrator.transaction():
        migrator.execute(LEDGER)
    done = applied(migrator)
    ran: list[str] = []
    for name, body in available():
        if name in done:
            continue
        with migrator.transaction():
            migrator.execute("SELECT pg_advisory_xact_lock(hashtextextended('xt:migrate', 0))")
            again = migrator.execute(
                "SELECT 1 FROM xt_meta.schema_migration WHERE name = %s", (name,)
            ).fetchone()
            if again:
                continue
            # Immutable 0019 mixed protected gate DDL with views; 0021 replaces its view step.
            # Gate upgrades belong to the superuser PostgREST installer, never this login.
            if name != "0019_postgrest_app_roles.sql":
                migrator.execute(body.encode())
            migrator.execute("INSERT INTO xt_meta.schema_migration (name) VALUES (%s)", (name,))
        ran.append(name)
    return ran
