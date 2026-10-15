# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tenant-scoped notification source checkpoints; committed with inbox writes."""

from __future__ import annotations

from typing import Any

from xt.domain.model.context import ModelActor


class NotificationSources:
    def __init__(self, uow: Any, actor: ModelActor) -> None:
        self.uow, self.actor = uow, actor

    def claim(self, key: str) -> bool:
        with self.uow.tenant_tx(self.actor) as cur:
            row = cur.execute(
                "INSERT INTO xt_meta.notification_source(tenant_id, source_key)"
                " VALUES (xt.tenant(), %s) ON CONFLICT DO NOTHING RETURNING source_key",
                (key,),
            ).fetchone()
        return row is not None

    def process_events(self) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(self.actor) as cur:
            rows = cur.execute(
                "SELECT e.id, e.type, e.payload, r.ref FROM xt_meta.process_event e"
                " LEFT JOIN xt_meta.workflow_request r ON r.tenant_id=e.tenant_id"
                " AND r.instance_id=e.instance_id"
                " WHERE e.type IN ('assignment','assignment.created','assignment.assigned',"
                " 'assignment.escalated','escalation','confirmation_required')"
                " AND NOT EXISTS (SELECT 1 FROM xt_meta.notification_source s"
                " WHERE s.tenant_id=e.tenant_id AND s.source_key='event:' || e.id::text)"
                " ORDER BY e.created_at, e.id LIMIT 500"
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_confirmations(self) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(self.actor) as cur:
            rows = cur.execute(
                "SELECT confirmation_id, pending FROM xt_meta.tool_operation"
                " WHERE pending_status IN ('pending','awaiting_second_approver')"
                " AND expires_at > clock_timestamp()"
                " AND NOT EXISTS (SELECT 1 FROM xt_meta.notification_source s"
                " WHERE s.tenant_id=xt.tenant() AND s.source_key='confirm:' ||"
                " confirmation_id || ':' || jsonb_array_length(pending->'approvals')::text)"
                " ORDER BY expires_at LIMIT 500"
            ).fetchall()
        return [dict(row) for row in rows]

    def open_instances(self) -> list[str]:
        with self.uow.tenant_tx(self.actor) as cur:
            rows = cur.execute(
                "SELECT id FROM xt_meta.workflow_instance WHERE status='running' ORDER BY id"
            ).fetchall()
        return [str(row["id"]) for row in rows]
