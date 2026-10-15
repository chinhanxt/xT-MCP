# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng của process engine. Application chỉ biết các Protocol này; SpiffWorkflow/BPMN nằm ở
`xt.adapters.workflow.bpmn`, SQL nằm ở `xt.adapters.db`, hàng đợi do lớp tích hợp nối vào."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from xt.domain.process.spec import ProcessSpec

ServiceFn = Callable[..., Any]
ServiceRegistry = Mapping[str, ServiceFn]
"""op -> hàm Python. Chỉ op có trong registry mới chạy được (allowlist theo deployment)."""

EventSink = Callable[[dict[str, Any]], None]
"""Nhận sự kiện runtime (`assignment.escalated`, `assignment.unresolved`...). Best-effort."""


@dataclass(frozen=True, slots=True)
class CompiledProcess:
    """Kết quả compile: BPMN 2.0 (+BPMNDI) bất biến + manifest JSON cho runtime."""

    process_id: str
    bpmn: str
    manifest: dict[str, Any]
    spec: ProcessSpec = field(repr=False)

    @property
    def version(self) -> int:
        return int(self.manifest["version"])

    @property
    def sha256(self) -> str:
        return str(self.manifest["spec_sha256"])


@dataclass(frozen=True, slots=True)
class UserTask:
    """Một user task đang READY. `data` là bản sao dữ liệu tại task (chỉ đọc)."""

    id: str
    name: str
    lane: str | None
    data: Mapping[str, Any]


class WorkflowHandle(Protocol):
    """Một instance đang nạp trong engine."""

    def ready_user_tasks(self) -> list[UserTask]: ...

    def complete(self, task_id: str, updates: Mapping[str, Any], remove: Iterable[str]) -> None:
        """Xóa `remove`, ghi `updates` vào task, chạy task rồi chạy tiếp các bước tự động."""
        ...

    def refresh_timers(self) -> None:
        """Đánh giá timer (SLA, timer start) theo đồng hồ hiện tại rồi chạy tiếp."""
        ...

    def is_completed(self) -> bool: ...

    def cancel(self) -> None:
        """Hủy mọi task còn mở (rút đề nghị đang chạy); sau đó `is_completed()` là True."""
        ...

    def completed_task_names(self) -> set[str]: ...

    def data(self) -> dict[str, Any]: ...

    def next_due(self) -> datetime | None:
        """Thời điểm timer gần nhất đang chờ (để hẹn đánh thức), None nếu không có."""
        ...

    def to_state(self) -> dict[str, Any]:
        """State JSON. Lưu ở cột `json` (giữ thứ tự key), không phải `jsonb`."""
        ...


class ProcessEngine(Protocol):
    def compile(
        self, spec: ProcessSpec | Mapping[str, Any], allowed_ops: Mapping[str, Sequence[str]]
    ) -> CompiledProcess: ...

    def start(
        self,
        compiled: CompiledProcess,
        *,
        instance_id: str,
        data: Mapping[str, Any],
        services: ServiceRegistry,
    ) -> WorkflowHandle: ...

    def load(
        self,
        compiled: CompiledProcess,
        state: Mapping[str, Any],
        *,
        instance_id: str,
        services: ServiceRegistry,
    ) -> WorkflowHandle: ...


class TimerScheduler(Protocol):
    """Hẹn đánh thức instance khi timer (SLA) tới hạn. Lớp tích hợp nối vào job queue
    (`xt.application.jobs.JobQueue.schedule_at`), dedupe theo (instance, due)."""

    def wake_at(self, *, process_id: str, instance_id: str, due: datetime) -> None: ...


class TimerFireTx(Protocol):
    """Một transaction của poller timer start."""

    def claim_period(
        self, *, process_key: str, period_key: str, status: str, version: int, fired_at: datetime
    ) -> bool:
        """INSERT ... ON CONFLICT DO NOTHING; True nếu transaction này giành được kỳ."""
        ...

    def save_instance(
        self, *, instance_id: str, process_key: str, version: int, state: Mapping[str, Any]
    ) -> None: ...

    def link_instance(self, *, process_key: str, period_key: str, instance_id: str) -> None: ...


class TimerFireStore(Protocol):
    def transaction(self) -> AbstractContextManager[TimerFireTx]:
        """Commit khi thoát bình thường, rollback khi có ngoại lệ."""
        ...


class InstanceStore(Protocol):
    """Lưu instance. `get_for_update` khóa dòng tới hết transaction của use case (adapter lo)."""

    def insert(self, instance_id: str, record: InstanceRecord) -> None: ...

    def get_for_update(self, instance_id: str) -> InstanceRecord | None: ...

    def save(self, instance_id: str, record: InstanceRecord) -> None: ...

    def open_for(self, user: str) -> list[str]:
        """Id các instance đang có task mà `user` là ứng viên."""
        ...


@dataclass(slots=True)
class InstanceRecord:
    process_id: str
    version: int
    status: str
    state: dict[str, Any]
    candidates: list[str] = field(default_factory=list)
    next_due: datetime | None = None
