# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Narrow Telegram webhook: account linking and privacy safe summary lookup only.

The secret header authenticates transport; it never supplies approval evidence. Private chat
sender binding and the directory's one-use Desk-generated link code establish the identity.
The database directory owns durable replay claims. No callback or approval action exists.
"""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from typing import Protocol

import anyio.to_thread
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from xt.contracts.tools.strict_json import loads_strict

_MAX_BODY = 64 * 1024
_COMMAND = re.compile(r"/(start|link|summary) ([A-Za-z0-9_.:#-]{1,200})", re.ASCII)


class TelegramSender(Protocol):
    def send(
        self, chat_id: str, *, summary: str, reference: str, confirm_url: str | None = None
    ) -> str: ...


class TelegramDirectory(Protocol):
    def consume(self, code: str, telegram_user_id: str, chat_id: str) -> bool: ...

    def get_summary(
        self, telegram_user_id: str, chat_id: str, reference: str
    ) -> Mapping[str, str | None] | None: ...

    def claim_update(self, update_id: int) -> bool: ...

    def release_update(self, update_id: int) -> None: ...


def _positive_id(value: object) -> bool:
    return type(value) is int and 0 < value < 2**52


class TelegramWebhook:
    def __init__(
        self, *, secret_token: str, directory: TelegramDirectory, sender: TelegramSender
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret_token):
            raise ValueError("Telegram webhook secret is required")
        self._secret = secret_token.encode("ascii")
        self.directory = directory
        self.sender = sender

    def routes(self) -> list[Route]:
        return [Route("/telegram/webhook", self.webhook, methods=["POST"])]

    async def webhook(self, request: Request) -> JSONResponse:
        secrets = request.headers.getlist("x-telegram-bot-api-secret-token")
        if len(secrets) != 1 or not hmac.compare_digest(secrets[0].encode("utf-8"), self._secret):
            return JSONResponse({"ok": False}, status_code=403)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAX_BODY:
                return JSONResponse({"ok": False}, status_code=413)
        try:
            update = loads_strict(bytes(body))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return JSONResponse({"ok": False}, status_code=400)
        command = self._command(update)
        if command is None:
            return JSONResponse({"ok": True})
        update_id, user_id, chat_id, action, argument = command
        try:
            await anyio.to_thread.run_sync(
                self._process, update_id, user_id, chat_id, action, argument
            )
        except Exception:
            # Do not log Telegram payloads, link codes, Bot API URLs or provider errors.
            return JSONResponse({"ok": False}, status_code=503)
        return JSONResponse({"ok": True})

    @staticmethod
    def _command(update: object) -> tuple[int, str, str, str, str] | None:
        if not isinstance(update, dict) or type(update.get("update_id")) is not int:
            return None
        update_id = update["update_id"]
        if not 0 <= update_id < 2**63 or "callback_query" in update:
            return None
        message = update.get("message")
        if not isinstance(message, dict):
            return None
        if any(k in message for k in ("forward_origin", "forward_from", "sender_chat")):
            return None
        sender, chat, text = message.get("from"), message.get("chat"), message.get("text")
        if not isinstance(sender, dict) or not isinstance(chat, dict) or not isinstance(text, str):
            return None
        if (
            chat.get("type") != "private"
            or sender.get("is_bot") is not False
            or not _positive_id(sender.get("id"))
            or sender.get("id") != chat.get("id")
            or not _positive_id(chat.get("id"))
        ):
            return None
        matched = _COMMAND.fullmatch(text)
        if matched is None:
            return None
        return update_id, str(sender["id"]), str(chat["id"]), matched[1], matched[2]

    def _process(
        self, update_id: int, user_id: str, chat_id: str, action: str, argument: str
    ) -> None:
        if not self.directory.claim_update(update_id):
            return
        try:
            if action in {"start", "link"}:
                linked = self.directory.consume(argument, user_id, chat_id)
                self.sender.send(
                    chat_id,
                    summary=(
                        "Đã liên kết tài khoản xT."
                        if linked
                        else "Mã liên kết không hợp lệ hoặc đã hết hạn."
                    ),
                    reference="telegram-link",
                )
            else:
                item = self.directory.get_summary(user_id, chat_id, argument)
                if item is not None:
                    summary, reference = item.get("summary"), item.get("reference")
                    if not summary or not reference:
                        raise ValueError("Invalid Telegram summary")
                    self.sender.send(
                        chat_id,
                        summary=summary,
                        reference=reference,
                        confirm_url=item.get("confirm_url"),
                    )
        except Exception:
            self.directory.release_update(update_id)
            raise
