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


def _pending(db: Any, actor: ModelActor, recipient: ModelActor) -> str:
    from psycopg.types.json import Jsonb

    cid = "cf-" + str(uuid4())
    pending = {
        "plan_digest": "sha256:" + "a1" * 32,
        "approvals": [],
        "confirmers": [{"ref": f"person:{recipient.actor_id}"}],
    }
    with db.tenant_tx(actor) as cur:
        cur.execute(
            "INSERT INTO xt_meta.tool_operation(tenant_id,actor_key,tool,idempotency_key,"
            "fingerprint,confirmation_id,pending,pending_status,expires_at,state)"
            " VALUES(xt.tenant(),%s,'notify',%s,'fingerprint',%s,%s,'pending',"
            "clock_timestamp()+interval '10 minutes','pending')",
            (str(actor.actor_id), str(uuid4()), cid, Jsonb(pending)),
        )
    return cid


def test_worker_process_and_confirmation_sources_are_idempotent(people: Any) -> None:
    from psycopg.types.json import Jsonb

    from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
    from xt.entrypoints.composition.runtime import Runtime
    from xt.entrypoints.notify_runtime import NotificationRelay

    db, a, b, _other = people
    event_ids = [uuid4(), uuid4()]
    with db.tenant_tx(a) as cur:
        for eid, kind in zip(event_ids, ["assignment", "assignment.escalated"], strict=True):
            cur.execute(
                "INSERT INTO xt_meta.process_event(id,tenant_id,instance_id,type,payload)"
                " VALUES(%s,xt.tenant(),'notify-test',%s,%s)",
                (
                    eid,
                    kind,
                    Jsonb({"recipients": [f"person:{b.actor_id}"], "task": "Task summary"}),
                ),
            )
    cid = _pending(db, a, b)
    rt = Runtime(app_db=db, migrator_dsn=None)
    relay = NotificationRelay(rt, SpiffProcessEngine(), {"XT_DESK_URL": "https://desk.example"})
    assert relay.run(a.tenant.id) == 0
    assert relay.run(a.tenant.id) == 0
    inbox = PgNotificationRepository(db, b).inbox()
    assert sorted(r["kind"] for r in inbox) == ["assignment", "confirmation_required", "escalation"]
    confirmation = next(row for row in inbox if row["kind"] == "confirmation_required")
    assert confirmation["confirmation_code"] == cid + "#a1a1a1a1"
    assert confirmation["confirm_url"] == "https://desk.example/desk/confirm/" + cid
    with db.tenant_tx(a) as cur:
        keys = ["event:" + str(eid) for eid in event_ids] + ["confirm:" + cid + ":0"]
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM xt_meta.notification_source WHERE source_key=ANY(%s)",
                (keys,),
            ).fetchone()["n"]
            == 3
        )
        assert cur.execute(
            "SELECT pending->'approvals' AS approvals,pending_status"
            " FROM xt_meta.tool_operation WHERE confirmation_id=%s",
            (cid,),
        ).fetchone() == {"approvals": [], "pending_status": "pending"}


