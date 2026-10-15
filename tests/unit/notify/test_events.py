# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Event normalization uses stable durable identity and never performs approval."""

import pytest

from xt.application.notify_events import notification_event


def test_assignment_and_escalation_are_idempotent_and_recipient_deduplicated() -> None:
    payload = {"candidates": ["person:a", "person:a", "person:b"], "task": "Nộp báo cáo"}
    first = notification_event("42", "assignment.created", payload, subject_ref="task:report")
    assert first is not None
    assert first == notification_event(
        "42", "assignment.created", payload, subject_ref="task:report"
    )
    assert first.recipients == ("person:a", "person:b")
    assert first.subject_ref == "task:report"
    escalated = notification_event("42", "assignment.escalated", payload)
    assert escalated is not None and escalated.idempotency_key != first.idempotency_key
    assert escalated.kind == "escalation"


def test_confirmation_preserves_captured_summary_code_and_desk_link_only() -> None:
    payload = {
        "recipient": "person:a",
        "plan_summary": "Chuyển trạng thái yêu cầu",
        "confirmation_code": "R-42#1234abcd",
        "confirm_url": "https://desk.example/desk/confirm/42",
    }
    event = notification_event("pending:42", "confirmation_required", payload)
    assert event is not None
    assert event.message == payload["plan_summary"]
    assert event.confirmation_code == payload["confirmation_code"]
    assert event.confirm_url == payload["confirm_url"]
    assert not hasattr(event, "decision")


def test_confirmation_requires_both_display_binding_and_desk_url() -> None:
    with pytest.raises(ValueError, match="comparison code"):
        notification_event("42", "confirmation_required", {"recipient": "person:a"})


def test_unhandled_or_unassigned_events_do_not_notify() -> None:
    assert notification_event("42", "assignment.unresolved", {"candidates": []}) is None
    assert notification_event("42", "assignment", {}) is None
    with pytest.raises(ValueError, match="durable"):
        notification_event("", "assignment", {})


def test_process_candidate_ids_are_normalized_to_person_refs() -> None:
    event = notification_event("42", "assignment.escalated", {"candidates": ["user-1"]})
    assert event is not None
    assert event.recipients == ("person:user-1",)
