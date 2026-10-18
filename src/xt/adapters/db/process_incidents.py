# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Read-only, tenant-scoped selection of replayable disabled-app incidents."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from xt.application.process.service import replayable_incident
from xt.domain.model.context import TenantRef


def app_incidents_for(
    instance: str, state: Mapping[str, Any], pack: str, models: Sequence[str]
) -> list[dict[str, str]]:
    out = []
    for incident in state.get("meta", {}).get("incidents", []):
        replay = incident.get("retry")
        if (
            not replayable_incident(incident)
            or incident.get("pack") != pack
            or incident.get("model") not in models
            or not incident.get("id")
            or not isinstance(replay, dict)
            or replay.get("operation") not in ("act", "tick")
        ):
            continue
        canonical = json.dumps(replay, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        out.append(
            {
                "instance": instance,
                "incident": str(incident["id"]),
                "model": str(incident["model"]),
                "pack": pack,
                "reason": "app_disabled",
                "replay_digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
            }
        )
    return sorted(out, key=lambda i: i["incident"])


def list_app_incidents(
    conn: psycopg.Connection[Any], tenant: TenantRef, pack: str, models: Sequence[str]
) -> list[dict[str, str]]:
    if not models:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            "SELECT id,state FROM xt_meta.workflow_instance WHERE tenant_id=%s"
            " AND status='running' ORDER BY id",
            (tenant.id,),
        ).fetchall()
    return [
        i for row in rows for i in app_incidents_for(str(row["id"]), row["state"], pack, models)
    ]
