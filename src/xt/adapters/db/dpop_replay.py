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
ACQUIRE_TIMEOUT_S = 1.5  # chờ kết nối từ pool (DB mất -> 503 nhanh, DPA-07)
STATEMENT_TIMEOUT_MS = 3000  # câu lệnh/khóa bảng (LOCK, VACUUM FULL, migration) -> 503 nhanh


class PgDpopReplayStore:
    def __init__(
        self,
        db: Database,
        *,
        acquire_timeout_s: float = ACQUIRE_TIMEOUT_S,
        statement_timeout_ms: int = STATEMENT_TIMEOUT_MS,
    ) -> None:
        self._db = db
        self._acquire = acquire_timeout_s
        self._stmt = int(statement_timeout_ms)

    def _call(self, sql: str, params: tuple[object, ...]) -> object:
        """Một transaction ngắn: `SET LOCAL` statement/lock timeout, rồi gọi hàm. Không bao giờ treo
        quá acquire + statement timeout; lỗi ném ra để verifier trả 503."""
        with self._db.pool.connection(timeout=self._acquire) as conn, conn.transaction():
            conn.execute(f"SET LOCAL statement_timeout = {self._stmt}")
            conn.execute(f"SET LOCAL lock_timeout = {self._stmt}")
            row = conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def consume(self, key: str, expires_at: int | None) -> bool:
        prefix, jkt, jti = key.split("|", 2)  # jti có thể chứa "|": maxsplit giữ nguyên
        if prefix != "dpop" or not jkt or not jti or expires_at is None:
            raise ValueError("khóa DPoP không hợp lệ")
        got = self._call(
            "SELECT xt_identity.dpop_jti_consume(%s, %s, %s)",
            (jkt, jti, datetime.fromtimestamp(expires_at, UTC)),
        )
        return bool(got)

    def purge(self, limit: int = PURGE_BATCH) -> int:
        got = self._call("SELECT xt_identity.dpop_jti_purge(%s)", (limit,))
        return int(got) if isinstance(got, int) else 0
