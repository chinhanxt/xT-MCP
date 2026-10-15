# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Validation at the application job queue boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any
from uuid import UUID

import pytest

from xt.application.jobs import JobRequest, validate_schedule_at

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")


def test_job_request_preserves_json_payload_without_sharing_mutable_state() -> None:
    payload: dict[str, Any] = {"record": {"ids": [1, 2]}, "label": "duyệt", "optional": None}
    job = JobRequest(TENANT_ID, "workflow.timer", "instance:42:due", payload)

    payload["record"]["ids"].append(3)
    exposed = job.payload
    exposed["record"]["ids"].append(4)

    assert job.payload == {"record": {"ids": [1, 2]}, "label": "duyệt", "optional": None}
    assert job.tenant_id == TENANT_ID
    assert job.task_name == "workflow.timer"
    assert job.idempotency_key == "instance:42:due"
    with pytest.raises(FrozenInstanceError):
        job.task_name = "another.task"  # type: ignore[misc]


@pytest.mark.parametrize("tenant_id", [None, "10000000-0000-0000-0000-000000000001", 1])
def test_job_request_requires_uuid_tenant(tenant_id: Any) -> None:
    with pytest.raises(ValueError, match="tenant_id"):
        JobRequest(tenant_id, "task", "key", {})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_name", ""),
        ("task_name", "  "),
        ("task_name", " task"),
        ("task_name", "task\x00name"),
        ("task_name", "x" * 129),
        ("task_name", None),
        ("idempotency_key", ""),
        ("idempotency_key", "\t"),
        ("idempotency_key", "key "),
        ("idempotency_key", "key\x00value"),
        ("idempotency_key", "x" * 256),
        ("idempotency_key", 1),
    ],
)
def test_job_request_rejects_invalid_identifiers(field: str, value: Any) -> None:
    values: dict[str, Any] = {"task_name": "task", "idempotency_key": "key"}
    values[field] = value
    with pytest.raises(ValueError, match=field):
        JobRequest(TENANT_ID, payload={}, **values)


def test_job_request_accepts_identifier_length_boundaries() -> None:
    job = JobRequest(TENANT_ID, "x" * 128, "k" * 255, {})
    assert len(job.task_name) == 128
    assert len(job.idempotency_key) == 255


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"id": TENANT_ID},
        {"created": datetime(2026, 10, 15, tzinfo=UTC)},
        {"bad": {1, 2}},
        {"bad": float("nan")},
        {"nested": [float("inf")]},
        {"nested": {1: "not a JSON string key"}},
    ],
)
def test_job_request_rejects_non_json_object_payload(payload: Any) -> None:
    with pytest.raises(ValueError, match="payload"):
        JobRequest(TENANT_ID, "task", "key", payload)


def test_job_request_rejects_circular_payload() -> None:
    payload: dict[str, Any] = {}
    payload["cycle"] = payload
    with pytest.raises(ValueError, match="payload"):
        JobRequest(TENANT_ID, "task", "key", payload)


@pytest.mark.parametrize("offset", [UTC, timezone(timedelta(hours=7))])
def test_schedule_preserves_aware_datetime(offset: tzinfo) -> None:
    when = datetime(2026, 10, 15, 12, 30, tzinfo=offset)
    assert validate_schedule_at(when) is when


def test_schedule_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone"):
        validate_schedule_at(datetime(2026, 10, 15, 12, 30))


def test_schedule_rejects_tzinfo_without_an_offset() -> None:
    class NoOffset(tzinfo):
        def utcoffset(self, dt: datetime | None) -> None:
            return None

    with pytest.raises(ValueError, match="timezone"):
        validate_schedule_at(datetime(2026, 10, 15, tzinfo=NoOffset()))
