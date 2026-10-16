# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Small Bot API transport. Application owns redaction and the egress audit.

Only already masked/tokenized summaries enter this adapter. A final recognizer check rejects
accidental email/phone/identity leakage; it is not a substitute for field classification.
No keyboard, callback data, parse mode, arbitrary API method, or approval action is exposed.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

import httpx

from xt.application.tools.pii_scrub import ErrorScrubber

_REFERENCE = re.compile(r"[A-Za-z0-9_.:#-]{1,160}", re.ASCII)
_TOKEN = re.compile(r"[0-9]{1,20}:[A-Za-z0-9_-]{10,}", re.ASCII)
_CONFIRM_PATH = re.compile(r"/desk/confirm/[A-Za-z0-9_-]{1,160}", re.ASCII)


class _BotCredentialFilter(logging.Filter):
    """httpx INFO/httpcore DEBUG include the credential-bearing request path."""

    _credential = re.compile(r"(?<=/bot)[0-9]{1,20}:[A-Za-z0-9_-]{10,}", re.ASCII)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self._credential.search(message):
            record.msg = self._credential.sub("[REDACTED]", message)
            record.args = ()
        return True


def _protect_transport_logs() -> None:
    for name in (
        "httpx",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    ):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _BotCredentialFilter) for f in logger.filters):
            logger.addFilter(_BotCredentialFilter())


class TelegramDeliveryError(RuntimeError):
    """Safe error: never includes the Bot API URL, credential, response, or message."""


def _origin(url: str) -> tuple[str, str, int | None]:
    if any(ord(c) <= 32 or c == "\\" for c in url):
        raise ValueError("Invalid Telegram Desk URL")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Telegram Desk URL must be a trusted HTTPS URL")
    return parsed.scheme, parsed.hostname.lower(), parsed.port or 443


class HttpxTelegramTransport:
    """Synchronous port implementation; test with an injected MockTransport client."""

    def __init__(
        self,
        bot_token: str,
        *,
        desk_base_url: str,
        api_base_url: str = "https://api.telegram.org",
        client: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        if not _TOKEN.fullmatch(bot_token):
            raise ValueError("Invalid Telegram bot credential")
        _protect_transport_logs()
        self._desk_origin = _origin(desk_base_url)
        base = urlsplit(api_base_url)
        if (
            base.scheme not in {"https", "http"}
            or not base.hostname
            or base.username is not None
            or base.password is not None
            or base.query
            or base.fragment
            or base.path not in {"", "/"}
            or (base.scheme == "http" and base.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("Invalid Telegram API endpoint")
        if timeout_s <= 0:
            raise ValueError("Telegram timeout must be positive")
        self._url = f"{api_base_url.rstrip('/')}/bot{bot_token}/sendMessage"
        self._owned = client is None
        self._client = client or httpx.Client(
            timeout=timeout_s, follow_redirects=False, trust_env=False
        )

    def close(self) -> None:
        if self._owned:
            self._client.close()

    def send(
        self,
        chat_id: str,
        *,
        summary: str,
        reference: str,
        confirm_url: str | None = None,
    ) -> str:
        if not re.fullmatch(r"[1-9][0-9]{0,15}", chat_id):
            raise ValueError("Telegram destination must be a linked private chat")
        if not _REFERENCE.fullmatch(reference):
            raise ValueError("Invalid comparison code")
        scrubber = ErrorScrubber(lambda _: "[PII:masked]")
        safe_summary = scrubber.text(summary)
        scrubber.text(reference)
        if (
            not summary.strip()
            or len(summary) > 1000
            or scrubber.hits
            or "http://" in safe_summary.lower()
            or "https://" in safe_summary.lower()
        ):
            raise ValueError("Telegram summary must already be privacy safe")
        lines = [safe_summary, f"Mã đối chiếu: {reference}"]
        if confirm_url is not None:
            if _origin(confirm_url) != self._desk_origin:
                raise ValueError("Untrusted Telegram confirmation URL")
            if not _CONFIRM_PATH.fullmatch(urlsplit(confirm_url).path):
                raise ValueError("Invalid Telegram confirmation locator")
            lines.append(confirm_url)
        try:
            response = self._client.post(
                self._url,
                json={
                    "chat_id": chat_id,
                    "text": "\n".join(lines),
                    "link_preview_options": {"is_disabled": True},
                    "protect_content": True,
                },
                follow_redirects=False,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError):
            raise TelegramDeliveryError("Telegram delivery failed") from None
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise TelegramDeliveryError("Telegram delivery rejected")
        result = data.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int or message_id < 0:
            raise TelegramDeliveryError("Telegram delivery response invalid")
        return str(message_id)
