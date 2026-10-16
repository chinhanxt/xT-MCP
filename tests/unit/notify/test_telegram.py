# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Offline Bot API contract and outbound privacy boundary."""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from xt.adapters.notify.telegram import HttpxTelegramTransport, TelegramDeliveryError

TOKEN = "123456:test_bot_credential_123"
DESK = "https://desk.example.test"


def test_send_plain_summary_comparison_and_locator_without_action() -> None:
    seen = []

    def telegram(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    with httpx.Client(transport=httpx.MockTransport(telegram)) as client:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=client)
        assert (
            sender.send(
                "1234",
                summary="Bạn có thông báo mới. [PII:token_1]",
                reference="R-42#50cfaf56",
                confirm_url=f"{DESK}/desk/confirm/cf_example",
            )
            == "42"
        )
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.loads(request.content)
    assert payload == {
        "chat_id": "1234",
        "text": (
            "Bạn có thông báo mới. [PII:token_1]\nMã đối chiếu: R-42#50cfaf56\n"
            f"{DESK}/desk/confirm/cf_example"
        ),
        "link_preview_options": {"is_disabled": True},
        "protect_content": True,
    }


@pytest.mark.parametrize(
    "url",
    [
        "http://desk.example.test/desk/confirm/cf_1",
        "https://evil.test/desk/confirm/cf_1",
        "https://desk.example.test.evil.test/desk/confirm/cf_1",
        "https://u:p@desk.example.test/desk/confirm/cf_1",
        f"{DESK}/desk/confirm/cf_1?token=secret",
        f"{DESK}/desk/confirm/cf_1#secret",
        f"{DESK}/desk/confirm/cf_1/execute",
        f"{DESK}/desk/confirm/../execute",
        f"{DESK}/desk/confirm/%2e%2e",
        f"{DESK}/other/cf_1",
        f"{DESK}/desk/confirm/cf_1\n",
        f"{DESK}/desk/confirm/cf_1\\",
    ],
)
def test_reject_untrusted_or_bearer_confirmation_url_without_egress(url: str) -> None:
    def no_network(_: httpx.Request) -> httpx.Response:
        pytest.fail("invalid notification attempted egress")

    with httpx.Client(transport=httpx.MockTransport(no_network)) as client:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=client)
        with pytest.raises(ValueError):
            sender.send("1234", summary="Thông báo mới", reference="R-42", confirm_url=url)


@pytest.mark.parametrize(
    "summary",
    [
        "mail mai@example.test",
        "SĐT 0912345678",
        "CCCD 079123456789",
        "",
        "x" * 1001,
        "Mở https://evil.test",
        "Mở HTTP://evil.test",
        "SĐT \uff10\uff19\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18",
    ],
)
def test_raw_pii_and_arbitrary_links_rejected_before_egress(summary: str) -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected egress"))
    ) as c:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=c)
        with pytest.raises(ValueError):
            sender.send("1234", summary=summary, reference="R-42")


@pytest.mark.parametrize("chat_id", ["-1001234", "0", "1234abc", "1\n2"])
def test_private_chat_required(chat_id: str) -> None:
    sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK)
    try:
        with pytest.raises(ValueError):
            sender.send(chat_id, summary="Thông báo mới", reference="R-42")
    finally:
        sender.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"ok": False, "description": TOKEN}),
        httpx.Response(200, json={"ok": False, "description": TOKEN}),
        httpx.Response(200, content=b"not JSON"),
        httpx.Response(200, json={"ok": True, "result": {"message_id": True}}),
        httpx.Response(200, json={"ok": True, "result": {}}),
        httpx.Response(302, headers={"Location": "https://evil.test"}),
    ],
)
def test_failure_does_not_leak_token_or_follow_redirects(response: httpx.Response) -> None:
    seen = []

    def telegram(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    with httpx.Client(transport=httpx.MockTransport(telegram), follow_redirects=True) as client:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=client)
        with pytest.raises(TelegramDeliveryError) as error:
            sender.send("1234", summary="Thông báo mới", reference="R-42")
        assert TOKEN not in str(error.value)
        assert error.value.__cause__ is None
    assert len(seen) == 1


def test_timeout_exception_is_sanitized() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"credential {TOKEN}", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=client)
        with pytest.raises(TelegramDeliveryError) as error:
            sender.send("1234", summary="Thông báo mới", reference="R-42")
        assert TOKEN not in str(error.value)


def test_local_fake_bot_api_allowed_but_cleartext_remote_api_rejected() -> None:
    with pytest.raises(ValueError):
        HttpxTelegramTransport(TOKEN, desk_base_url=DESK, api_base_url="http://evil.test")
    sender = HttpxTelegramTransport(
        TOKEN, desk_base_url=DESK, api_base_url="http://127.0.0.1:19999"
    )
    sender.close()


def test_verbose_http_logs_redact_bot_credentials(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        )
    ) as client:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=client)
        sender.send("1234", summary="Thông báo mới", reference="R-42")
        # httpcore emits the path as bytes at DEBUG with a real socket transport.
        logging.getLogger("httpcore.http11").debug(
            "send_request_headers %r", f"/bot{TOKEN}/sendMessage".encode()
        )
    assert TOKEN not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_comparison_reference_cannot_smuggle_phone_pii() -> None:
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected egress"))
    ) as c:
        sender = HttpxTelegramTransport(TOKEN, desk_base_url=DESK, client=c)
        with pytest.raises(ValueError, match="privacy safe"):
            sender.send("1234", summary="Thông báo mới", reference="0912345678")
