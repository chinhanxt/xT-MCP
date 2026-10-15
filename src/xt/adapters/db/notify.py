# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Durable tenant inbox, outbox and one-use Telegram account linking.

All ordinary operations use the authenticated actor's tenant transaction. The bot's two narrow
SECURITY DEFINER lookups derive that context from a random link capability or an already linked
private Telegram identity; no tenant/user identity supplied by a Telegram command is trusted.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

from psycopg.types.json import Jsonb

from xt.application.notify.ports import Notification, Person
from xt.application.notify.service import SAFE_SUMMARY
from xt.application.tools.errors import ToolError, invalid_request
from xt.contracts.tools import ErrorCode
from xt.domain.model.context import ModelActor, TenantRef


class _Uow(Protocol):
    def tenant_tx(self, actor: ModelActor) -> Any: ...


def _json(row: Notification) -> dict[str, Any]:
    result = asdict(row)
    result["id"] = str(row.id)
    result["recipient_id"] = str(row.recipient_id)
    return result


def _row(data: Mapping[str, Any]) -> Notification:
    values = dict(data)
    values["id"] = UUID(str(values["id"]))
    values["recipient_id"] = UUID(str(values["recipient_id"]))
    return Notification(**values)


class PgNotificationRepository:
    def __init__(self, uow: _Uow, actor: ModelActor, *, per_recipient_limit: int = 30) -> None:
        self.uow = uow
        self.actor = actor
        self.per_recipient_limit = per_recipient_limit

    def resolve_person(self, ref: str) -> Person | None:
        if ref == "me":
            value = str(self.actor.actor_id)
        else:
            parts = ref.split(":")
            if len(parts) == 4:
                if parts[0] != "xrn" or parts[1] != str(self.actor.tenant.id):
                    # Public tenant aliases are handled by the composition reference parser.
                    return None
                parts = parts[2:]
            if len(parts) != 2 or parts[0] != "person":
                return None
            value = parts[1]
        with self.uow.tenant_tx(self.actor) as cur:
            data = cur.execute(
                "SELECT id,key,name FROM xt_meta.person WHERE active AND (id::text=%s OR key=%s)",
                (value, value),
            ).fetchone()
        return Person(data["id"], f"person:{data['id']}", data["name"]) if data else None

    def enqueue(
        self, actor_id: UUID, key: str, digest: str, rows: list[Notification]
    ) -> list[Notification]:
        if actor_id != self.actor.actor_id:
            raise PermissionError("Notification actor differs from authenticated actor.")
        with self.uow.tenant_tx(self.actor) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"notify:{self.actor.tenant.id}",),
            )
            previous = cur.execute(
                "SELECT digest,result FROM xt_meta.notification_request WHERE actor=%s AND key=%s",
                (actor_id, key),
            ).fetchone()
            if previous:
                if previous["digest"] != digest:
                    raise ToolError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "Notification key already identifies another request.",
                    )
                return [_row(value) for value in previous["result"]]
            for row in rows:
                if not cur.execute(
                    "SELECT 1 FROM xt_meta.person WHERE id=%s AND active", (row.recipient_id,)
                ).fetchone():
                    raise ToolError(ErrorCode.NOT_FOUND, "Notification recipient was not found.")
                # Count outbox rows, not inbox SELECT: sender cannot read another recipient's inbox.
                count = cur.execute(
                    "SELECT xt.notification_rate(%s) AS n",
                    (row.recipient_id,),
                ).fetchone()["n"]
                if count >= self.per_recipient_limit:
                    raise ToolError(
                        ErrorCode.RATE_LIMITED,
                        "Recipient notification limit reached.",
                        details={"retry_after_seconds": 60},
                    )
            for row in rows:
                cur.execute(
                    "INSERT INTO xt_meta.notification(tenant_id,id,recipient,title,ref,dedupe_key,"
                    "kind,confirmation_code,confirm_url)"
                    " VALUES(xt.tenant(),%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        row.id,
                        row.recipient_id,
                        row.message,
                        row.subject_ref,
                        f"notify:{actor_id}:{key}",
                        row.kind,
                        row.confirmation_code,
                        row.confirm_url,
                    ),
                )
                cur.execute(
                    "INSERT INTO xt_meta.notification_delivery(id,actor,recipient,payload)"
                    " VALUES(%s,%s,%s,%s)",
                    (row.id, actor_id, row.recipient_id, Jsonb(_json(row))),
                )
            cur.execute(
                "INSERT INTO xt_meta.notification_request(actor,key,digest,result)"
                " VALUES(%s,%s,%s,%s)",
                (actor_id, key, digest, Jsonb([_json(r) for r in rows])),
            )
        return rows

    def inbox(self, *, open_only: bool = True, limit: int = 100) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(self.actor) as cur:
            rows = cur.execute(
                "SELECT id,title,ref,kind,confirmation_code,confirm_url,created_at,read_at"
                " FROM xt_meta.notification WHERE recipient=xt.actor()"
                " AND (NOT %s OR read_at IS NULL) ORDER BY created_at DESC,id LIMIT %s",
                (open_only, min(max(limit, 1), 1000)),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending(self, limit: int) -> list[tuple[Notification, str]]:
        with self.uow.tenant_tx(self.actor) as cur:
            rows = cur.execute(
                "SELECT d.id,d.payload,l.chat_id FROM xt_meta.notification_delivery d"
                " JOIN xt_meta.telegram_link l ON l.tenant_id=d.tenant_id AND l.person=d.recipient"
                " JOIN xt_meta.person p ON p.tenant_id=d.tenant_id AND p.id=d.recipient"
                " WHERE d.state='queued' AND p.active ORDER BY d.updated_at LIMIT %s"
                " FOR UPDATE OF d SKIP LOCKED",
                (min(max(limit, 1), 1000),),
            ).fetchall()
            for row in rows:
                cur.execute(
                    "UPDATE xt_meta.notification_delivery SET state='sending',"
                    " attempts=attempts+1,updated_at=clock_timestamp() WHERE id=%s",
                    (row["id"],),
                )
        return [(_row(r["payload"]), r["chat_id"]) for r in rows]

    def egress(self, row: Notification, outcome: str) -> None:
        with self.uow.tenant_tx(self.actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.pii_egress(tenant_id,trace_id,tool,client_class,mode,fields,"
                "person,outcome,tokens,records)"
                " VALUES(xt.tenant(),%s,'notify','telegram','omitted',"
                "ARRAY['message','recipient.name'],%s,%s,%s,%s)",
                (
                    self.actor.trace_id or str(row.id),
                    row.recipient_id,
                    outcome,
                    [row.confirmation_code or str(row.id)],
                    [],
                ),
            )

    def delivered(self, notification_id: UUID, external_id: str) -> None:
        with self.uow.tenant_tx(self.actor) as cur:
            cur.execute(
                "UPDATE xt_meta.notification_delivery SET state='sent',external_id=%s,"
                " updated_at=clock_timestamp() WHERE id=%s AND state='sending'",
                (external_id, notification_id),
            )

    def failed(self, notification_id: UUID) -> None:
        # Telegram has no idempotency key. Ambiguous attempts are not retried automatically.
        with self.uow.tenant_tx(self.actor) as cur:
            cur.execute(
                "UPDATE xt_meta.notification_delivery SET state='failed',"
                "updated_at=clock_timestamp() WHERE id=%s AND state='sending'",
                (notification_id,),
            )

    def mint_link_code(self, *, ttl_seconds: int = 600) -> str:
        if not 30 <= ttl_seconds <= 900:
            raise invalid_request("Link code lifetime must be between 30 and 900 seconds.")
        code = secrets.token_urlsafe(32)
        digest = hashlib.sha256(code.encode()).digest()
        with self.uow.tenant_tx(self.actor) as cur:
            if not cur.execute(
                "SELECT 1 FROM xt_meta.person WHERE id=xt.actor() AND active"
            ).fetchone():
                raise PermissionError("Only an active authenticated person may link Telegram.")
            cur.execute(
                "INSERT INTO xt_meta.telegram_link_code(hash,person,expires_at)"
                " VALUES(%s,xt.actor(),%s)",
                (digest, datetime.now(UTC) + timedelta(seconds=ttl_seconds)),
            )
        return code


class PgTelegramDirectory:
    """Webhook backend: verifies one-use capability or an already bound Telegram identity."""

    def __init__(self, db: Any) -> None:
        self.db = db

    def consume(self, code: str, telegram_user_id: str, chat_id: str) -> bool:
        if not 40 <= len(code) <= 64 or str(telegram_user_id) != str(chat_id):
            return False
        digest = hashlib.sha256(code.encode()).digest()
        with self.db.connection() as conn, conn.cursor() as cur:
            context = cur.execute(
                "SELECT seq,id,person FROM xt.telegram_code_context(%s)", (digest,)
            ).fetchone()
        if not context:
            return False
        actor = ModelActor(TenantRef(context[0], context[1]), context[2], ())
        with self.db.tenant_tx(actor) as cur:
            used = cur.execute(
                "UPDATE xt_meta.telegram_link_code SET used_at=clock_timestamp()"
                " WHERE hash=%s AND used_at IS NULL AND expires_at>clock_timestamp()"
                " RETURNING person",
                (digest,),
            ).fetchone()
            if not used:
                return False
            cur.execute(
                "INSERT INTO xt_meta.telegram_link(person,telegram_user_id,chat_id)"
                " VALUES(%s,%s,%s) ON CONFLICT(tenant_id,person) DO UPDATE"
                " SET telegram_user_id=excluded.telegram_user_id,chat_id=excluded.chat_id,"
                " linked_at=clock_timestamp()",
                (actor.actor_id, str(telegram_user_id), str(chat_id)),
            )
        return True

    def get_summary(
        self, telegram_user_id: str, chat_id: str, reference: str
    ) -> Mapping[str, str | None] | None:
        if str(telegram_user_id) != str(chat_id) or len(reference) > 128:
            return None
        with self.db.connection() as conn:
            context = conn.execute(
                "SELECT seq,id,person FROM xt.telegram_summary_context(%s,%s,%s)",
                (str(telegram_user_id), str(chat_id), reference),
            ).fetchone()
        if not context:
            return None
        actor = ModelActor(TenantRef(context[0], context[1]), context[2], ())
        with self.db.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT id,confirmation_code,confirm_url FROM xt_meta.notification"
                " WHERE recipient=xt.actor() AND (id::text=%s OR confirmation_code=%s)",
                (reference, reference),
            ).fetchone()
        if not row:
            return None
        PgNotificationRepository(self.db, actor).egress(
            Notification(
                row["id"],
                actor.actor_id,
                "me",
                "",
                "",
                confirmation_code=row["confirmation_code"],
                confirm_url=row["confirm_url"],
            ),
            "summary_attempted",
        )
        return {
            "summary": SAFE_SUMMARY,
            "reference": row["confirmation_code"] or str(row["id"]),
            "confirm_url": row["confirm_url"],
        }

    def claim_update(self, update_id: int) -> bool:
        with self.db.connection() as conn:
            row = conn.execute("SELECT xt.telegram_claim_update(%s)", (update_id,)).fetchone()
        return bool(row and row[0])

    def release_update(self, update_id: int) -> None:
        with self.db.connection() as conn:
            conn.execute("SELECT xt.telegram_release_update(%s)", (update_id,))
