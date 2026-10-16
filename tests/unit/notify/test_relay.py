# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Relay poison-source, quota savepoint, and scheduler-chain regression tests; no network."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from xt.application.jobs import JobRequest
from xt.application.notify.ports import Notification, Person
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.domain.model.context import TenantRef
from xt.entrypoints import notify_runtime as module
from xt.entrypoints.composition import worker


class Sources:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.claimed: set[str] = set()

    def claim(self, key: str) -> bool:
        if key in self.claimed:
            return False
        self.claimed.add(key)
        return True

    def process_events(self) -> list[dict[str, Any]]:
        return [row for row in self.rows if f"event:{row['id']}" not in self.claimed][:500]

    def pending_confirmations(self) -> list[dict[str, Any]]:
        return []


class Repository:
    def __init__(self) -> None:
        self.rows: list[Notification] = []
        self.limited = True
        self.people = {
            "person:limited": Person(uuid4(), "person:limited", "Limited"),
            "person:available": Person(uuid4(), "person:available", "Available"),
        }

    def resolve_person(self, ref: str) -> Person | None:
        return self.people.get(ref)

    def enqueue(
        self, actor_id: Any, key: str, digest: str, rows: list[Notification]
    ) -> list[Notification]:
        if self.limited and rows[0].recipient_ref == "person:limited":
            raise ToolError(ErrorCode.RATE_LIMITED, "private recipient info")
        self.rows.extend(rows)
        return rows


class Uow:
    """Track the relevant transaction state to verify source rollback boundaries."""

    def __init__(self, sources: Sources, repository: Repository) -> None:
        self.sources, self.repository = sources, repository
        self.depth, self.max_depth = 0, 0

    @contextmanager
    def scope(self, actor: Any) -> Any:
        yield

    def run(self, fn: Any) -> Any:
        checkpoint, rows = set(self.sources.claimed), list(self.repository.rows)
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)
        try:
            return fn()
        except Exception:
            self.sources.claimed, self.repository.rows = checkpoint, rows
            raise
        finally:
            self.depth -= 1


def configured(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]
) -> tuple[module.NotificationRelay, Sources, Repository, Uow, TenantRef]:
    sources, repository = Sources(rows), Repository()
    uow = Uow(sources, repository)
    tenant = TenantRef(1, uuid4())
    runtime = SimpleNamespace(
        app_db=object(), uow=uow, store=SimpleNamespace(profile=lambda actor: None)
    )
    monkeypatch.setattr(module, "tenant_by_id", lambda db, tid: tenant)
    monkeypatch.setattr(module, "NotificationSources", lambda uow, actor: sources)
    monkeypatch.setattr(module, "PgNotificationRepository", lambda uow, actor: repository)
    relay = module.NotificationRelay(runtime, object(), {})  # type: ignore[arg-type]
    return relay, sources, repository, uow, tenant


def event(identity: str, recipients: list[str]) -> dict[str, Any]:
    return {"id": identity, "type": "assignment", "payload": {"recipients": recipients}}


def test_unassigned_source_checkpoint_allows_later_pages_to_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [event(str(i), []) for i in range(500)] + [event("later", ["person:available"])]
    relay, sources, repository, _, tenant = configured(monkeypatch, rows)
    assert relay.run(tenant.id) == 0
    assert len(sources.claimed) == 500 and not repository.rows
    relay.run(tenant.id)
    assert "event:later" in sources.claimed
    assert len(repository.rows) == 1
    relay.run(tenant.id)
    assert len(repository.rows) == 1


def test_recipient_rate_limit_rolls_back_only_its_source_and_retries_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay, sources, repository, uow, tenant = configured(
        monkeypatch, [event("limited", ["person:limited"]), event("good", ["person:available"])]
    )
    relay.run(tenant.id)
    assert sources.claimed == {"event:good"}
    assert len(repository.rows) == 1
    assert uow.max_depth >= 2  # UoW's nested run provides the real savepoint in production.
    repository.limited = False
    relay.run(tenant.id)
    assert sources.claimed == {"event:good", "event:limited"}
    assert len(repository.rows) == 2


def test_malformed_source_is_checkpointed_without_payload_logging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    malformed = {
        "id": "malformed",
        "type": "confirmation_required",
        "payload": {"recipients": ["person:available"], "plan_summary": "secret customer PII"},
    }
    relay, sources, repository, _, tenant = configured(
        monkeypatch, [malformed, event("good", ["person:available"])]
    )
    relay.run(tenant.id)
    assert sources.claimed == {"event:malformed", "event:good"}
    assert len(repository.rows) == 1
    assert "checkpointed" in caplog.text
    assert "secret customer PII" not in caplog.text


def test_notification_exception_does_not_break_next_workflow_relay_slot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(worker, "relay_timers", AsyncMock())
    monkeypatch.setattr(worker, "relay_model_changes", AsyncMock())
    jobs = worker.WorkflowJobs.__new__(worker.WorkflowJobs)
    jobs.rt = SimpleNamespace(models=SimpleNamespace(migrator=SimpleNamespace(run_jobs=Mock())))
    jobs.queue = Mock()
    tenant = TenantRef(1, uuid4())
    jobs._tenant = Mock(return_value=tenant)
    jobs.purge_reveal_tickets = Mock()
    jobs.purge_dpop_jti = Mock()
    jobs.files = None
    jobs.files_maintenance = None
    jobs.notifications = SimpleNamespace(run=Mock(side_effect=RuntimeError("bot token secret")))
    jobs.relay_interval_s = 5
    jobs._schedule_timer_starts = AsyncMock()
    jobs._schedule_relay = AsyncMock()
    job = JobRequest(tenant.id, worker.RELAY, "relay-test-1", {})
    connection = object()
    asyncio.run(jobs.relay(connection, job))
    jobs._schedule_timer_starts.assert_awaited_once_with(connection, tenant.id)
    jobs._schedule_relay.assert_awaited_once_with(connection, tenant.id, after=5)
    assert "RuntimeError" in caplog.text and "bot token secret" not in caplog.text