def test_real_webhook_with_database_never_supplies_approval_evidence(people: Any) -> None:
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from xt.entrypoints.telegram_http import TelegramWebhook

    db, a, b, _other = people
    cid = _pending(db, a, b)
    code = PgNotificationRepository(db, b).mint_link_code()
    rows = NotificationService(
        PgNotificationRepository(db, a), confirm_base_url="https://desk.example"
    ).send(
        actor_id=a.actor_id,
        recipients=[Person(b.actor_id, "person:b", "Private Name")],
        message="private@example.com",
        idempotency_key="notification-webhook-test",
        kind="confirmation_required",
        confirmation_code=cid + "#a1a1a1a1",
        confirm_url="https://desk.example/desk/confirm/" + cid,
    )
    sent: list[dict[str, Any]] = []

    def fake(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    def update(uid: int, command: str) -> dict[str, Any]:
        return {
            "update_id": uid,
            "message": {
                "message_id": uid,
                "from": {"id": 525252, "is_bot": False},
                "chat": {"id": 525252, "type": "private"},
                "text": command,
            },
        }

    with httpx.Client(transport=httpx.MockTransport(fake)) as api:
        sender = HttpxTelegramTransport(
            "123:local_fake_token_value",
            desk_base_url="https://desk.example",
            api_base_url="http://127.0.0.1",
            client=api,
        )
        webhook = TelegramWebhook(
            secret_token="integration_secret", directory=PgTelegramDirectory(db), sender=sender
        )
        headers = {"X-Telegram-Bot-Api-Secret-Token": "integration_secret"}
        with TestClient(Starlette(routes=webhook.routes())) as client:
            body = update(8888801, "/link " + code)
            assert client.post("/telegram/webhook", json=body).status_code == 403
            for _ in range(2):
                assert (
                    client.post("/telegram/webhook", headers=headers, json=body).status_code == 200
                )
            assert len(sent) == 1
            summary = update(8888802, "/summary " + str(rows[0].id))
            for _ in range(2):
                assert (
                    client.post("/telegram/webhook", headers=headers, json=summary).status_code
                    == 200
                )
            assert len(sent) == 2
            assert (
                client.post(
                    "/telegram/webhook", headers=headers, json=update(8888803, "/approve " + cid)
                ).status_code
                == 200
            )
            callback = {"update_id": 8888804, "callback_query": {"id": "forged", "data": cid}}
            assert (
                client.post("/telegram/webhook", headers=headers, json=callback).status_code == 200
            )
    assert len(sent) == 2
    assert "private@example.com" not in json.dumps(sent)
    assert "reply_markup" not in json.dumps(sent)
    with db.tenant_tx(b) as cur:
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM xt_meta.pii_egress WHERE person=%s"
                " AND outcome='summary_attempted'",
                (b.actor_id,),
            ).fetchone()["n"]
            == 1
        )
        assert cur.execute(
            "SELECT pending->'approvals' AS approvals,pending_status"
            " FROM xt_meta.tool_operation WHERE confirmation_id=%s",
            (cid,),
        ).fetchone() == {"approvals": [], "pending_status": "pending"}


def test_recipient_rate_limit_rolls_back_whole_batch_and_rls_mark_read(people: Any) -> None:
    db, a, b, _other = people
    repo = PgNotificationRepository(db, a, per_recipient_limit=1)
    recipient = Person(b.actor_id, "person:b", "Private Name")
    svc = NotificationService(repo)
    first = svc.send(
        actor_id=a.actor_id,
        recipients=[recipient],
        message="first",
        idempotency_key="notification-rate-first",
    )
    with pytest.raises(ToolError) as limited:
        svc.send(
            actor_id=a.actor_id,
            recipients=[Person(a.actor_id, "me", "A"), recipient],
            message="must roll back",
            idempotency_key="notification-rate-second",
        )
    assert limited.value.code == ErrorCode.RATE_LIMITED
    assert repo.inbox() == []
    with db.tenant_tx(a) as cur:
        cur.execute(
            "UPDATE xt_meta.notification SET read_at=clock_timestamp() WHERE id=%s", (first[0].id,)
        )
        assert cur.rowcount == 0
        assert (
            cur.execute(
                "SELECT 1 FROM xt_meta.notification_request WHERE key=%s",
                ("notification-rate-second",),
            ).fetchall()
            == []
        )
    with db.tenant_tx(b) as cur:
        cur.execute(
            "UPDATE xt_meta.notification SET read_at=clock_timestamp() WHERE id=%s", (first[0].id,)
        )
        assert cur.rowcount == 1
    assert PgNotificationRepository(db, b).inbox() == []
    assert len(PgNotificationRepository(db, b).inbox(open_only=False)) == 1


