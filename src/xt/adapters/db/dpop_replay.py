# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cache phát lại DPoP dùng chung trên Postgres (audit DPA-02, migration `0013_dpop_jti.sql`).

`SingleUseStore` không theo tenant: khóa `dpop|<jkt>|<jti>` -> `xt_identity.dpop_jti_consume`
(UNIQUE (jkt, jti), SECURITY DEFINER; xt_app không đụng bảng trực tiếp). Mọi tiến trình/replica
xt-api thấy cùng một tập jti và tập này sống qua restart. Lỗi DB ném ra để verifier fail-closed.
Dọn mục hết hạn: `purge()` (job relay của `xt worker`).
"""

from __future__ import annotations

from datetime import UTC, datetime

from xt.adapters.db.connection import Database

PURGE_BATCH = 10_000


class PgDpopReplayStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def consume(self, key: str, expires_at: int | None) -> bool:
        prefix, jkt, jti = key.split("|", 2)  # jti có thể chứa "|": maxsplit giữ nguyên
        if prefix != "dpop" or not jkt or not jti or expires_at is None:
            raise ValueError("khóa DPoP không hợp lệ")
        with self._db.connection() as conn:
            row = conn.execute(
                "SELECT xt_identity.dpop_jti_consume(%s, %s, %s)",
                (jkt, jti, datetime.fromtimestamp(expires_at, UTC)),
            ).fetchone()
        return bool(row and row[0])

    def purge(self, limit: int = PURGE_BATCH) -> int:
        with self._db.connection() as conn:
            row = conn.execute("SELECT xt_identity.dpop_jti_purge(%s)", (limit,)).fetchone()
        return int(row[0]) if row else 0
