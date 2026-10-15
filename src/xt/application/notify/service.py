# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Durable notification enqueue and Telegram relay through application ports."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from xt.application.notify.ports import Notification, NotificationRepository, Person, TelegramSender
from xt.application.tools.errors import invalid_request

SAFE_SUMMARY = "Bạn có thông báo mới trong xT. Mở Desk để xem nội dung."


class NotificationService:
    def __init__(
        self,
        repository: NotificationRepository,
        sender: TelegramSender | None = None,
        *,
        confirm_base_url: str | None = None,
    ) -> None:
        self.repository = repository
        self.sender = sender
        self.confirm_base_url = confirm_base_url

    def send(
        self,
        *,
        actor_id: UUID,
        recipients: list[Person],
        message: str,
        idempotency_key: str,
        subject_ref: str | None = None,
        kind: str = "notification",
        confirmation_code: str | None = None,
        confirm_url: str | None = None,
    ) -> list[Notification]:
        if not 1 <= len(recipients) <= 50 or not 1 <= len(message) <= 1000:
            raise invalid_request("Notification requires 1–50 recipients and 1–1000 characters.")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", idempotency_key):
            raise invalid_request("Invalid notification idempotency key.")
        if kind not in {"notification", "assignment", "escalation", "confirmation_required"}:
            raise invalid_request("Invalid notification kind.")
        if confirmation_code and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", confirmation_code):
            raise invalid_request("Invalid comparison code.")
        if confirm_url:
            parsed = urlsplit(confirm_url)
            base = urlsplit(self.confirm_base_url or "")
            if (
                not base.netloc
                or (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc)
                or parsed.scheme not in {"https", "http"}
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not re.fullmatch(r"/desk/confirm/[A-Za-z0-9_-]+", parsed.path)
            ):
                raise invalid_request("Confirmation URL must point to the configured Desk.")
        # Recipients are canonicalized before hashing: duplicate aliases cannot duplicate delivery.
        unique = {p.id: p for p in recipients}
        people = sorted(unique.values(), key=lambda p: str(p.id))
        payload = [str(actor_id), [str(p.id) for p in people], message, subject_ref, kind,
                   confirmation_code, confirm_url]
        digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode()).hexdigest()
        rows = [Notification(uuid4(), p.id, p.ref, p.name, message, subject_ref, kind,
                             confirmation_code, confirm_url) for p in people]
        return self.repository.enqueue(actor_id, idempotency_key, digest, rows)

    def deliver_pending(self, limit: int = 50) -> int:
        if self.sender is None:
            return 0
        delivered = 0
        for row, chat_id in self.repository.pending(limit):
            # Free text may contain names or addresses unrecognizable by regex. Omit all of it.
            # Record egress BEFORE the network boundary, including failed/ambiguous attempts.
            self.repository.egress(row, "attempted")
            try:
                external_id = self.sender.send(
                    chat_id, summary=SAFE_SUMMARY,
                    reference=row.confirmation_code or str(row.id), confirm_url=row.confirm_url,
                )
            except Exception:
                self.repository.failed(row.id)
                continue
            self.repository.delivered(row.id, external_id)
            delivered += 1
        return delivered
