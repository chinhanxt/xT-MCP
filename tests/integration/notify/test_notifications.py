# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

import httpx
import pytest

from xt.adapters.db.migrations import migrate
from xt.adapters.db.notify import PgNotificationRepository, PgTelegramDirectory
from xt.adapters.notify.telegram import HttpxTelegramTransport
from xt.application.notify.ports import Person
from xt.application.notify.service import NotificationService
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.domain.model.context import ModelActor

pytestmark = pytest.mark.integration


@pytest.fixture
def people(pg: Any, app_db: Any, tenants: Any) -> tuple[Any, ModelActor, ModelActor, ModelActor]:
    a = ModelActor(tenants.a, uuid4(), ())
    b = ModelActor(tenants.a, uuid4(), ())
    other = ModelActor(tenants.b, uuid4(), ())
    with pg.migrator() as conn:
        migrate(conn)
        for person in (a, b, other):
            conn.execute(
                "INSERT INTO xt_meta.person(tenant_id,id,key,name) VALUES(%s,%s,%s,%s)",
                (person.tenant.id, person.actor_id, str(person.actor_id), "Private Name"),
            )
    return app_db, a, b, other


def test_inbox_rls_atomic_idempotency_and_cross_tenant_rejection(people: Any) -> None:
    db, a, b, other = people
    repo = PgNotificationRepository(db, a)
    svc = NotificationService(repo)
    recipient = Person(b.actor_id, f"person:{b.actor_id}", "Private Name")
    args = dict(
        actor_id=a.actor_id,
        recipients=[recipient],
        message="A private notice",
        idempotency_key="notification-test-1",
    )
    first = svc.send(**args)
    assert svc.send(**args) == first
    assert repo.inbox() == []
    target = PgNotificationRepository(db, b)
    assert [r["id"] for r in target.inbox()] == [first[0].id]
    # Guessed notification ids and another person's inbox query cannot bypass DB policy.
    with db.tenant_tx(a) as cur:
        assert (
            cur.execute(
                "SELECT id FROM xt_meta.notification WHERE id=%s", (first[0].id,)
            ).fetchall()
            == []
        )
    with db.tenant_tx(other) as cur:
        assert (
            cur.execute(
                "SELECT id FROM xt_meta.notification WHERE id=%s", (first[0].id,)
            ).fetchall()
            == []
        )
    with pytest.raises(ToolError) as conflict:
        svc.send(**{**args, "message": "changed"})
    assert conflict.value.code == ErrorCode.IDEMPOTENCY_CONFLICT
    with pytest.raises(ToolError) as denied:
        svc.send(
            **{
                **args,
                "recipients": [Person(other.actor_id, "person:other", "Other")],
                "idempotency_key": "notification-test-other",
            }
        )
    assert denied.value.code == ErrorCode.NOT_FOUND


def test_one_use_link_fake_telegram_egress_summary_binding_and_no_repeat(people: Any) -> None:
    db, a, b, _other = people
    links = PgTelegramDirectory(db)
    code = PgNotificationRepository(db, b).mint_link_code()
    assert not links.consume(code, "11", "22")
    assert links.consume(code, "424242", "424242")
    assert not links.consume(code, "424242", "424242")
    with db.tenant_tx(b) as cur:
        stored = cur.execute(
            "SELECT hash FROM xt_meta.telegram_link_code WHERE person=%s", (b.actor_id,)
        ).fetchone()
        assert code.encode() not in bytes(stored["hash"])
    svc = NotificationService(
        PgNotificationRepository(db, a), confirm_base_url="https://desk.example"
    )
    rows = svc.send(
        actor_id=a.actor_id,
        recipients=[Person(b.actor_id, "person:b", "Private Name")],
        message="Name at 12 Private Street, private@example.com 0987654321",
        idempotency_key="notification-test-egress",
        kind="confirmation_required",
        confirmation_code="cf-42#abc123",
        confirm_url="https://desk.example/desk/confirm/cf-42",
    )
    outgoing: list[dict[str, Any]] = []

    def fake(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        # Audit is already committed in a distinct connection before the fake API receives data.
        with db.tenant_tx(b) as cur:
            assert (
                cur.execute(
                    "SELECT count(*) AS n FROM xt_meta.pii_egress WHERE person=%s"
                    " AND outcome='attempted'",
                    (b.actor_id,),
                ).fetchone()["n"]
                >= 1
            )
        outgoing.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    with httpx.Client(transport=httpx.MockTransport(fake)) as client:
        transport = HttpxTelegramTransport(
            "123:local_fake_token_value",
            desk_base_url="https://desk.example",
            api_base_url="http://127.0.0.1",
            client=client,
        )
        worker = NotificationService(
            PgNotificationRepository(db, replace(a, roles=("xt_workflow",))), transport
        )
        assert worker.deliver_pending() == 1
        assert worker.deliver_pending() == 0
    assert len(outgoing) == 1
    payload = json.dumps(outgoing)
    for secret in ("Private Name", "Private Street", "private@example.com", "0987654321"):
        assert secret not in payload
    assert "cf-42#abc123" in payload
    assert "reply_markup" not in payload
    assert links.get_summary("424242", "424242", str(rows[0].id)) is not None
    assert links.get_summary("999999", "999999", str(rows[0].id)) is None
    assert links.get_summary("424242", "999999", str(rows[0].id)) is None
    assert links.claim_update(99999942)
    assert not links.claim_update(99999942)
    links.release_update(99999942)
    assert links.claim_update(99999942)
