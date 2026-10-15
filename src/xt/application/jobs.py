# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Job queue boundary independent of the database driver and queue implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID


def _validate_identifier(value: str, name: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise ValueError(
            f"{name} must contain 1 to {maximum} characters without edge whitespace/NUL"
        )


def _validate_json_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload object keys must be strings")
            _validate_json_keys(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_keys(item)


@dataclass(frozen=True, slots=True, init=False)
class JobRequest:
    """Validated tenant-scoped work, with an immutable JSON payload snapshot.

    ``payload`` returns a fresh dictionary so callers cannot change work after it
    has been validated. The idempotency key is scoped to ``tenant_id``.
    """

    tenant_id: UUID
    task_name: str
    idempotency_key: str
    _payload_json: str = field(repr=False)

    def __init__(
        self,
        tenant_id: UUID,
        task_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> None:
        if not isinstance(tenant_id, UUID):
            raise ValueError("tenant_id must be a UUID")
        _validate_identifier(task_name, "task_name", 128)
        _validate_identifier(idempotency_key, "idempotency_key", 255)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        try:
            # Serialize first: the encoder rejects circular structures before
            # the string-key check traverses the payload.
            payload_json = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            _validate_json_keys(payload)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(
                "payload must be a finite, JSON-serializable object with string keys"
            ) from exc
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "task_name", task_name)
        object.__setattr__(self, "idempotency_key", idempotency_key)
        object.__setattr__(self, "_payload_json", payload_json)

    @property
    def payload(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self._payload_json))


def validate_schedule_at(when: datetime) -> datetime:
    """Require an explicit timezone; adapters may normalize the returned instant."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("scheduled time must include a timezone with a UTC offset")
    return when


class JobQueue(Protocol):
    """Persist work using the caller's open transaction, without committing it.

    ``transaction`` is an opaque handle owned by the adapter. An implementation
    validates its driver-specific type; the application never imports a driver.
    Workers must establish tenant context from ``JobRequest.tenant_id`` before
    processing work. Cancellation and deduplication are always tenant-scoped.
    """

    async def enqueue(self, transaction: object, job: JobRequest) -> int:
        """Return the persisted job id, deduplicating its tenant/idempotency key."""
        ...

    async def schedule_at(self, transaction: object, job: JobRequest, when: datetime) -> int:
        """Enqueue work due at a timezone-aware instant."""
        ...

    async def cancel(self, transaction: object, *, tenant_id: UUID, idempotency_key: str) -> bool:
        """Cancel matching pending work; return whether a job was cancelled."""
        ...

    async def periodic(
        self, transaction: object, job: JobRequest, *, cron: str, timezone: str
    ) -> int:
        """Register recurring work using a cron expression and IANA timezone."""
        ...
