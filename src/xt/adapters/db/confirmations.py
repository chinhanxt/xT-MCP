# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Durable tool receipts, confirmation bindings and replay protection.

The composition root binds a trusted ModelActor with ``uow.scope(actor)``. Pass this
same unit of work to record/catalog adapters as their Database. Wrapping step-up,
receipt consumption and ConfirmationService in ``uow.run`` commits their writes
and the existing database audit triggers together. No repository uses a privileged
connection or infers tenant context from client supplied tool arguments.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, TypeVar
from uuid import UUID

from psycopg import sql
from psycopg.errors import LockNotAvailable
from psycopg.pq import TransactionStatus
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from xt.adapters.db.connection import AuditKey, Database, set_tenant_context
from xt.application.tools.context import Actor, ClientClass, PersonRef
from xt.application.tools.ports import (
    IN_PROGRESS,
    ClaimBusy,
    OperationKey,
    PendingOperation,
    PendingStatus,
    Receipt,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import PrincipalKind
from xt.domain.model.context import ModelActor

T = TypeVar("T")
_INTRANS = TransactionStatus.INTRANS


def _set_context(cur: Any, actor: ModelActor) -> None:
    set_tenant_context(cur, actor)


class PgUnitOfWork:
    """Request-local actor and transaction binding; compatible with Database.tenant_tx.

    ``run`` always creates a savepoint when nested. A failed mutation can therefore
    be reported by ConfirmationService without committing half the mutation.
    ``tenant_tx`` refuses cross-actor nesting and temporarily changes the workflow
    role only while requested. Each instance owns its ContextVars.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._actor: ContextVar[ModelActor] = ContextVar("xt_confirmation_actor")
        self._cursor: ContextVar[Any] = ContextVar("xt_confirmation_cursor", default=None)

    @property
    def audit_key(self) -> AuditKey | None:
        """Khóa HMAC audit của pool bên dưới (repository tính HMAC cột sensitive, R2-01)."""
        return self.db.audit_key

    @property
    def actor(self) -> ModelActor:
        try:
            return self._actor.get()
        except LookupError as exc:
            raise RuntimeError("trusted actor scope is required") from exc

    @property
    def current_cursor(self) -> Any:
        cur = self._cursor.get()
        if cur is None:
            raise RuntimeError("an active unit-of-work transaction is required")
        return cur

    @property
    def in_transaction(self) -> bool:
        return self._cursor.get() is not None

    @contextmanager
    def scope(self, actor: ModelActor) -> Iterator[None]:
        previous = self._actor.get(None)
        if self.in_transaction and previous != actor:
            raise RuntimeError("cannot change actor inside a transaction")
        token = self._actor.set(actor)
        try:
            yield
        finally:
            self._actor.reset(token)

    @contextmanager
    def tenant_tx(self, actor: ModelActor, *, workflow: bool = False) -> Iterator[Any]:
        bound = self._actor.get(None)
        cur = self._cursor.get()
        if cur is None:
            if bound is not None and bound != actor:
                raise RuntimeError("transaction actor differs from trusted request scope")
            with self.scope(actor), self.db.tenant_tx(actor, workflow=workflow) as opened:
                token = self._cursor.set(opened)
                try:
                    yield opened
                finally:
                    self._cursor.reset(token)
            return
        if bound is not None and bound.tenant != actor.tenant:
            raise RuntimeError("transaction tenant differs from trusted request scope")
        switch = bound is not None and bound != actor
        previous = cur.execute("SELECT current_user AS role").fetchone()["role"]
        if switch:
            # Cùng tenant, khác vai (vd: đường workflow ghi trạng thái thay người duyệt). Ngữ cảnh
            # RLS đổi tạm thời trong cùng transaction rồi khôi phục, kể cả khi lỗi.
            _set_context(cur, actor)
        if workflow:
            cur.execute("SET LOCAL ROLE xt_workflow")
        try:
            if switch:
                with self.scope_nested(actor):
                    yield cur
            else:
                yield cur
        finally:
            if cur.connection.info.transaction_status == _INTRANS:
                if workflow:
                    cur.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(previous)))
                if switch and bound is not None:
                    _set_context(cur, bound)

    @contextmanager
    def scope_nested(self, actor: ModelActor) -> Iterator[None]:
        token = self._actor.set(actor)
        try:
            yield
        finally:
            self._actor.reset(token)

    def run(self, fn: Callable[[], T]) -> T:
        cur = self._cursor.get()
        if cur is not None:
            with cur.connection.transaction():
                return fn()
        with self.tenant_tx(self.actor):
            return fn()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        """Connection access for compatible readers, sharing an active transaction."""
        if self.in_transaction:
            yield self.current_cursor.connection
        else:
            with self.db.connection() as conn:
                yield conn


def _key(tenant_id: UUID, key: OperationKey) -> tuple[UUID, str, str, str]:
    return tenant_id, key.actor, key.tool, key.idempotency_key


def _pending_json(op: PendingOperation) -> dict[str, Any]:
    result = asdict(op)
    result["actor"]["scopes"] = sorted(op.actor.scopes)
    result["created_at"] = op.created_at.isoformat()
    result["expires_at"] = op.expires_at.isoformat()
    return result


def _pending(data: Mapping[str, Any]) -> PendingOperation:
    actor = data["actor"]
    return PendingOperation(
        confirmation_id=data["confirmation_id"],
        key=OperationKey(**data["key"]),
        tool=data["tool"],
        arguments=data["arguments"],
        fingerprint=data["fingerprint"],
        actor=Actor(
            **{**actor, "kind": PrincipalKind(actor["kind"]), "scopes": frozenset(actor["scopes"])}
        ),
        client_class=ClientClass(data["client_class"]),
        plan_digest=data["plan_digest"],
        plan_summary=data["plan_summary"],
        level=RiskLevel(data["level"]),
        confirmers=tuple(PersonRef(**person) for person in data["confirmers"]),
        created_at=datetime.fromisoformat(data["created_at"]),
        expires_at=datetime.fromisoformat(data["expires_at"]),
        status=PendingStatus(data["status"]),
        approvals=tuple(data["approvals"]),
    )


def _receipt_json(receipt: Receipt) -> dict[str, Any]:
    result = asdict(receipt)
    result["committed_at"] = receipt.committed_at.isoformat()
    return result


def _receipt(data: Mapping[str, Any]) -> Receipt:
    return Receipt(
        key=OperationKey(**data["key"]),
        fingerprint=data["fingerprint"],
        operation_ref=data["operation_ref"],
        commit_state=data["commit_state"],
        effective_level=RiskLevel(data["effective_level"]),
        data=data["data"],
        committed_at=datetime.fromisoformat(data["committed_at"]),
    )


class PgOperationStore:
    """Idempotency claim-before-execute (docs/integration-notes.md, audit MCP H1).

    `OperationKey.tenant` là tenant ở bề mặt tool (alias Organization); `tenant_of` ánh xạ nó sang
    tenant id và phải trùng tenant của transaction (fail-closed). `claim` chèn hàng `in_progress`
    theo PRIMARY KEY (tenant, actor, tool, key) TRONG transaction của lời gọi: transaction khác cùng
    key bị chặn trên unique index tới khi transaction đầu commit/rollback (quá `lock_timeout_ms`
    -> `IN_PROGRESS`). Rollback = nhả claim.
    """

    def __init__(
        self, uow: PgUnitOfWork, tenant_of: Callable[[str], UUID], *, lock_timeout_ms: int = 5000
    ) -> None:
        self.uow = uow
        self.tenant_of = tenant_of
        self.lock_timeout_ms = lock_timeout_ms

    def _tenant(self, tenant: str) -> UUID:
        tid = self.tenant_of(tenant)
        if tid != self.uow.actor.tenant.id:
            raise ValueError("operation tenant differs from trusted request tenant")
        return tid

    def _check_key(self, key: OperationKey) -> tuple[UUID, str, str, str]:
        return _key(self._tenant(key.tenant), key)

    @staticmethod
    def _decode(row: Mapping[str, Any]) -> Receipt | PendingOperation | None:
        if row["receipt"]:
            return _receipt(row["receipt"])
        if row["pending"]:
            return _pending(row["pending"])
        return None

    def claim(
        self, key: OperationKey, fingerprint: str, now: datetime
    ) -> Receipt | PendingOperation | ClaimBusy | None:
        k = self._check_key(key)
        cur = self.uow.current_cursor
        conn = cur.connection
        try:
            with conn.transaction():
                old = cur.execute("SELECT current_setting('lock_timeout') AS v").fetchone()["v"]
                cur.execute(
                    "SELECT set_config('lock_timeout', %s, true)", (f"{self.lock_timeout_ms}ms",)
                )
                row = cur.execute(
                    "INSERT INTO xt_meta.tool_operation"
                    " (tenant_id,actor_key,tool,idempotency_key,fingerprint,state)"
                    " VALUES (%s,%s,%s,%s,%s,'in_progress') ON CONFLICT DO NOTHING RETURNING id",
                    (*k, fingerprint),
                ).fetchone()
                existing = None
                if row is None:
                    # Hàng đã có: đọc KÈM KHÓA để chờ transaction đang giữ nó (claim khác hoặc
                    # ConfirmationService.lock_pending) commit rồi mới thấy receipt (R2-M3).
                    existing = cur.execute(
                        "SELECT state, pending, receipt FROM xt_meta.tool_operation"
                        " WHERE tenant_id=%s AND actor_key=%s AND tool=%s AND idempotency_key=%s"
                        " FOR UPDATE",
                        k,
                    ).fetchone()
                cur.execute("SELECT set_config('lock_timeout', %s, true)", (old,))
        except LockNotAvailable:
            return IN_PROGRESS
        if row is not None:
            return None
        if existing is None:  # pragma: no cover - hàng vừa xung đột phải còn đó
            raise RuntimeError("claimed operation vanished")
        decoded = self._decode(existing)
        return IN_PROGRESS if decoded is None else decoded

    def save_pending(self, op: PendingOperation) -> None:
        k = self._check_key(op.key)
        result = self.uow.current_cursor.execute(
            "UPDATE xt_meta.tool_operation SET state='pending', confirmation_id=%s, pending=%s,"
            " pending_status=%s, expires_at=%s WHERE tenant_id=%s AND actor_key=%s AND tool=%s"
            " AND idempotency_key=%s AND state='in_progress' AND fingerprint=%s",
            (
                op.confirmation_id,
                Jsonb(_pending_json(op)),
                op.status.value,
                op.expires_at,
                *k,
                op.fingerprint,
            ),
        )
        if result.rowcount != 1:
            raise RuntimeError("save_pending without a held claim")

    def save_receipt(self, receipt: Receipt) -> None:
        k = self._check_key(receipt.key)
        result = self.uow.current_cursor.execute(
            "UPDATE xt_meta.tool_operation SET state='done', receipt=%s WHERE tenant_id=%s"
            " AND actor_key=%s AND tool=%s AND idempotency_key=%s AND receipt IS NULL"
            " AND fingerprint=%s",
            (Jsonb(_receipt_json(receipt)), *k, receipt.fingerprint),
        )
        if result.rowcount != 1:
            raise RuntimeError("save_receipt without a held claim or pending operation")

    def update_pending(self, op: PendingOperation) -> bool:
        """Chỉ ghi khi hàng còn mở; đã có receipt thì chỉ nhận CONFIRMED (receipt vừa ghi cùng
        transaction xác nhận). False = không ghi, không bao giờ đè trạng thái đã đóng (R2-M3)."""
        k = self._check_key(op.key)
        with self.uow.tenant_tx(self.uow.actor) as cur:
            result = cur.execute(
                "UPDATE xt_meta.tool_operation SET pending=%s,pending_status=%s"
                " WHERE tenant_id=%s AND actor_key=%s AND tool=%s AND idempotency_key=%s"
                " AND confirmation_id=%s AND fingerprint=%s"
                " AND pending_status IN ('pending','awaiting_second_approver')"
                " AND (receipt IS NULL OR %s = 'confirmed')",
                (
                    Jsonb(_pending_json(op)),
                    op.status.value,
                    *k,
                    op.confirmation_id,
                    op.fingerprint,
                    op.status.value,
                ),
            )
            return bool(result.rowcount == 1)

    def _pending_row(self, confirmation_id: str, lock: bool) -> PendingOperation | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            row = cur.execute(
                "SELECT pending FROM xt_meta.tool_operation WHERE confirmation_id=%s"
                + (" FOR UPDATE" if lock else ""),
                (confirmation_id,),
            ).fetchone()
        return _pending(row["pending"]) if row and row["pending"] else None

    def get_pending(self, confirmation_id: str) -> PendingOperation | None:
        return self._pending_row(confirmation_id, False)

    def lock_pending(self, confirmation_id: str) -> PendingOperation | None:
        return self._pending_row(confirmation_id, True)

    def count_open_pending(self, tenant: str, actor: str, now: datetime) -> int:
        tid = self._tenant(tenant)
        with self.uow.tenant_tx(self.uow.actor) as cur:
            row = cur.execute(
                "SELECT count(*) AS n FROM xt_meta.tool_operation WHERE tenant_id=%s"
                " AND actor_key=%s AND pending_status IN ('pending','awaiting_second_approver')"
                " AND expires_at>%s",
                (tid, actor, now),
            ).fetchone()
        assert row is not None
        return int(row["n"])


class PgSingleUseStore:
    """Atomic uniqueness persists across processes; None expiry is never reclaimed."""

    def __init__(self, uow: PgUnitOfWork) -> None:
        self.uow = uow

    def consume(self, key: str, expires_at: int | None) -> bool:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            row = cur.execute(
                "INSERT INTO xt_meta.single_use_key (key,tenant_id,expires_at) VALUES (%s,%s,%s)"
                " ON CONFLICT (key) DO UPDATE SET expires_at=EXCLUDED.expires_at"
                " WHERE xt_meta.single_use_key.tenant_id=xt.tenant()"
                " AND xt_meta.single_use_key.expires_at IS NOT NULL"
                " AND xt_meta.single_use_key.expires_at<=extract(epoch FROM clock_timestamp())"
                " RETURNING key",
                (key, self.uow.actor.tenant.id, expires_at),
            ).fetchone()
        return row is not None


@dataclass(frozen=True, slots=True)
class StoredConfirmationBinding:
    confirmation_id: str
    nonce: str
    request_digest: str
    requester_subject: str
    created_at: datetime
    expires_at: datetime


def _binding(row: DictRow) -> StoredConfirmationBinding:
    return StoredConfirmationBinding(
        **{
            k: row[k]
            for k in (
                "confirmation_id",
                "nonce",
                "request_digest",
                "requester_subject",
                "created_at",
                "expires_at",
            )
        }
    )


class PgConfirmationBindingStore:
    def __init__(self, uow: PgUnitOfWork) -> None:
        self.uow = uow

    def get_or_create(
        self, op: PendingOperation, requester_subject: str
    ) -> StoredConfirmationBinding:
        if not requester_subject:
            raise ValueError("business requester subject is required")
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.confirmation_binding"
                " (confirmation_id,tenant_id,nonce,request_digest,requester_subject,"
                " created_at,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (confirmation_id) DO NOTHING",
                (
                    op.confirmation_id,
                    self.uow.actor.tenant.id,
                    secrets.token_urlsafe(32),
                    op.plan_digest,
                    requester_subject,
                    op.created_at,
                    op.expires_at,
                ),
            )
            row = cur.execute(
                "SELECT * FROM xt_meta.confirmation_binding WHERE confirmation_id=%s",
                (op.confirmation_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("confirmation binding is inaccessible")
        binding = _binding(row)
        if (
            binding.request_digest != op.plan_digest
            or binding.requester_subject != requester_subject
        ):
            raise ValueError("confirmation binding cannot be changed")
        return binding

    def get(self, confirmation_id: str) -> StoredConfirmationBinding | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            row = cur.execute(
                "SELECT * FROM xt_meta.confirmation_binding WHERE confirmation_id=%s",
                (confirmation_id,),
            ).fetchone()
        return _binding(row) if row else None


class PgConfirmationSessionStore:
    """OAuth/CSRF secrets remain server-side and tenant scoped. Use delete in the same
    transaction as callback processing; concurrent callbacks serialize on get()."""

    def __init__(self, uow: PgUnitOfWork) -> None:
        self.uow = uow

    def save(self, state: str, data: Mapping[str, Any], expires_at: datetime) -> None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.confirmation_session (state,tenant_id,data,expires_at)"
                " VALUES (%s,%s,%s,%s)",
                (state, self.uow.actor.tenant.id, Jsonb(dict(data)), expires_at),
            )

    def get(self, state: str) -> dict[str, Any] | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            row = cur.execute(
                "SELECT data FROM xt_meta.confirmation_session WHERE state=%s"
                " AND expires_at>clock_timestamp() FOR UPDATE",
                (state,),
            ).fetchone()
        return dict(row["data"]) if row else None

    def delete(self, state: str) -> None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute("DELETE FROM xt_meta.confirmation_session WHERE state=%s", (state,))
