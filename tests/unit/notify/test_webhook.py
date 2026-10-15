# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Webhook tests never contact Telegram; all callbacks and approval commands are inert."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from xt.entrypoints.telegram_http import TelegramWebhook

SECRET = "local_test_secret_123"
HEADERS = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


class Directory:
    def __init__(self) -> None:
        self.claimed: set[int] = set()
        self.consumed: list[tuple[str, str, str]] = []
        self.queries: list[tuple[str, str, str]] = []
        self.codes = {"1_one_time_code"}
        self.fail = False

    def claim_update(self, update_id: int) -> bool:
        if update_id in self.claimed:
            return False
        self.claimed.add(update_id)
        return True

    def release_update(self, update_id: int) -> None:
        self.claimed.remove(update_id)

    def consume(self, code: str, telegram_user_id: str, chat_id: str) -> bool:
        self.consumed.append((code, telegram_user_id, chat_id))
        if code not in self.codes:
            return False
        self.codes.remove(code)
        return True

    def get_summary(
        self, telegram_user_id: str, chat_id: str, reference: str
    ) -> Mapping[str, str | None] | None:
        self.queries.append((telegram_user_id, chat_id, reference))
        if self.fail:
            raise RuntimeError("private database context")
        if telegram_user_id != "1234" or reference != "R-42#50cfaf56":
            return None
        return {
            "summary": "Bạn có thông báo mới trong xT.",
            "reference": "R-42#50cfaf56",
            "confirm_url": "https://desk.example.test/desk/confirm/cf_1",
        }


class Sender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send(
        self, chat_id: str, *, summary: str, reference: str, confirm_url: str | None = None
    ) -> str:
        self.messages.append(
            dict(chat_id=chat_id, summary=summary, reference=reference, confirm_url=confirm_url)
        )
        return "1"


@pytest.fixture
def webhook() -> tuple[TestClient, Directory, Sender]:
    directory, sender = Directory(), Sender()
    endpoint = TelegramWebhook(secret_token=SECRET, directory=directory, sender=sender)
    with TestClient(Starlette(routes=endpoint.routes())) as client:
        yield client, directory, sender


def update(command: str = "/link 1_one_time_code", uid: int = 1) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "message_id": 8,
            "from": {"id": 1234, "is_bot": False},
            "chat": {"id": 1234, "type": "private"},
            "text": command,
        },
    }


def test_link_code_and_replayed_webhook_are_processed_once(webhook: tuple) -> None:
    client, directory, sender = webhook
    for _ in range(2):
        assert client.post("/telegram/webhook", headers=HEADERS, json=update()).status_code == 200
    assert directory.consumed == [("1_one_time_code", "1234", "1234")]
    assert len(sender.messages) == 1
    assert sender.messages[0]["summary"] == "Đã liên kết tài khoản xT."
    assert "1_one_time_code" not in str(sender.messages)
    # Same one-use code with a different update cannot link again.
    client.post("/telegram/webhook", headers=HEADERS, json=update(uid=2))
    assert "không hợp lệ" in sender.messages[1]["summary"]


@pytest.mark.parametrize("headers", [{}, {"X-Telegram-Bot-Api-Secret-Token": "wrong"}])
def test_secret_required_before_any_processing(webhook: tuple, headers: dict[str, str]) -> None:
    client, directory, sender = webhook
    assert client.post("/telegram/webhook", headers=headers, json=update()).status_code == 403
    assert not directory.consumed and not directory.claimed and not sender.messages


def test_duplicate_secret_headers_rejected(webhook: tuple) -> None:
    client, _, _ = webhook
    assert (
        client.post(
            "/telegram/webhook",
            json=update(),
            headers=[("X-Telegram-Bot-Api-Secret-Token", SECRET)] * 2,
        ).status_code
        == 403
    )


def test_invalid_json_duplicate_keys_and_body_limit(webhook: tuple) -> None:
    client, _, _ = webhook
    for body in [b"{", b'{"update_id":1,"update_id":2}', b"[" * 100]:
        assert client.post("/telegram/webhook", headers=HEADERS, content=body).status_code == 400
    assert (
        client.post(
            "/telegram/webhook", headers=HEADERS, content=b" " * (64 * 1024 + 1)
        ).status_code
        == 413
    )


@pytest.mark.parametrize(
    "command", ["/approve R-42", "/reject R-42", "/start", "/link x y", "/summary x\n/approve x"]
)
def test_no_arbitrary_commands_or_approval_actions(webhook: tuple, command: str) -> None:
    client, directory, sender = webhook
    assert (
        client.post("/telegram/webhook", headers=HEADERS, json=update(command)).status_code == 200
    )
    assert not directory.claimed and not sender.messages


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d["message"]["chat"].update(type="group"),
        lambda d: d["message"]["chat"].update(id=-1234),
        lambda d: d["message"]["from"].update(id=1235),
        lambda d: d["message"]["from"].update(id=True),
        lambda d: d["message"]["from"].update(is_bot=True),
        lambda d: d["message"].update(forward_origin={}),
        lambda d: d.update(callback_query={"data": "approve", "from": {"id": 1234}}),
        lambda d: d.update(update_id=True),
    ],
)
def test_private_sender_binding_and_callback_rejection(webhook: tuple, mutation: Any) -> None:
    client, directory, sender = webhook
    data = update()
    mutation(data)
    assert client.post("/telegram/webhook", headers=HEADERS, json=data).status_code == 200
    assert not directory.claimed and not directory.consumed and not sender.messages


def test_summary_uses_linked_identity_and_safe_directory_content(webhook: tuple) -> None:
    client, directory, sender = webhook
    response = client.post(
        "/telegram/webhook", headers=HEADERS, json=update("/summary R-42#50cfaf56")
    )
    assert response.status_code == 200
    assert directory.queries == [("1234", "1234", "R-42#50cfaf56")]
    assert sender.messages[0]["confirm_url"].endswith("/desk/confirm/cf_1")
    assert "approve" not in str(sender.messages)
    client.post("/telegram/webhook", headers=HEADERS, json=update("/summary other-tenant", uid=2))
    assert len(sender.messages) == 1


def test_transient_failure_releases_replay_claim_and_hides_details(webhook: tuple) -> None:
    client, directory, sender = webhook
    directory.fail = True
    response = client.post(
        "/telegram/webhook", headers=HEADERS, json=update("/summary R-42#50cfaf56")
    )
    assert response.status_code == 503
    assert response.json() == {"ok": False}
    assert not directory.claimed and not sender.messages
    directory.fail = False
    assert (
        client.post(
            "/telegram/webhook", headers=HEADERS, json=update("/summary R-42#50cfaf56")
        ).status_code
        == 200
    )
    assert len(sender.messages) == 1
