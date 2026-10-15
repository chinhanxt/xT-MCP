# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Điểm nối quản trị để cài schema nền trước PostgREST, không đổi migration đã audit.

Các migration nền cũ cài trigger trực tiếp. Bootstrap audit mới không cấp quyền EXECUTE hàm
trigger cho migrator lúc chạy. Chỉ trong transaction quản trị này mới cấp quyền cài trigger;
thu hồi trước commit nên không có cửa sổ quyền thêm cho các session khác.
"""

from __future__ import annotations

from typing import Any

import psycopg

from xt.adapters.db.migrations import migrate


def install_data_prerequisites(superuser: psycopg.Connection[Any]) -> list[str]:
    """Cài migration nền bằng cùng connection/transaction quản trị; trả file vừa áp.

    Giữ nguyên thân migration, ownership xt_migrator và ACL audit trước/sau. Đây là API cài đặt
    cần superuser, không thay thế đường migrator thông thường hay sửa logic trigger audit.
    """
    if (
        not superuser.autocommit
        or superuser.info.transaction_status != psycopg.pq.TransactionStatus.IDLE
    ):
        raise ValueError("cài schema data cần kết nối quản trị autocommit đang IDLE")
    with superuser.transaction():
        row = superuser.execute(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if row is None or not row[0]:
            raise ValueError("cài schema data cần superuser")
        superuser.execute("SELECT pg_advisory_xact_lock(hashtextextended('xt:migrate', 0))")
        granted = superuser.execute(
            "SELECT has_function_privilege('xt_migrator', 'xt_audit.log()', 'EXECUTE')"
        ).fetchone()
        assert granted is not None
        if not granted[0]:
            superuser.execute("GRANT EXECUTE ON FUNCTION xt_audit.log() TO xt_migrator")
        superuser.execute("SET LOCAL ROLE xt_migrator")
        ran = migrate(superuser)
        superuser.execute("RESET ROLE")
        if not granted[0]:
            superuser.execute("REVOKE EXECUTE ON FUNCTION xt_audit.log() FROM xt_migrator")
    return ran
