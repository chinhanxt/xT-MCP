# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kết nối Postgres cho xT: pool `autocommit=True`, mỗi lời gọi một transaction THẬT với ngữ cảnh
tenant đặt bằng `set_config(k, v, true)` (tương đương SET LOCAL, tự hết khi commit/rollback).

Bẫy psycopg (SMT §1): trên kết nối non-autocommit, `execute` ngoài transaction mở transaction ngầm
và `with conn.transaction()` chỉ là SAVEPOINT, nên GUC của request trước rò sang request sau.
`tenant_tx` từ chối mọi kết nối không ở trạng thái IDLE. Pool có `max_lifetime` vì mỗi kết nối
giữ relcache ~2,3 MB cho mỗi tenant đã chạm (SMT §3).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from xt.domain.model.context import ModelActor

CONTEXT_SQL = (
    "SELECT xt.set_context(%s::uuid, %s::uuid, %s::text[], %s::uuid[], %s::uuid[], %s::uuid,"
    " %s, %s)"
)
# Tương thích ngược cho module khác (vd adapters/db/confirmations.py): cùng 8 tham số chuỗi như
# bản cũ (role/team/phòng ban phân tách bằng dấu phẩy) nhưng đi qua xt.set_context — policy RLS
# không đọc GUC nữa (AUD-13). Dùng `set_tenant_context(cur, actor)` cho mã mới.
GUC_SQL = (
    "SELECT xt.set_context(%s::uuid, nullif(%s, '')::uuid,"
    " coalesce(string_to_array(nullif(%s, ''), ','), '{}')::text[],"
    " coalesce(string_to_array(nullif(%s, ''), ','), '{}')::uuid[],"
    " coalesce(string_to_array(nullif(%s, ''), ','), '{}')::uuid[],"
    " nullif(%s, '')::uuid, nullif(%s, ''), nullif(%s, ''))"
)


def set_tenant_context(cur: Any, actor: ModelActor) -> None:
    cur.execute(
        CONTEXT_SQL,
        (
            actor.tenant.id,
            actor.actor_id,
            list(actor.roles),
            list(actor.team_ids),
            list(actor.department_ids),
            actor.effective_actor,
            actor.client_azp,
            actor.trace_id,
        ),
    )


AUDIT_HMAC_SQL = "SELECT set_config('xt.audit_hmac', %s, true)"


@dataclass(frozen=True, slots=True)
class AuditKey:
    """Khóa HMAC audit (G-M2) lấy từ secret (OpenBao/Compose). KHÔNG BAO GIỜ gửi vào Postgres:
    xt-api/xt-worker tính HMAC trong tiến trình và chỉ đưa chuỗi `hmac:<kid>:<hex>` vào đúng
    transaction có ghi field sensitive (R2-01). DB giữ sha256(khóa) theo kid để kiểm cấu hình."""

    kid: int
    key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if len(self.key) < 32:
            raise ValueError("khóa HMAC audit phải ≥ 32 byte")


AuditHmacs = Mapping[str, Mapping[str, str]]  # {"new"|"old": {cột: "hmac:kid:hex"}}


def set_audit_hmacs(conn_or_cur: Any, hmacs: AuditHmacs | None) -> None:
    """Chỉ gọi trong transaction ghi field sensitive. Giá trị là HMAC, không phải khóa."""
    if hmacs:
        conn_or_cur.execute(AUDIT_HMAC_SQL, (json.dumps({k: dict(v) for k, v in hmacs.items()}),))


def _configure(conn: psycopg.Connection[Any]) -> None:
    conn.autocommit = True


class Database:
    """Pool cho một role DB (xt_app hoặc xt_migrator)."""

    def __init__(
        self,
        conninfo: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        max_lifetime: float = 1800.0,
        max_idle: float = 300.0,
        statement_timeout_ms: int = 15000,
        audit_key: AuditKey | None = None,
    ) -> None:
        self.statement_timeout_ms = statement_timeout_ms
        self.audit_key = audit_key
        self.pool: ConnectionPool[psycopg.Connection[Any]] = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            max_lifetime=max_lifetime,
            max_idle=max_idle,
            # Không prepare phía server: DDL của model engine đổi kiểu kết quả của `SELECT *`/
            # `RETURNING *` → "cached plan must not change result type" ngay sau apply.
            kwargs={"autocommit": True, "prepare_threshold": None},
            configure=_configure,
            check=ConnectionPool.check_connection,
            open=True,
        )

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with self.pool.connection() as conn:
            yield conn

    @contextmanager
    def tenant_tx(
        self, actor: ModelActor, *, workflow: bool = False, audit_hmacs: AuditHmacs | None = None
    ) -> Iterator[psycopg.Cursor[DictRow]]:
        """Một transaction với ngữ cảnh RLS của `actor`. `workflow=True`: SET LOCAL ROLE xt_workflow
        — đường DUY NHẤT ghi được cột managed_by=workflow (chỉ module workflow được gọi)."""
        with self.pool.connection() as conn:
            if conn.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("kết nối không ở trạng thái IDLE trước tenant transaction")
            with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    sql.SQL("SET LOCAL statement_timeout = {}").format(
                        sql.Literal(f"{self.statement_timeout_ms}ms")
                    )
                )
                # Ngữ cảnh RBAC chỉ đặt qua hàm SECURITY DEFINER (chỉ xt_app gọi được); policy đọc
                # bảng ngữ cảnh khóa theo (pid, xid), không đọc GUC client tự đặt.
                set_tenant_context(cur, actor)
                set_audit_hmacs(cur, audit_hmacs)
                if workflow:
                    cur.execute("SET LOCAL ROLE xt_workflow")
                yield cur