def test_worker_rate_limited_batch_commits_progress_and_resumes_without_duplicates(
    people: Any,
) -> None:
    from psycopg.types.json import Jsonb

    from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
    from xt.entrypoints.composition.runtime import Runtime
    from xt.entrypoints.notify_runtime import NotificationRelay

    db, a, b, _other = people
    event_ids = [uuid4() for _ in range(31)]
    with db.tenant_tx(a) as cur:
        for eid in event_ids:
            cur.execute(
                "INSERT INTO xt_meta.process_event(id,tenant_id,instance_id,type,payload)"
                " VALUES(%s,xt.tenant(),'notify-rate-batch','assignment',%s)",
                (eid, Jsonb({"recipients": [f"person:{b.actor_id}"], "task": "Batch task"})),
            )
    relay = NotificationRelay(
        Runtime(app_db=db, migrator_dsn=None),
        SpiffProcessEngine(),
        {"XT_DESK_URL": "https://desk.example"},
    )
    assert relay.run(a.tenant.id) == 0
    assert len(PgNotificationRepository(db, b).inbox()) == 30
    keys = ["event:" + str(eid) for eid in event_ids]
    with db.tenant_tx(a) as cur:
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM xt_meta.notification_source WHERE source_key=ANY(%s)",
                (keys,),
            ).fetchone()["n"]
            == 30
        )
    # Advance only stored rate timestamps; no wall-clock sleep or external API.
    with db.tenant_tx(replace(a, roles=("xt_workflow",))) as cur:
        cur.execute(
            "UPDATE xt_meta.notification_delivery"
            " SET updated_at=clock_timestamp()-interval '2 minutes'"
            " WHERE recipient=%s",
            (b.actor_id,),
        )
    assert relay.run(a.tenant.id) == 0
    assert len(PgNotificationRepository(db, b).inbox()) == 31
    assert relay.run(a.tenant.id) == 0
    assert len(PgNotificationRepository(db, b).inbox()) == 31
    with db.tenant_tx(a) as cur:
        assert (
            cur.execute(
                "SELECT count(*) AS n FROM xt_meta.notification_source WHERE source_key=ANY(%s)",
                (keys,),
            ).fetchone()["n"]
            == 31
        )


def test_desk_link_requires_real_fresh_owner_oidc_proof_and_persists_one_use_code(
    people: Any, pg: Any, idp: Any
) -> None:
    import re
    from types import SimpleNamespace
    from urllib.parse import parse_qs, urlsplit

    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.testclient import TestClient

    from xt.entrypoints.composition.confirm_web import ConfirmSettings
    from xt.entrypoints.composition.runtime import Runtime
    from xt.entrypoints.telegram_link import TelegramLinkPages

    db, _a, owner, _other = people
    subject = "telegram-owner-" + str(owner.actor_id)
    with pg.migrator() as conn:
        conn.execute(
            "UPDATE xt_meta.person SET subject=%s WHERE tenant_id=%s AND id=%s",
            (subject, owner.tenant.id, owner.actor_id),
        )
    pages = TelegramLinkPages(
        Runtime(app_db=db, migrator_dsn=None),
        ConfirmSettings(
            "https://desk.example", idp.base + "/auth", idp.base + "/token", idp.stepup_settings()
        ),
    )
    caller = SimpleNamespace(
        tenant=owner.tenant, person=SimpleNamespace(id=owner.actor_id, subject=subject, active=True)
    )
    request = Request({"type": "http", "headers": []})
    with TestClient(Starlette(routes=pages.routes()), base_url="https://desk.example") as browser:
        for expected_subject, expected_status in (("wrong-owner", 403), (subject, 200)):
            initiated = pages.begin(caller, request)
            start_url = json.loads(initiated.body)["authorize_url"]
            assert "link_code" not in json.loads(initiated.body)
            with db.tenant_tx(owner) as cur:
                assert (
                    cur.execute(
                        "SELECT count(*) AS n FROM xt_meta.telegram_link_code"
                        " WHERE person=xt.actor()"
                    ).fetchone()["n"]
                    == 0
                )
            started = browser.get(urlsplit(start_url).path, follow_redirects=False)
            assert started.status_code == 302
            query = parse_qs(urlsplit(started.headers["location"]).query)
            assert query["max_age"] == ["0"]
            assert query["code_challenge_method"] == ["S256"]
            state, nonce = query["state"][0], query["nonce"][0]
            # Real local-IDP signature, HTTP code exchange, JWKS and DB jti verification.
            authorization_code = idp.issue_code(idp.id_token(expected_subject, nonce=nonce))
            callback = browser.get(
                "/desk/telegram/callback", params={"state": state, "code": authorization_code}
            )
            assert callback.status_code == expected_status, callback.text
            with db.tenant_tx(owner) as cur:
                assert (
                    cur.execute(
                        "SELECT 1 FROM xt_meta.confirmation_session WHERE state=%s", (state,)
                    ).fetchall()
                    == []
                )
            if expected_status == 200:
                match = re.search(r"/link ([A-Za-z0-9_-]{43})", callback.text)
                assert match is not None
                code = match[1]
                assert callback.headers["cache-control"] == "no-store"
                directory = PgTelegramDirectory(db)
                assert directory.consume(code, "626262", "626262")
                assert not directory.consume(code, "626262", "626262")
                replay = browser.get(
                    "/desk/telegram/callback", params={"state": state, "code": authorization_code}
                )
                assert replay.status_code == 400
    assert idp.token_requests[-1]["redirect_uri"] == "https://desk.example/desk/telegram/callback"
    assert "code_verifier" in idp.token_requests[-1]


