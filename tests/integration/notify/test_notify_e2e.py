# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Real Postgres + MCP HTTP + Desk OTP + inbox + local fake Telegram boundary."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from tests.integration.composition.fixtures import (
    DEPT_HEAD,
    REQUESTER,
    ROOT,
    Stack,
    StackBase,
    run_admin,
    sub,
)
from tests.integration.composition.idp import AGENT_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import serve
from tests.integration.composition.test_e2e import confirm_in_desk

from xt.adapters.db.integration import person_id
from xt.adapters.db.notify import PgNotificationRepository
from xt.adapters.notify.telegram import HttpxTelegramTransport
from xt.application.notify.service import SAFE_SUMMARY, NotificationService
from xt.domain.model.context import ModelActor
from xt.entrypoints import notify_http, notify_runtime
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration
SECRET = "notify_e2e_local_secret"
CHAT_ID = 74747474


@dataclass
class NotificationStack:
    stack: Stack
    sender: HttpxTelegramTransport
    outgoing: list[dict[str, Any]]


@pytest.fixture(scope="module")
def notification_stack(
    stack_base: StackBase, idp: LocalIdp, mcp_port: int
) -> Iterator[NotificationStack]:
    env = {
        **stack_base.env,
        "TELEGRAM_BOT_TOKEN": "123456:local_test_credential_only",
        "TELEGRAM_WEBHOOK_SECRET": SECRET,
        "TELEGRAM_LINK_CLIENTS": AGENT_CLIENT,
    }
    for alias in ("acme", "beta"):
        command = [
            "pack", "install", "--tenant", alias, str(ROOT / "packages/thong-bao"),
            "--proposed-by", "agent:integration", "--approved-by", "person:admin",
        ]  # fmt: skip
        rc, plan = run_admin(command, env)
        assert rc == 0, plan
        digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, output = run_admin([*command, "--confirm", digest.group(1)], env)
        assert rc == 0, output
    outgoing: list[dict[str, Any]] = []

    def local_telegram(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "127.0.0.1", "Telegram tests must stay local"
        payload = json.loads(request.content)
        assert "reply_markup" not in payload
        outgoing.append(payload)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(outgoing)}})

    with httpx.Client(transport=httpx.MockTransport(local_telegram)) as api:
        sender = HttpxTelegramTransport(
            "123456:local_test_credential_only",
            desk_base_url="https://desk.test",
            api_base_url="http://127.0.0.1",
            client=api,
        )
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(notify_http, "telegram_sender", lambda env: sender)
            patch.setattr(notify_runtime, "telegram_sender", lambda env: sender)
            prepared = prepare(
                env,
                argparse.Namespace(
                    transport="http", host="127.0.0.1", port=mcp_port, profile="full"
                ),
            )
            app = serve(prepared.http_app(), mcp_port)
            stack = Stack(idp, app, prepared, env, stack_base.tenants, stack_base.outputs)
            try:
                yield NotificationStack(stack, sender, outgoing)
            finally:
                app.stop()
                prepared.close()


def _telegram_update(update_id: int, command: str, *, chat_id: int = CHAT_ID) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": chat_id, "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "text": command,
        },
    }


def _link_in_browser(s: Stack, *, proof_owner: str = DEPT_HEAD) -> httpx.Response:
    """Delegated bearer only starts the flow; only the exact owner's OTP reveals a code."""
    with httpx.Client(base_url=s.app.base, follow_redirects=False, timeout=30) as browser:
        initiated = browser.post(
            "/telegram/link-code",
            headers={"Authorization": "Bearer " + s.token("acme", DEPT_HEAD)},
        )
        assert initiated.status_code == 200, initiated.text
        assert set(initiated.json()) == {"authorize_url", "expires_in"}
        assert "/link " not in initiated.text
        started = browser.get(urlsplit(initiated.json()["authorize_url"]).path)
        assert started.status_code == 302, started.text
        query = parse_qs(urlsplit(started.headers["location"]).query)
        assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
        assert query["code_challenge_method"] == ["S256"]
        code = s.idp.issue_code(s.idp.id_token(sub("acme", proof_owner), nonce=query["nonce"][0]))
        response = browser.get(
            "/desk/telegram/callback", params={"state": query["state"][0], "code": code}
        )
        replay = browser.get(
            "/desk/telegram/callback", params={"state": query["state"][0], "code": code}
        )
        assert replay.status_code == 400
        return response


def test_link_owner_otp_is_required_before_any_telegram_capability(
    notification_stack: NotificationStack,
) -> None:
    denied = _link_in_browser(notification_stack.stack, proof_owner=REQUESTER)
    assert denied.status_code == 403, denied.text
    assert "/link " not in denied.text
    assert notification_stack.outgoing == []


