# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`FileRepository` trên `xt_meta.file_object` / `xt_meta.file_event` (migration 0009_files.sql).

Mọi truy vấn đi qua transaction tenant của UoW (`xt_app`, RLS `tenant_id = xt.tenant()`): tệp của
tenant khác không bao giờ hiện ra, kể cả khi đoán đúng id. Audit hash-chain do trigger
`xt_audit_meta` lo (tên tệp chỉ lưu sha256 trong audit).
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from xt.adapters.db.audit import lock_audit_chain
from xt.adapters.db.compiler import Layout
from xt.application.files.ports import FileRow, Usage
from xt.domain.model.audit import audit_hmac
from xt.domain.model.context import ModelActor

_COLS = (
    "id, name, declared_type, status, created_by, client_id, created_at, content_type,"
    " size_bytes, sha256, object_key, upload_expires_at, finalized_at, scan_signature, deleted_at,"
    " scan_attempts"
)
_GUC = "SELECT set_config('xt.audit_hmac', %s, true)"


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

    @contextmanager
    def _wtx(self) -> Iterator[Any]:
        """Transaction sẽ ghi audit / giữ khóa: lấy khóa chuỗi audit của tenant TRƯỚC (thứ tự toàn
        dự án như WA-11: chuỗi audit -> khóa quota/nội dung/dòng). Thiếu bước này, PUT (quota rồi
        mới audit) và tool (audit ở claim rồi mới quota) khóa ngược thứ tự -> deadlock (R3-C1).
        Advisory lock xact tái nhập được nên gọi nhiều lần trong cùng transaction không sao."""
        with self._tx() as cur:
            lock_audit_chain(cur, self.actor.tenant.id)
            yield cur

    def tenant_id(self) -> UUID:
        return self.actor.tenant.id

    def _name_hmac(self, name: str) -> str:
        """FB-04: tên tệp vào audit dưới dạng HMAC có khóa (khóa audit của tiến trình, không vào
        DB), như cột sensitive của model engine."""
        key = getattr(self.uow, "audit_key", None)
        if key is None:
            raise RuntimeError("thiếu khóa HMAC audit (AuditKey): không ghi tên tệp vào audit")
        return audit_hmac(key.key, key.kid, name)

    def insert(self, row: FileRow) -> None:
        guc = json.dumps({"meta:file_object": {"new": {"name": self._name_hmac(row.name)}}})
        with self._wtx() as cur:
            cur.execute(_GUC, (guc,))
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
            cur.execute(_GUC, ("",))  # không để HMAC rò sang ghi khác cùng transaction

    def get(self, file_id: UUID, *, lock: bool = False) -> FileRow | None:
        q = f"SELECT {_COLS} FROM xt_meta.file_object WHERE id = %s AND deleted_at IS NULL"
        with self._wtx() if lock else self._tx() as cur:
            r = cur.execute(q + (" FOR UPDATE" if lock else ""), (file_id,)).fetchone()
        return _row(r) if r else None

    def update(self, row: FileRow) -> None:
        with self._wtx() as cur:
            cur.execute(
                "UPDATE xt_meta.file_object SET status = %s, content_type = %s, size_bytes = %s,"
                " sha256 = %s, object_key = %s, upload_expires_at = %s, finalized_at = %s,"
                " scan_signature = %s, deleted_at = %s, scan_attempts = %s,"
                " updated_at = clock_timestamp()"
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
                    row.scan_attempts,
                    row.id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError("file_object không cập nhật được (RLS / không tồn tại)")

    def event(self, file_id: UUID, action: str, detail: Mapping[str, Any]) -> None:
        a = self.actor
        with self._wtx() as cur:
            cur.execute(
                "INSERT INTO xt_meta.file_event (file_id, action, person, client_id, trace_id,"
                " detail) VALUES (%s,%s,%s,%s,%s,%s)",
                (file_id, action, a.actor_id, a.client_azp, a.trace_id, Jsonb(dict(detail))),
            )

    def scanning(self, limit: int) -> list[FileRow]:
        with self._wtx() as cur:
            rows = cur.execute(
                f"SELECT {_COLS} FROM xt_meta.file_object WHERE status = 'scanning'"
                " AND deleted_at IS NULL ORDER BY created_at LIMIT %s",
                (limit,),
            ).fetchall()
        return [_row(r) for r in rows]

    def usage(self, person: UUID) -> Usage:
        """Ý định đang mở + byte đang giữ của người và của tenant (FB-02). Khóa tư vấn theo tenant
        tuần tự hóa kiểm-rồi-ghi quota trong cùng transaction."""
        with self._wtx() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('xt:files:quota:' || %s, 0))",
                (str(self.actor.tenant.id),),
            )
            r = cur.execute(
                "SELECT"
                " count(*) FILTER (WHERE pend AND created_by = %s) AS user_pending,"
                " count(*) FILTER (WHERE pend) AS tenant_pending,"
                " coalesce(sum(size_bytes) FILTER (WHERE held AND created_by = %s), 0)"
                "   AS user_bytes,"
                " coalesce(sum(size_bytes) FILTER (WHERE held), 0) AS tenant_bytes"
                " FROM (SELECT created_by, size_bytes,"
                "   deleted_at IS NULL AND status IN ('awaiting_upload', 'uploaded')"
                "     AND upload_expires_at > clock_timestamp() AS pend,"
                # R2-F1: tệp xóa mềm vẫn giữ byte (object_key còn) tới khi job dọn.
                "   status NOT IN ('expired', 'infected') AND size_bytes IS NOT NULL"
                "     AND (deleted_at IS NULL OR object_key IS NOT NULL) AS held"
                "   FROM xt_meta.file_object) f",
                (person, person),
            ).fetchone()
        return Usage(
            int(r["user_pending"]),
            int(r["tenant_pending"]),
            int(r["user_bytes"]),
            int(r["tenant_bytes"]),
        )

    def lock_content(self, sha256: str) -> None:
        """R2-F3: khóa theo (tenant, sha256) tới hết transaction. finalize (dùng lại / copy object
        theo nội dung) và purge (xóa object) cùng lấy khóa này, nên purge chỉ quyết định xóa sau
        khi mọi finalize cùng nội dung đã commit, và finalize luôn HEAD lại sau purge."""
        with self._wtx() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('xt:files:obj:' || %s || ':' || %s,"
                " 0))",
                (str(self.actor.tenant.id), sha256),
            )

    def linked_records(
        self, tenant_seq: int, fields: list[tuple[str, str]], file_id: UUID
    ) -> list[str]:
        """Bản ghi (`model:id`) mà người gọi THẤY được qua RLS có field kiểu `file` trỏ tới tệp
        (R2-F5: quyền đọc tệp đi theo quyền đọc bản ghi). `fields` = [(model, field)]."""
        lay = Layout(tenant_seq)
        out: list[str] = []
        with self._tx() as cur:
            for model, col in fields:
                q = sql.SQL("SELECT id FROM {} WHERE {} = %s LIMIT 20").format(
                    lay.table(model), sql.Identifier(col)
                )
                cur.execute("SAVEPOINT xt_files_link")
                try:
                    rows = cur.execute(q, (file_id,)).fetchall()
                except psycopg.Error:  # bảng chưa áp / không có quyền: bỏ qua model này
                    cur.execute("ROLLBACK TO SAVEPOINT xt_files_link")
                    continue
                cur.execute("RELEASE SAVEPOINT xt_files_link")
                out += [f"{model}:{r['id']}" for r in rows]
        return out

    def expired_pending(self, limit: int) -> list[FileRow]:
        with self._wtx() as cur:
            rows = cur.execute(
                f"SELECT {_COLS} FROM xt_meta.file_object"
                " WHERE status IN ('awaiting_upload', 'uploaded')"
                " AND upload_expires_at <= clock_timestamp()"
                " ORDER BY upload_expires_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (limit,),
            ).fetchall()
        return [_row(r) for r in rows]

    def purgeable_deleted(self, older_than_s: int, limit: int) -> list[FileRow]:
        """Tệp xóa mềm quá hạn lưu, còn object."""
        with self._wtx() as cur:
            rows = cur.execute(
                f"SELECT {_COLS} FROM xt_meta.file_object WHERE deleted_at IS NOT NULL"
                " AND object_key IS NOT NULL"
                " AND deleted_at <= clock_timestamp() - make_interval(secs => %s)"
                " ORDER BY deleted_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (older_than_s, limit),
            ).fetchall()
        return [_row(r) for r in rows]

    def object_in_use(self, object_key: str, except_id: UUID) -> bool:
        """Còn hàng chưa xóa (hoặc xóa mềm chưa dọn) trỏ tới cùng object theo nội dung."""
        with self._tx() as cur:
            r = cur.execute(
                "SELECT 1 FROM xt_meta.file_object WHERE object_key = %s AND id <> %s LIMIT 1",
                (object_key, except_id),
            ).fetchone()
        return r is not None

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
