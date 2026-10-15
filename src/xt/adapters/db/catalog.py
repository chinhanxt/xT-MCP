# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Catalog store: đọc catalog đang áp của tenant (xt_app, qua RLS), lưu đề xuất thay đổi model.

Nguồn sự thật của định nghĩa nằm trong DB (`xt_meta.catalog_version`). Catalog được cache theo
(tenant_seq, version); mỗi lời gọi chỉ tốn một truy vấn version (lệch phiên bản giữa tenant là
trạng thái hợp lệ nên luôn nạp theo tenant).
"""

from __future__ import annotations

import threading
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from xt.adapters.db.connection import Database
from xt.adapters.db.records import translate
from xt.application.model_engine.ports import StoredProposal
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.plan import Plan
from xt.domain.model.spec import Catalog


class PgCatalogReader:
    def __init__(self, db: Database, cache_size: int = 256) -> None:
        self.db = db
        self._cache: dict[tuple[int, int], Catalog] = {}
        self._lock = threading.Lock()
        self.cache_size = cache_size

    def current(self, actor: ModelActor) -> tuple[int, Catalog] | None:
        try:
            with self.db.tenant_tx(actor) as cur:
                row = cur.execute(
                    "SELECT version FROM xt_meta.catalog_version WHERE tenant_seq = %s"
                    " ORDER BY version DESC LIMIT 1",
                    (actor.tenant.seq,),
                ).fetchone()
                if row is None:
                    return None
                key = (actor.tenant.seq, int(row["version"]))
                with self._lock:
                    hit = self._cache.get(key)
                if hit is not None:
                    return key[1], hit
                spec = cur.execute(
                    "SELECT spec FROM xt_meta.catalog_version WHERE tenant_seq = %s AND version ="
                    " %s",
                    key,
                ).fetchone()
        except psycopg.Error as e:
            raise translate(e) from e
        assert spec is not None
        cat = Catalog.model_validate(spec["spec"])
        with self._lock:
            if len(self._cache) >= self.cache_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = cat
        return key[1], cat


def _proposal(row: dict[str, Any]) -> StoredProposal:
    return StoredProposal(
        id=row["id"],
        tenant_seq=int(row["tenant_seq"]),
        base_version=int(row["base_version"]),
        spec=Catalog.model_validate(row["spec"]),
        plan_hash=str(row["plan_hash"]),
        proposed_by=str(row["proposed_by"]),
        fingerprint=str(row["fingerprint"]),
        created_at=row["created_at"].isoformat(),
    )


class PgProposalStore:
    """Đề xuất bất biến. Ghi bằng xt_app (RLS theo tenant); đọc để apply bằng migrator."""

    def __init__(self, app_db: Database, migrator_db: Database) -> None:
        self.app_db, self.migrator_db = app_db, migrator_db

    def save(
        self,
        actor: ModelActor,
        *,
        base_version: int,
        spec: Catalog,
        plan: Plan,
        rationale: str,
        proposed_by: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredProposal:
        try:
            with self.app_db.tenant_tx(actor) as cur:
                row = cur.execute(
                    "INSERT INTO xt_meta.model_proposal (tenant_seq, base_version, spec, plan,"
                    " plan_hash,"
                    " rationale, proposed_by, idempotency_key, fingerprint)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                    (
                        actor.tenant.seq, base_version, Jsonb(spec.dump()), Jsonb(plan.to_json()),
                        plan.hash, rationale, proposed_by, idempotency_key, fingerprint,
                    ),
                ).fetchone()  # fmt: skip
        except psycopg.Error as e:
            raise translate(e) from e
        assert row is not None
        return _proposal(row)

    def find(
        self, actor: ModelActor, proposed_by: str, idempotency_key: str
    ) -> StoredProposal | None:
        try:
            with self.app_db.tenant_tx(actor) as cur:
                row = cur.execute(
                    "SELECT * FROM xt_meta.model_proposal WHERE tenant_seq = %s AND proposed_by ="
                    " %s"
                    " AND idempotency_key = %s",
                    (actor.tenant.seq, proposed_by, idempotency_key),
                ).fetchone()
        except psycopg.Error as e:
            raise translate(e) from e
        return _proposal(row) if row else None

    def get(self, tenant: TenantRef, proposal_id: UUID) -> StoredProposal | None:
        from psycopg.rows import dict_row

        with self.migrator_db.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "SELECT * FROM xt_meta.model_proposal WHERE tenant_seq = %s AND id = %s",
                (tenant.seq, proposal_id),
            ).fetchone()
        return _proposal(row) if row else None
