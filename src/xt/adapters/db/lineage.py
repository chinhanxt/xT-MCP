# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Short-lived aggregate definitions; never retain result rows or personal fields."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any

from psycopg.types.json import Jsonb

from xt.adapters.db.integration import _Uow
from xt.domain.model.context import ModelActor


class PgLineageStore:
    def __init__(self, uow: _Uow) -> None:
        self.uow = uow

    def save(self, actor: ModelActor, owner_key: str, trace_id: str, query: dict[str, Any]) -> None:
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "DELETE FROM xt_meta.aggregate_explain WHERE expires_at <= clock_timestamp()"
            )
            cur.execute(
                "INSERT INTO xt_meta.aggregate_explain (tenant_id, trace_id, actor_key, query)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (tenant_id, trace_id) DO NOTHING",
                (actor.tenant.id, trace_id, sha256(owner_key.encode()).hexdigest(), Jsonb(query)),
            )

    def get(
        self, actor: ModelActor, owner_key: str, trace_id: str, now: datetime
    ) -> dict[str, Any] | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT tenant_id, actor_key, expires_at, query FROM xt_meta.aggregate_explain"
                " WHERE tenant_id = %s AND trace_id = %s",
                (actor.tenant.id, trace_id),
            ).fetchone()
        if (
            row is None
            or row["tenant_id"] != actor.tenant.id
            or row["expires_at"] <= now
            or (
                row["actor_key"] != sha256(owner_key.encode()).hexdigest()
                and "admin" not in actor.roles
            )
        ):
            return None
        return dict(row["query"])
