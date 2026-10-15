# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tenant notification ports. Telegram carries opaque references, never approval evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Person:
    id: UUID
    ref: str
    name: str


@dataclass(frozen=True, slots=True)
class Notification:
    id: UUID
    recipient_id: UUID
    recipient_ref: str
    recipient_name: str
    message: str
    subject_ref: str | None = None
    kind: str = "notification"
    confirmation_code: str | None = None
    confirm_url: str | None = None
    status: str = "queued"


class TelegramSender(Protocol):
    def send(
        self, chat_id: str, *, summary: str, reference: str, confirm_url: str | None = None
    ) -> str: ...


class NotificationRepository(Protocol):
    def enqueue(
        self, actor_id: UUID, key: str, digest: str, rows: list[Notification]
    ) -> list[Notification]: ...

    def pending(self, limit: int) -> list[tuple[Notification, str]]: ...

    def egress(self, row: Notification, outcome: str) -> None: ...

    def delivered(self, notification_id: UUID, external_id: str) -> None: ...

    def failed(self, notification_id: UUID) -> None: ...


class Notifier(Protocol):
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
    ) -> list[Notification]: ...
