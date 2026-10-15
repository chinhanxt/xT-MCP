# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""CRUD tổng quát trên bảng `xt_t<seq>.<model>` dưới role xt_app + RLS; ghi workflow qua
`SET LOCAL ROLE xt_workflow`. Lỗi Postgres → lỗi cổng (application dịch sang tiếng Việt)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg import errors as pg_errors
from psycopg import sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from xt.adapters.db.compiler import Layout, compile_expr
from xt.adapters.db.connection import Database, set_audit_hmacs
from xt.application.model_engine.ports import (
    Busy,
    ConstraintViolated,
    Page,
    PermissionDenied,
    SchemaMissing,
    StoreError,
    UpdateOutcome,
)
from xt.domain.model.audit import sensitive_hmacs
from xt.domain.model.context import ModelActor
from xt.domain.model.expr import Expr, coerce_literal
from xt.domain.model.spec import FieldSpec, ModelSpec
from xt.domain.model.values import with_system_fields

SYSTEM_OUT = ("id", "revision", "owner_id", "team_id", "department_id", "created_at", "updated_at")


def _param(v: Any) -> Any:
    return Jsonb(v) if isinstance(v, dict | list) else v


def translate(e: psycopg.Error) -> StoreError:
    d = e.diag
    if isinstance(e, pg_errors.UniqueViolation):
        return ConstraintViolated("unique", d.constraint_name, d.column_name)
    if isinstance(e, pg_errors.CheckViolation):
        return ConstraintViolated("check", d.constraint_name, d.column_name)
    if isinstance(e, pg_errors.ForeignKeyViolation):
        return ConstraintViolated("fk", d.constraint_name, d.column_name)
    if isinstance(e, pg_errors.NotNullViolation):
        return ConstraintViolated("not_null", d.constraint_name, d.column_name)
    if isinstance(e, pg_errors.InsufficientPrivilege):
        return PermissionDenied(d.message_primary or "permission denied")
    if isinstance(e, pg_errors.LockNotAvailable | pg_errors.QueryCanceled):
        return Busy(d.message_primary or "busy")
    if isinstance(e, pg_errors.UndefinedTable | pg_errors.UndefinedColumn):
        return SchemaMissing(d.message_primary or "schema missing")
    return StoreError(d.message_primary or str(e))


class PgRecordRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @contextmanager
    def _tx(
        self, actor: ModelActor, *, workflow: bool = False
    ) -> Iterator[psycopg.Cursor[DictRow]]:
        try:
            with self.db.tenant_tx(actor, workflow=workflow) as cur:
                yield cur
        except psycopg.Error as e:
            raise translate(e) from e

    def _hmacs(
        self,
        m: ModelSpec,
        *,
        new: Mapping[str, Any] | None = None,
        old: Mapping[str, Any] | None = None,
    ) -> dict[str, dict[str, str]]:
        """HMAC cho cột sensitive (tính trong tiến trình; khóa không vào DB, R2-01)."""
        sens = [f.name for f in m.fields if f.pii == "sensitive"]
        touched = [
            c for c in sens if (new and new.get(c) is not None) or (old and old.get(c) is not None)
        ]
        if not touched:
            return {}
        key = self.db.audit_key
        if key is None:
            raise StoreError("thiếu khóa HMAC audit (AuditKey) để ghi field sensitive")
        return sensitive_hmacs(key.key, key.kid, sens, new=new, old=old)

    def _old_sensitive(
        self, cur: psycopg.Cursor[DictRow], lay: Layout, m: ModelSpec, rid: UUID, rev: int,
        cols: list[str],
    ) -> dict[str, Any] | None:  # fmt: skip
        """Giá trị cũ của cột sensitive, khóa dòng trong cùng transaction (CAS theo revision)."""
        if not cols:
            return {}
        row = cur.execute(
            sql.SQL("SELECT {} FROM {} WHERE id = %s AND revision = %s FOR UPDATE").format(
                sql.SQL(", ").join(map(sql.Identifier, cols)), lay.table(m.name)
            ),
            [rid, rev],
        ).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _out(row: Mapping[str, Any], m: ModelSpec) -> dict[str, Any]:
        return {k: row[k] for k in (*SYSTEM_OUT, *(f.name for f in m.fields))}

    def insert(self, actor: ModelActor, m: ModelSpec, values: Mapping[str, Any]) -> dict[str, Any]:
        lay = Layout(actor.tenant.seq)
        cols = list(values)
        q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            lay.table(m.name),
            sql.SQL(", ").join(map(sql.Identifier, cols)),
            sql.SQL(", ").join(sql.Placeholder() * len(cols)),
        )
        new = {
            f.name: values.get(f.name, f.coerced("default"))
            for f in m.fields
            if f.pii == "sensitive"
        }
        hm = self._hmacs(m, new=new)
        with self._tx(actor) as cur:
            set_audit_hmacs(cur, hm)
            row = cur.execute(q, [_param(values[c]) for c in cols]).fetchone()
        assert row is not None
        return self._out(row, m)

    def _current_revision(
        self, cur: psycopg.Cursor[DictRow], lay: Layout, m: ModelSpec, rid: UUID
    ) -> int | None:
        r = cur.execute(
            sql.SQL("SELECT revision FROM {} WHERE id = %s").format(lay.table(m.name)), [rid]
        ).fetchone()
        return int(r["revision"]) if r else None

    def update(
        self,
        actor: ModelActor,
        m: ModelSpec,
        record_id: UUID,
        expected_revision: int,
        values: Mapping[str, Any],
        *,
        workflow: bool = False,
    ) -> UpdateOutcome:
        lay = Layout(actor.tenant.seq)
        sets = sql.SQL(", ").join(
            sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in values
        )
        q = sql.SQL("UPDATE {} SET {} WHERE id = %s AND revision = %s RETURNING *").format(
            lay.table(m.name), sets
        )
        sens = [f.name for f in m.fields if f.pii == "sensitive" and f.name in values]
        with self._tx(actor, workflow=workflow) as cur:
            if sens:
                old = self._old_sensitive(cur, lay, m, record_id, expected_revision, sens)
                if old is not None:
                    set_audit_hmacs(cur, self._hmacs(m, new={c: values[c] for c in sens}, old=old))
            row = cur.execute(
                q, [*(_param(v) for v in values.values()), record_id, expected_revision]
            ).fetchone()
            if row is not None:
                return UpdateOutcome(self._out(row, m), int(row["revision"]))
            return UpdateOutcome(None, self._current_revision(cur, lay, m, record_id))

    def delete(
        self, actor: ModelActor, m: ModelSpec, record_id: UUID, expected_revision: int
    ) -> UpdateOutcome:
        lay = Layout(actor.tenant.seq)
        q = sql.SQL("DELETE FROM {} WHERE id = %s AND revision = %s RETURNING *").format(
            lay.table(m.name)
        )
        sens = [f.name for f in m.fields if f.pii == "sensitive"]
        with self._tx(actor) as cur:
            if sens:
                old = self._old_sensitive(cur, lay, m, record_id, expected_revision, sens)
                if old is not None:
                    set_audit_hmacs(cur, self._hmacs(m, old=old))
            row = cur.execute(q, [record_id, expected_revision]).fetchone()
            if row is not None:
                return UpdateOutcome(self._out(row, m), int(row["revision"]))
            return UpdateOutcome(None, self._current_revision(cur, lay, m, record_id))

    def get(self, actor: ModelActor, m: ModelSpec, record_id: UUID) -> dict[str, Any] | None:
        lay = Layout(actor.tenant.seq)
        with self._tx(actor) as cur:
            row = cur.execute(
                sql.SQL("SELECT * FROM {} WHERE id = %s").format(lay.table(m.name)), [record_id]
            ).fetchone()
        return self._out(row, m) if row else None

    def search(
        self,
        actor: ModelActor,
        m: ModelSpec,
        where: Expr | None,
        order: Sequence[tuple[str, bool]],
        limit: int,
        offset: int,
        scope_hint: Literal["all", "scoped"],
    ) -> Page:
        lay = Layout(actor.tenant.seq)
        params: list[Any] = []

        def literal(f: FieldSpec, v: object) -> sql.Composable:
            params.append(coerce_literal(f, v))
            return sql.Placeholder()

        cond: sql.Composable = sql.SQL("true")
        if where is not None:
            cond = compile_expr(where, with_system_fields(m), literal)
        if scope_hint == "scoped":
            # Lặp lại điều kiện phạm vi dưới dạng tham số để planner dùng index owner/department
            # (SME §6). Chỉ thu hẹp; RLS vẫn là chốt chặn.
            parts: list[sql.Composable] = [sql.SQL("owner_id = %s")]
            params.append(actor.actor_id)
            if actor.department_ids:
                parts.append(sql.SQL("department_id = ANY(%s)"))
                params.append(list(actor.department_ids))
            if actor.team_ids:
                parts.append(sql.SQL("team_id = ANY(%s)"))
                params.append(list(actor.team_ids))
            cond = sql.SQL("{} AND ({})").format(cond, sql.SQL(" OR ").join(parts))
        orders: list[sql.Composable] = [
            sql.SQL("{} {}").format(sql.Identifier(col), sql.SQL("DESC" if desc else "ASC"))
            for col, desc in order
        ]
        orders.append(sql.SQL("id"))
        t = lay.table(m.name)
        with self._tx(actor) as cur:
            total_row = cur.execute(
                sql.SQL("SELECT count(*) AS n FROM {} WHERE {}").format(t, cond), params
            ).fetchone()
            rows = cur.execute(
                sql.SQL("SELECT * FROM {} WHERE {} ORDER BY {} LIMIT %s OFFSET %s").format(
                    t, cond, sql.SQL(", ").join(orders)
                ),
                [*params, limit, offset],
            ).fetchall()
        assert total_row is not None
        return Page(int(total_row["n"]), [self._out(r, m) for r in rows])