def test_mcp_and_direct_enqueue_share_audit_before_notify_lock_order(people: Any, pg: Any) -> None:
    import time
    from concurrent.futures import ThreadPoolExecutor

    from xt.adapters.db.audit import lock_audit_chain
    from xt.adapters.db.confirmations import PgUnitOfWork
    from xt.adapters.db.connection import Database

    db, a, b, _other = people
    recipient = Person(b.actor_id, f"person:{b.actor_id}", "Private Name")
    marker = "xt-notify-lock-" + uuid4().hex
    worker_db = Database(pg.app_dsn + " application_name=" + marker, min_size=1, max_size=1)
    uow = PgUnitOfWork(db)
    future = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            try:
                with uow.scope(a), uow.tenant_tx(a) as cur:
                    # Models the audit lock already taken by MCP operation claim/confirmation.
                    lock_audit_chain(cur, a.tenant.id)
                    main_pid = cur.execute("SELECT pg_backend_pid() AS pid").fetchone()["pid"]
                    future = executor.submit(
                        NotificationService(PgNotificationRepository(worker_db, a)).send,
                        actor_id=a.actor_id,
                        recipients=[recipient],
                        message="ordinary sender",
                        idempotency_key="lock-order-worker-" + uuid4().hex,
                    )
                    deadline = time.monotonic() + 5
                    with pg.su() as monitor:
                        while True:
                            waiting = monitor.execute(
                                "SELECT pid FROM pg_stat_activity WHERE application_name=%s"
                                " AND wait_event_type='Lock' AND wait_event='advisory'"
                                " AND %s=ANY(pg_blocking_pids(pid))",
                                (marker, main_pid),
                            ).fetchone()
                            if waiting:
                                break
                            assert not future.done(), (
                                "Concurrent enqueue exited before waiting on audit"
                            )
                            assert time.monotonic() < deadline, (
                                "Concurrent enqueue did not wait on audit"
                            )
                            time.sleep(0.01)
                    # The other sender is waiting for audit and must not own the notify lock.
                    own_rows = NotificationService(PgNotificationRepository(uow, a)).send(
                        actor_id=a.actor_id,
                        recipients=[recipient],
                        message="MCP sender",
                        idempotency_key="lock-order-mcp-" + uuid4().hex,
                    )
                    assert len(own_rows) == 1
            finally:
                # Exiting the main transaction releases audit before waiting for the worker.
                if future is not None:
                    worker_rows = future.result(timeout=5)
        assert worker_rows[0].id != own_rows[0].id
        assert {row["title"] for row in PgNotificationRepository(db, b).inbox()} == {
            "ordinary sender",
            "MCP sender",
        }
    finally:
        worker_db.close()
