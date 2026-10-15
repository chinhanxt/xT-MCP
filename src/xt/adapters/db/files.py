# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`FileRepository` trên `xt_meta.file_object` / `xt_meta.file_event` (migration 0009_files.sql).

Mọi truy vấn đi qua transaction tenant của UoW (`xt_app`, RLS `tenant_id = xt.tenant()`): tệp của
tenant khác không bao giờ hiện ra, kể cả khi đoán đúng id. Audit hash-chain do trigger
`xt_audit_meta` lo (tên tệp chỉ lưu sha256 trong audit).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from typing import Any, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb

from xt.application.files.ports import FileRow
from xt.domain.model.context import ModelActor

_COLS = (
    "id, name, declared_type, status, created_by, client_id, created_at, content_type,"
    " size_bytes, sha256, object_key, upload_expires_at, finalized_at, scan_signature, deleted_at"
)


class _Uow(Protocol):
    def tenant_tx(self, actor: ModelActor) -> AbstractContextManager[Any]: ...


def _row(r: Mapping[str, Any]) -> FileRow:
    return FileRow(**{k: r[k] for k in _COLS.replace(" ", "").split(",")})


class PgFileRepository:
    """Gắn với `actor` của lời gọi (người thật + client + trace cho `file_event`)."""

    def __init__(self, uow: _Uow, actor: ModelActor) -> None:
        self.uow = uow
        self.actor = actor

    def _tx(self) -> AbstractContextManager[Any]:
        return self.uow.tenant_tx(self.actor)

    def tenant_id(self) -> UUID:
        return self.actor.tenant.id

    def insert(self, row: FileRow) -> None:
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO xt_meta.file_object (id, name, declared_type, status, created_by,"
                " client_id, created_at, upload_expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    row.id,
                    row.name,
                    row.declared_type,
                    row.status,
                    row.created_by,
                    row.client_id,
                    row.created_at,
                    row.upload_expires_at,
                ),
            )

    def get(self, file_id: UUID, *, lock: bool = False) -> FileRow | None:
        q = f"SELECT {_COLS} FROM xt_meta.file_object WHERE id = %s AND deleted_at IS NULL"
        with self._tx() as cur:
            r = cur.execute(q + (" FOR UPDATE" if lock else ""), (file_id,)).fetchone()
        return _row(r) if r else None

    def update(self, row: FileRow) -> None:
        with self._tx() as cur:
            cur.execute(
                "UPDATE xt_meta.file_object SET status = %s, content_type = %s, size_bytes = %s,"
                " sha256 = %s, object_key = %s, upload_expires_at = %s, finalized_at = %s,"
                " scan_signature = %s, deleted_at = %s, updated_at = clock_timestamp()"
                " WHERE id = %s",
                (
                    row.status,
                    row.content_type,
                    row.size_bytes,
                    row.sha256,
                    row.object_key,
                    row.upload_expires_at,
                    row.finalized_at,
                    row.scan_signature,
                    row.deleted_at,
                    row.id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError("file_object không cập nhật được (RLS / không tồn tại)")

    def event(self, file_id: UUID, action: str, detail: Mapping[str, Any]) -> None:
        a = self.actor
        with self._tx() as cur:
            cur.execute(
                "INSERT INTO xt_meta.file_event (file_id, action, person, client_id, trace_id,"
                " detail) VALUES (%s,%s,%s,%s,%s,%s)",
                (file_id, action, a.actor_id, a.client_azp, a.trace_id, Jsonb(dict(detail))),
            )

    def scanning(self, limit: int) -> list[FileRow]:
        with self._tx() as cur:
            rows = cur.execute(
                f"SELECT {_COLS} FROM xt_meta.file_object WHERE status = 'scanning'"
                " AND deleted_at IS NULL ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            ).fetchall()
        return [_row(r) for r in rows]

    def created_by(self, person: UUID, limit: int) -> list[FileRow]:
        with self._tx() as cur:
            rows = cur.execute(
                f"SELECT {_COLS} FROM xt_meta.file_object WHERE created_by = %s"
                " AND deleted_at IS NULL ORDER BY created_at DESC LIMIT %s",
                (person, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def events(self, file_id: UUID) -> Iterator[Mapping[str, Any]]:
        """Nhật ký của một tệp (đọc cho quản trị/test)."""
        with self._tx() as cur:
            rows = cur.execute(
                "SELECT action, person, client_id, detail, at FROM xt_meta.file_event"
                " WHERE file_id = %s ORDER BY at, id",
                (file_id,),
            ).fetchall()
        yield from rows
