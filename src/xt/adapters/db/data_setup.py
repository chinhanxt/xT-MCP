# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Điểm nối quản trị để cài schema nền trước PostgREST, không đổi migration đã audit.

R2-04 (docs/decisions.md): xt_migrator không được tin cậy với audit, nên **không bao giờ** được
EXECUTE `xt_audit.log()`/`append()`, kể cả tạm thời trong transaction. Bước superuser chỉ cài API
audit thuộc `xt_audit_owner` (`install_meta_audit`: `xt_audit.install_meta` SECURITY DEFINER + event
trigger bảo vệ); migration chạy bằng xt_migrator dùng các API đó (0006), không dùng hàm ghi audit.
"""

from __future__ import annotations

from typing import Any

import psycopg

from xt.adapters.db.migrations import migrate
from xt.adapters.db.tenancy import install_meta_audit


def install_data_prerequisites(superuser: psycopg.Connection[Any]) -> list[str]:
    """Cài API audit metadata (superuser) rồi migration nền (xt_migrator) trong cùng một
    transaction quản trị; trả file vừa áp. Không cấp/thu quyền audit nào cho xt_migrator.
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
        install_meta_audit(superuser)
        superuser.execute("SET LOCAL ROLE xt_migrator")
        ran = migrate(superuser)
        superuser.execute("RESET ROLE")
    return ran
