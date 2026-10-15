# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Normalize workflow/confirmation events into idempotent internal notification requests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class NotifyEvent:
    recipients: tuple[str, ...]
    message: str
    idempotency_key: str
    kind: str
    subject_ref: str | None = None
    confirmation_code: str | None = None
    confirm_url: str | None = None


def _recipients(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("recipients", payload.get("candidates", ()))
    if not raw and payload.get("recipient"):
        raw = [payload["recipient"]]
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Sequence):
        return ()
    return tuple(
        dict.fromkeys(
            value if ":" in value else f"person:{value}"
            for value in raw
            if isinstance(value, str) and value
        )
    )


def notification_event(
    event_id: str,
    kind: str,
    payload: Mapping[str, Any],
    *,
    subject_ref: str | None = None,
) -> NotifyEvent | None:
    """Stable event identity is scoped by the repository's tenant and recipient uniqueness.

    No event can approve an operation. Confirmation events contain only the captured plan
    summary, comparison code and the Desk URL; the bot only displays those values.
    """
    if not event_id:
        raise ValueError("a durable source event id is required")
    if kind in ("assignment", "assignment.created", "assignment.assigned"):
        message = f"Bạn có công việc mới: {payload.get('task', '')}"
        output_kind = "assignment"
    elif kind in ("escalation", "assignment.escalated"):
        message = f"Công việc được chuyển cho bạn (leo thang): {payload.get('task', '')}"
        output_kind = "escalation"
    elif kind == "confirmation_required":
        message = str(payload.get("plan_summary") or "Có thao tác đang chờ xác nhận trên Desk.")
        output_kind = "confirmation_required"
        if not payload.get("confirmation_code") or not payload.get("confirm_url"):
            raise ValueError("confirmation notification requires comparison code and Desk URL")
    else:
        return None
    recipients = _recipients(payload)
    if not recipients:
        return None
    digest = hashlib.sha256(f"{kind}:{event_id}".encode()).hexdigest()
    return NotifyEvent(
        recipients=recipients,
        message=message[:1000],
        idempotency_key=f"event:{digest}",
        kind=output_kind,
        subject_ref=subject_ref,
        confirmation_code=(
            str(payload["confirmation_code"]) if output_kind == "confirmation_required" else None
        ),
        confirm_url=(
            str(payload["confirm_url"]) if output_kind == "confirmation_required" else None
        ),
    )