@pytest.mark.anyio
async def test_notify_l2_desk_receipt_real_inbox_and_fake_telegram_egress(
    notification_stack: NotificationStack,
) -> None:
    test = notification_stack
    s = test.stack
    runtime = s.prepared.services.runtime
    tenant = s.tenants["acme"]
    sender_id = person_id(tenant.id, REQUESTER)
    recipient_id = person_id(tenant.id, DEPT_HEAD)
    linked = _link_in_browser(s)
    assert linked.status_code == 200, linked.text
    command = re.search(r"/link ([A-Za-z0-9_-]{40,64})", linked.text)
    assert command, linked.text
    webhook_headers = {"X-Telegram-Bot-Api-Secret-Token": SECRET}
    with httpx.Client(base_url=s.app.base, timeout=30) as http:
        link_update = _telegram_update(747474001, command.group(0))
        assert http.post("/telegram/webhook", json=link_update).status_code == 403
        for _ in range(2):
            assert (
                http.post(
                    "/telegram/webhook", headers=webhook_headers, json=link_update
                ).status_code
                == 200
            )
    assert len(test.outgoing) == 1
    assert "Đã liên kết" in test.outgoing[0]["text"]
    private = "Riêng Tư, 12 Đường Riêng, secret@example.test, 0987654321"
    args = {
        "recipients": [f"person:{recipient_id}"],
        "message": private,
        "idempotency_key": "notify-real-e2e-0001",
    }
    pending = await s.call("acme", REQUESTER, "notify", args)
    assert not pending["ok"] and pending["error"]["code"] == "confirmation_required", pending
    url = pending["error"]["details"]["confirm_url"]
    assert len(test.outgoing) == 1, "MCP transactions only enqueue after human confirmation"
    before = await s.call("acme", DEPT_HEAD, "get_inbox", {"kinds": ["notification"]})
    assert before["ok"], before
    confirmed = confirm_in_desk(s, url, sub("acme", REQUESTER))
    assert confirmed.status_code == 200 and "Đã thực hiện" in confirmed.text, confirmed.text
    receipt = await s.call("acme", REQUESTER, "notify", args)
    assert receipt["ok"], receipt
    assert receipt["meta"]["effective_level"] == "L2"
    assert receipt["meta"]["receipt"]["replayed"] is True
    notifications = receipt["data"]["notifications"]
    assert len(notifications) == 1
    notification_ref = notifications[0]["ref"]
    notification_id = notification_ref.split(":", 1)[1]
    inbox = await s.call("acme", DEPT_HEAD, "get_inbox", {"kinds": ["notification"]})
    assert inbox["ok"], inbox
    assert notification_ref in {item["ref"] for item in inbox["data"]["items"]}
    sender_inbox = await s.call("acme", REQUESTER, "get_inbox", {"kinds": ["notification"]})
    other_inbox = await s.call("beta", DEPT_HEAD, "get_inbox", {"kinds": ["notification"]})
    for invisible in (sender_inbox, other_inbox):
        assert invisible["ok"], invisible
        assert notification_ref not in {item["ref"] for item in invisible["data"]["items"]}
    for actor in (
        ModelActor(tenant, sender_id, ()),
        ModelActor(s.tenants["beta"], person_id(s.tenants["beta"].id, DEPT_HEAD), ()),
    ):
        with runtime.app_db.tenant_tx(actor) as cur:
            assert (
                cur.execute(
                    "SELECT id FROM xt_meta.notification WHERE id=%s", (notification_id,)
                ).fetchall()
                == []
            )
    workflow = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-worker")
    worker = NotificationService(PgNotificationRepository(runtime.app_db, workflow), test.sender)
    assert worker.deliver_pending() >= 1
    assert worker.deliver_pending() == 0
    pushed = [payload for payload in test.outgoing if notification_id in payload["text"]]
    assert len(pushed) == 1
    assert SAFE_SUMMARY in pushed[0]["text"]
    assert str(pushed[0]["chat_id"]) == str(CHAT_ID)
    serialized = json.dumps(test.outgoing, ensure_ascii=False)
    for protected in ("Riêng Tư", "Đường Riêng", "secret@example.test", "0987654321"):
        assert protected not in serialized
    with httpx.Client(base_url=s.app.base, timeout=30) as http:
        summary_update = _telegram_update(747474002, "/summary " + notification_id)
        for _ in range(2):
            assert (
                http.post(
                    "/telegram/webhook", headers=webhook_headers, json=summary_update
                ).status_code
                == 200
            )
        count = len(test.outgoing)
        assert (
            http.post(
                "/telegram/webhook",
                headers=webhook_headers,
                json=_telegram_update(747474003, "/summary " + notification_id, chat_id=74747475),
            ).status_code
            == 200
        )
        assert len(test.outgoing) == count
    assert len([p for p in test.outgoing if notification_id in p["text"]]) == 2
    recipient = ModelActor(tenant, recipient_id, ())
    with runtime.app_db.tenant_tx(recipient) as cur:
        outcomes = cur.execute(
            "SELECT outcome FROM xt_meta.pii_egress WHERE person=%s AND outcome IN"
            " ('attempted','summary_attempted')",
            (recipient_id,),
        ).fetchall()
        assert {row["outcome"] for row in outcomes} == {"attempted", "summary_attempted"}
        stored = cur.execute(
            "SELECT hash FROM xt_meta.telegram_link_code WHERE person=%s", (recipient_id,)
        ).fetchall()
        assert stored and all(command.group(1).encode() not in bytes(row["hash"]) for row in stored)
