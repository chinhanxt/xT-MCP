# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Independent boundary checks after notification implementation security review."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from tests.unit.notify.test_service import Repository
from tests.unit.tools.fakes import agent_ctx
from xt.adapters.notify.telegram import HttpxTelegramTransport
from xt.application.notify.ports import Notification, Person
from xt.application.notify.service import SAFE_SUMMARY, NotificationService
from xt.application.tools.dispatcher import plan_digest
from xt.entrypoints.composition.notify import NotificationToolAdapter


def test_human_confirmation_digest_pins_person_identity_when_alias_reassigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = Person(uuid4(), "person:manager", "Manager")
    replacement = Person(uuid4(), "person:manager", "Manager")
    repo = SimpleNamespace(resolve_person=lambda ref: original)
    caller = SimpleNamespace(person=SimpleNamespace(id=uuid4()), alias="acme")
    runtime = SimpleNamespace(caller=lambda ctx: caller)
    adapter = NotificationToolAdapter(runtime)  # type: ignore[arg-type]
    monkeypatch.setattr(adapter, "_repo", lambda ctx: repo)
    ctx = agent_ctx()
    args = {
        "recipients": ["person:manager"],
        "message": "Private notice",
        "idempotency_key": "test-key-1",
    }
    first = adapter.plan_notify(ctx, args)
    repo.resolve_person = lambda ref: replacement
    second = adapter.plan_notify(ctx, args)
    assert plan_digest("notify", args, first) != plan_digest("notify", args, second)


def test_complete_service_to_bot_boundary_omits_free_text_and_audits_before_network() -> None:
    repository = Repository()
    delivered = []
    private = "Nguyễn Văn Riêng, 12 Đường Riêng, nguyen@example.test, 0912345678"

    def fake_telegram(request: httpx.Request) -> httpx.Response:
        assert repository.events[-1] == "audit:attempted"
        delivered.append(json.loads(request.content))
        assert private not in request.content.decode()
        assert "Nguyễn" not in request.content.decode()
        assert "Đường" not in request.content.decode()
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    with httpx.Client(transport=httpx.MockTransport(fake_telegram)) as client:
        transport = HttpxTelegramTransport(
            "123456:local_credential_123", desk_base_url="https://desk.test", client=client
        )
        service = NotificationService(repository, transport, confirm_base_url="https://desk.test")
        service.send(
            actor_id=uuid4(),
            recipients=[Person(uuid4(), "person:private", private)],
            message=private,
            idempotency_key="private-notice-1",
            confirmation_code="R-42#1234abcd",
            confirm_url="https://desk.test/desk/confirm/cf_1",
        )
        assert service.deliver_pending() == 1
        assert service.deliver_pending() == 0
    assert len(delivered) == 1
    assert delivered[0]["text"] == (
        f"{SAFE_SUMMARY}\nMã đối chiếu: R-42#1234abcd\nhttps://desk.test/desk/confirm/cf_1"
    )


def test_audit_unavailable_blocks_any_telegram_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = Repository()

    def audit_unavailable(row: Notification, outcome: str) -> None:
        raise RuntimeError("audit storage unavailable")

    monkeypatch.setattr(repository, "egress", audit_unavailable)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unaudited egress"))
    ) as c:
        service = NotificationService(
            repository,
            HttpxTelegramTransport(
                "123456:local_credential_123",
                desk_base_url="https://desk.test",
                client=c,
            ),
        )
        service.send(
            actor_id=uuid4(),
            recipients=[Person(uuid4(), "person:p", "P")],
            message="Notice",
            idempotency_key="audit-failure-1",
        )
        with pytest.raises(RuntimeError, match="audit storage unavailable"):
            service.deliver_pending()
