# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest

from xt.application.notify.ports import Notification, Person
from xt.application.notify.service import SAFE_SUMMARY, NotificationService
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode


class Repository:
    def __init__(self) -> None:
        self.requests: dict[tuple[UUID, str], tuple[str, list[Notification]]] = {}
        self.rows: list[Notification] = []
        self.events: list[str] = []

    def enqueue(
        self, actor_id: UUID, key: str, digest: str, rows: list[Notification]
    ) -> list[Notification]:
        previous = self.requests.get((actor_id, key))
        if previous:
            if previous[0] != digest:
                raise ToolError(ErrorCode.IDEMPOTENCY_CONFLICT, "changed request")
            return previous[1]
        self.requests[(actor_id, key)] = digest, rows
        self.rows.extend(rows)
        return rows

    def pending(self, limit: int) -> list[tuple[Notification, str]]:
        rows, self.rows = self.rows[:limit], self.rows[limit:]
        self.events.append("reserved")
        return [(row, "42") for row in rows]

    def egress(self, row: Notification, outcome: str) -> None:
        self.events.append("audit:" + outcome)

    def delivered(self, notification_id: UUID, external_id: str) -> None:
        self.events.append("delivered")

    def failed(self, notification_id: UUID) -> None:
        self.events.append("failed")


class Sender:
    def __init__(self, repository: Repository, *, fail: bool = False) -> None:
        self.repo = repository
        self.fail = fail
        self.messages: list[dict[str, Any]] = []

    def send(
        self, chat_id: str, *, summary: str, reference: str, confirm_url: str | None = None
    ) -> str:
        assert self.repo.events[-1] == "audit:attempted"
        self.messages.append(dict(summary=summary, reference=reference, confirm_url=confirm_url))
        if self.fail:
            raise RuntimeError("ambiguous network failure")
        return "telegram-1"


def test_idempotency_canonicalizes_recipients_and_rejects_changed_message() -> None:
    repo = Repository()
    svc = NotificationService(repo)
    actor = uuid4()
    a, b = Person(uuid4(), "person:a", "A"), Person(uuid4(), "person:b", "B")
    first = svc.send(
        actor_id=actor, recipients=[a, b, a], message="hello", idempotency_key="message-1"
    )
    retry = svc.send(
        actor_id=actor, recipients=[b, a], message="hello", idempotency_key="message-1"
    )
    assert first == retry
    assert len(repo.rows) == 2
    with pytest.raises(ToolError) as error:
        svc.send(actor_id=actor, recipients=[a, b], message="changed", idempotency_key="message-1")
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("fail", [False, True])
def test_egress_omits_all_unstructured_pii_and_does_not_repeat_attempt(fail: bool) -> None:
    repo = Repository()
    sender = Sender(repo, fail=fail)
    svc = NotificationService(repo, sender, confirm_base_url="https://desk.example")
    rows = svc.send(
        actor_id=uuid4(),
        recipients=[Person(uuid4(), "person:a", "Nguyễn Văn A")],
        message="Nguyễn Văn A tại 123 Lê Lợi; a@example.com; 0987654321",
        idempotency_key="message-1",
        kind="confirmation_required",
        confirmation_code="R-42#1234abcd",
        confirm_url="https://desk.example/desk/confirm/cf-1",
    )
    assert svc.deliver_pending() == (0 if fail else 1)
    assert svc.deliver_pending() == 0
    assert sender.messages == [
        {"summary": SAFE_SUMMARY, "reference": "R-42#1234abcd", "confirm_url": rows[0].confirm_url}
    ]
    assert repo.events[:3] == ["reserved", "audit:attempted", "failed" if fail else "delivered"]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/desk/confirm/a",
        "https://desk.example/other/a",
        "https://desk.example/desk/confirm/a?email=pii@example.com",
        "https://desk.example/desk/confirm/a#x",
    ],
)
def test_confirmation_url_cannot_smuggle_egress(url: str) -> None:
    with pytest.raises(ToolError):
        NotificationService(Repository(), confirm_base_url="https://desk.example").send(
            actor_id=uuid4(),
            recipients=[Person(uuid4(), "person:a", "A")],
            message="hi",
            idempotency_key="message-1",
            confirm_url=url,
        )
