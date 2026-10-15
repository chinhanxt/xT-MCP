# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Thông báo PostgREST sau DDL: phát cùng commit, rollback không đổi cache."""

from __future__ import annotations

from typing import Any

import psycopg


def notify_postgrest(conn: psycopg.Connection[Any]) -> None:
    """Nạp lại danh sách schema từ DB trước khi nạp view/model vào schema cache.

    Caller giữ transaction DDL; PostgreSQL chỉ gửi NOTIFY khi transaction commit.
    Không lưu danh sách schema ở phía Python để tránh ghi đè khi tạo tenant đồng thời.
    """
    conn.execute("SELECT pg_notify('pgrst', 'reload config')")
    conn.execute("SELECT pg_notify('pgrst', 'reload schema')")
