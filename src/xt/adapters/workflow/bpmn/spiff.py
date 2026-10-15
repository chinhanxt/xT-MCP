# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`ProcessEngine` hiện thực bằng SpiffWorkflow 3.2.0 (compose, không sửa thư viện)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from SpiffWorkflow.bpmn import BpmnWorkflow
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.util.task import TaskState

from xt.adapters.workflow.bpmn.compiler import NOTIFY_OP, compile_process
from xt.adapters.workflow.bpmn.engine import safe_engine
from xt.adapters.workflow.bpmn.hardening import parse_bpmn
from xt.adapters.workflow.bpmn.serializer import from_state, to_state
from xt.application.process.ports import CompiledProcess, ServiceRegistry, UserTask
from xt.domain.process.errors import DecisionError, EngineError, SpecError, UnsafeExpression
from xt.domain.process.spec import ProcessSpec

_TIMER_EVENTS = frozenset({"TimeDateEventDefinition", "DurationTimerEventDefinition"})


def load_specs(xml: str | bytes, process_id: str) -> tuple[Any, Any]:
    """Parse BPMN đã kiểm (parser cứng) -> (spec, subprocess_specs) của SpiffWorkflow."""
    p = SpiffBpmnParser()
    p.add_bpmn_xml(parse_bpmn(xml), filename=f"{process_id}.bpmn")
    return p.get_spec(process_id), p.get_subprocess_specs(process_id)


def new_workflow(
    xml: str | bytes, process_id: str, engine: Any, data: Mapping[str, Any] | None = None
) -> BpmnWorkflow:
    spec, subs = load_specs(xml, process_id)
    wf = BpmnWorkflow(spec, subs, script_engine=engine)
    wf.task_tree.data.update(dict(data or {}))
    wf.do_engine_steps()
    return wf


def ready_user_tasks(wf: BpmnWorkflow) -> list[Any]:
    return [t for t in wf.get_tasks(state=TaskState.READY) if t.task_spec.manual]


class SpiffWorkflowHandle:
    def __init__(self, workflow: BpmnWorkflow):
        self.workflow = workflow

    def ready_user_tasks(self) -> list[UserTask]:
        return [
            UserTask(str(t.id), t.task_spec.name, t.task_spec.lane, dict(t.data))
            for t in ready_user_tasks(self.workflow)
        ]

    def complete(self, task_id: str, updates: Mapping[str, Any], remove: Iterable[str]) -> None:
        for t in ready_user_tasks(self.workflow):
            if str(t.id) == task_id:
                for k in remove:
                    t.data.pop(k, None)
                t.data.update(dict(updates))
                t.run()
                self.workflow.do_engine_steps()
                return
        raise DecisionError(f"task {task_id} không ở trạng thái chờ xử lý")

    def refresh_timers(self) -> None:
        self.workflow.refresh_timers()
        self.workflow.do_engine_steps()

    def is_completed(self) -> bool:
        return bool(self.workflow.is_completed())

    def cancel(self) -> None:
        self.workflow.cancel()

    def completed_task_names(self) -> set[str]:
        return {t.task_spec.name for t in self.workflow.get_tasks(state=TaskState.COMPLETED)}

    def data(self) -> dict[str, Any]:
        if self.workflow.is_completed():
            return dict(self.workflow.data)
        tasks = self.workflow.get_tasks(state=TaskState.READY)
        return dict(tasks[-1].data) if tasks else {}

    def next_due(self) -> datetime | None:
        due: list[datetime] = []
        for ev in self.workflow.waiting_events():
            if ev.event_type not in _TIMER_EVENTS or not isinstance(ev.value, str):
                continue
            try:
                t = datetime.fromisoformat(ev.value)
            except ValueError:
                continue
            if t.tzinfo is not None:
                due.append(t)
        return min(due) if due else None

    def to_state(self) -> dict[str, Any]:
        return to_state(self.workflow)


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SpiffProcessEngine:
    """Compile spec -> BPMN, tạo/nạp instance với script engine an toàn."""

    def __init__(self, notify_op: str = NOTIFY_OP):
        self.notify_op = notify_op
        self._spec_digest: dict[str, str] = {}

    def spec_digest(self, compiled: CompiledProcess) -> str:
        """Digest của spec SpiffWorkflow (dạng serialize) sinh từ BPMN đã compile."""
        key = hashlib.sha256(compiled.bpmn.encode()).hexdigest()
        if key not in self._spec_digest:
            spec, subs = load_specs(compiled.bpmn, compiled.process_id)
            st = to_state(BpmnWorkflow(spec, subs, script_engine=safe_engine({})))
            self._spec_digest[key] = _digest([st["spec"], st["subprocess_specs"]])
        return self._spec_digest[key]

    def compile(
        self, spec: ProcessSpec | Mapping[str, Any], allowed_ops: Mapping[str, Sequence[str]]
    ) -> CompiledProcess:
        c = compile_process(spec, allowed_ops, notify_op=self.notify_op)
        try:  # BPMN sinh ra phải parse được, nếu không: lỗi spec ngay (AUD-7)
            load_specs(c.bpmn, c.process_id)
        except UnsafeExpression as e:
            raise SpecError([f"BPMN sinh ra không hợp lệ: {e}"]) from None
        return c

    def start(
        self,
        compiled: CompiledProcess,
        *,
        instance_id: str,
        data: Mapping[str, Any],
        services: ServiceRegistry,
    ) -> SpiffWorkflowHandle:
        wf = new_workflow(
            compiled.bpmn, compiled.process_id, safe_engine(services, instance_id), data
        )
        return SpiffWorkflowHandle(wf)

    def load(
        self,
        compiled: CompiledProcess,
        state: Mapping[str, Any],
        *,
        instance_id: str,
        services: ServiceRegistry,
    ) -> SpiffWorkflowHandle:
        # AUD-4: state phải mang đúng spec của BPMN đã compile (không nhận wf của định nghĩa khác)
        got = _digest([state.get("spec"), state.get("subprocess_specs")])
        if got != self.spec_digest(compiled):
            raise EngineError(
                "state workflow không khớp BPMN đã compile của định nghĩa này",
                reason="spec_mismatch",
            )
        return SpiffWorkflowHandle(from_state(state, safe_engine(services, instance_id)))
