# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Use case process: start, inbox, act, reassign, tick. Mỗi lời gọi là một đơn vị công việc:
nạp instance (khóa dòng qua `InstanceStore.get_for_update`) -> runtime -> lưu (+ chỉ mục ứng viên,
`next_due`). Transaction, IAM, xác nhận của người (D8) và audit do dispatcher bao ngoài."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from xt.application.process.ports import InstanceRecord, InstanceStore
from xt.application.process.runtime import ProcessInstance, ProcessRuntime
from xt.domain.process.errors import DecisionError, EngineError, ProcessError


class ProcessCatalog(Protocol):
    """Định nghĩa đã kích hoạt. Instance cũ chạy đúng version đã khởi tạo."""

    def active(self, process_id: str) -> ProcessRuntime | None: ...

    def get(self, process_id: str, version: int) -> ProcessRuntime | None: ...


class InMemoryProcessCatalog:
    def __init__(self) -> None:
        self._by_version: dict[tuple[str, int], ProcessRuntime] = {}
        self._active: dict[str, int] = {}

    def add(self, rt: ProcessRuntime, *, activate: bool = True) -> None:
        key = (rt.compiled.process_id, rt.compiled.version)
        if key in self._by_version:
            raise DecisionError(f"định nghĩa {key} đã tồn tại (bất biến)")
        self._by_version[key] = rt
        if activate:
            self._active[rt.compiled.process_id] = rt.compiled.version

    def active(self, process_id: str) -> ProcessRuntime | None:
        v = self._active.get(process_id)
        return None if v is None else self._by_version[(process_id, v)]

    def get(self, process_id: str, version: int) -> ProcessRuntime | None:
        return self._by_version.get((process_id, version))


def _record(inst: ProcessInstance) -> InstanceRecord:
    m = inst.rt.manifest
    return InstanceRecord(
        process_id=m["process_id"],
        version=m["version"],
        status=inst.status(),
        state=inst.to_state(),
        candidates=inst.candidates(),
        next_due=inst.wf.next_due(),
    )


def summary(inst: ProcessInstance, user: str | None = None) -> dict[str, Any]:
    return {"instance": inst.id, "status": inst.status(), "tasks": inst.inbox(user)}


class ProcessService:
    def __init__(self, catalog: ProcessCatalog, store: InstanceStore):
        self.catalog = catalog
        self.store = store

    def _runtime(self, process_id: str, version: int | None = None) -> ProcessRuntime:
        rt = (
            self.catalog.active(process_id)
            if version is None
            else self.catalog.get(process_id, version)
        )
        if rt is None:
            raise DecisionError(f"không có quy trình {process_id!r}", code="not_found")
        return rt

    def _load(self, instance_id: str) -> ProcessInstance:
        rec = self.store.get_for_update(instance_id)
        if rec is None:
            raise DecisionError(f"không có instance {instance_id!r}", code="not_found")
        inst = self._runtime(rec.process_id, rec.version).load(rec.state)
        if inst.id != instance_id:  # AUD-5: dòng A không được mang state của instance khác
            raise EngineError(
                f"state của dòng {instance_id!r} thuộc instance {inst.id!r}",
                reason="state_mismatch",
            )
        return inst

    def _apply(self, instance_id: str, op: Callable[[ProcessInstance], None]) -> dict[str, Any]:
        """Chạy op rồi lưu. EngineError: wf đã khôi phục, meta có `incidents` -> vẫn lưu để
        quản trị thấy, rồi ném lại (AUD-3)."""
        inst = self._load(instance_id)
        try:
            op(inst)
        except EngineError:
            self.store.save(instance_id, _record(inst))
            raise
        self.store.save(instance_id, _record(inst))
        return summary(inst)

    def start(
        self,
        process_id: str,
        data: Mapping[str, Any],
        requester: str,
        *,
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        inst = self._runtime(process_id).start(data, requester, instance_id=instance_id)
        self.store.insert(inst.id, _record(inst))
        return summary(inst)

    def get(self, instance_id: str, user: str | None = None) -> dict[str, Any]:
        inst = self._load(instance_id)
        m = inst.rt.manifest
        return {**summary(inst, user), "process_id": m["process_id"], "version": m["version"],
                "history": inst.meta["history"],
                "incidents": inst.meta.get("incidents", [])}  # fmt: skip

    def inbox(self, user: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for iid in self.store.open_for(user):
            try:
                inst = self._load(iid)
            except ProcessError:  # một dòng hỏng không làm hỏng cả inbox
                continue
            pid = inst.rt.manifest["process_id"]
            out.extend({**t, "process_id": pid} for t in inst.inbox(user))
        return out

    def act(
        self,
        instance_id: str,
        task_id: str,
        actor: str,
        outcome: str,
        comment: str | None = None,
        **kw: Any,
    ) -> dict[str, Any]:
        return self._apply(instance_id, lambda i: i.act(task_id, actor, outcome, comment, **kw))

    def check_delegate(
        self, instance_id: str, task_id: str, actor: str, delegate_to: str | None
    ) -> None:
        """Kiểm đích ủy quyền mà không lưu gì (plan của decide_request)."""
        self._load(instance_id).check_delegate(task_id, actor, delegate_to)

    def withdraw(self, instance_id: str, actor: str, reason: str | None = None) -> dict[str, Any]:
        return self._apply(instance_id, lambda i: i.withdraw(actor, reason))

    def reassign(
        self, instance_id: str, task_id: str, admin: str, to_user: str, reason: str
    ) -> dict[str, Any]:
        return self._apply(instance_id, lambda i: i.reassign(task_id, admin, to_user, reason))

    def tick(self, instance_id: str) -> dict[str, Any]:
        """Worker gọi khi `next_due` tới (đánh thức qua `TimerScheduler`)."""
        return self._apply(instance_id, lambda i: i.tick())
