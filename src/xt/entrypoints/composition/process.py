# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Port tool trên process engine: describe_process, submit_request, decide_request, start_process,
get_inbox, transition_record (rút đề nghị; hoàn thành bước người gắn bản ghi chủ thể).

Nối (docs/process-engine.md §7):
- `ProcessCatalog`: định nghĩa đã duyệt trong `xt_meta.process_definition`, compile lại theo đúng
  `(process_id, version)` và đối chiếu `spec_sha256` (instance cũ chạy đúng bản của nó);
- `InstanceStore`: `PgWorkflowStore` (cùng transaction của lời gọi tool);
- `OrgDirectory`: `xt_meta.person` / `xt_meta.org_unit`;
- `TimerScheduler`: `PgTimerScheduler` ghi outbox trong transaction; worker relay sang
  `JobQueue.schedule_at` rồi job `workflow.tick` gọi `ProcessService.tick`;
- `EventSink`: `xt_meta.process_event` (cùng transaction) + thông báo cho người nhận leo thang;
- op dịch vụ: registry `OPS` của deployment (pack chỉ khai tên + tham số; op lạ -> từ chối cài).

Trạng thái bản ghi `request` (trường `state`, managed_by=workflow) chỉ đổi ở đây, qua
`RecordService.workflow_write` với vai trò xt_workflow.

Bước người của quy trình không gắn đề nghị (giao việc, vòng đời nhân sự) khai `complete_via` trong
spec: `transition_record` trên đúng bản ghi chủ thể của bước, bởi người được giao (IAM
`record.transition` + ứng viên của bước + SoD), mức tự chủ theo `complete_via.autonomy` của gói
(`human_only` -> luôn cần chính người được giao xác nhận). Quyết định: docs/decisions.md D-STEP-*.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from xt.adapters.db.integration import ProcessDefinition, SodViolation
from xt.adapters.db.notification_sources import NotificationSources
from xt.adapters.db.notify import PgNotificationRepository
from xt.adapters.db.workflow_bridge import PgTimerScheduler, PgWorkflowStore
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.application.model_engine.service import jsonable
from xt.application.notify.service import NotificationService
from xt.application.notify_events import NotifyEvent, notification_event
from xt.application.process.ports import CompiledProcess, ServiceFn
from xt.application.process.runtime import ProcessRuntime
from xt.application.process.service import ProcessService as EngineProcesses
from xt.application.tools.context import CallContext, PersonRef, iso
from xt.application.tools.errors import (
    ToolError,
    access_denied,
    invalid_request,
    not_found,
    precondition_failed,
    revision_conflict,
)
from xt.application.tools.ports import Args, CommitResult, Data, MutationPlan
from xt.contracts.tools import ErrorCode
from xt.domain.autonomy import RiskLevel
from xt.domain.model.context import ModelActor
from xt.domain.process.assignment import OrgUser
from xt.domain.process.errors import EngineError
from xt.domain.process.expr import evaluate
from xt.domain.process.spec import ApproveStep, CompleteVia, ProcessSpec, TaskStep
from xt.entrypoints.composition.model_changes import (
    MODEL_PROPOSAL,
    ModelChangeDecisions,
    ModelDecidePlan,
)
from xt.entrypoints.composition.pack_ops import ACTING, PACK_OPS
from xt.entrypoints.composition.runtime import Caller, Runtime, after_rollback
from xt.entrypoints.composition.util import (
    engine_call,
    parse_person_ref,
    parse_record_ref,
    signed_page,
    unavailable,
)

DECISIONS = {
    "approve": "approve",
    "reject": "reject",
    "return": "return_for_revision",
    "delegate": "delegate",
}
OPEN_STATES = ("draft", "returned")


# ------------------------------------------------------------------------- tổ chức ----
class OrgView:
    """`OrgDirectory` của process runtime trên bảng tổ chức (id người = uuid dạng chuỗi)."""

    def __init__(self, rt: Runtime, actor: ModelActor) -> None:
        self.rt, self.actor = rt, actor

    def get_user(self, uid: object) -> OrgUser | None:
        try:
            pid = UUID(str(uid))
        except ValueError:
            return None
        p = self.rt.store.person(self.actor, pid)
        if p is None:
            return None
        return OrgUser(
            str(p.id), p.active, p.unit_key, tuple(p.roles), str(p.manager) if p.manager else None
        )

    def department_head(self, dept: str) -> str | None:
        u = self.rt.store.unit(self.actor, key=dept)
        return str(u.head) if u and u.head else None

    def users_with_role(self, role: str, dept: str | None = None) -> list[str]:
        unit = None
        if dept is not None:
            u = self.rt.store.unit(self.actor, key=dept)
            if u is None:
                return []
            unit = u.id
        _, people = self.rt.store.people(self.actor, role=role, unit=unit, limit=1000)
        return sorted(str(p.id) for p in people)

    def department_exists(self, dept: object) -> bool:
        return isinstance(dept, str) and self.rt.store.unit(self.actor, key=dept) is not None


# ------------------------------------------------------------------------- op dịch vụ ----
OpFactory = Callable[["ProcessContext", ProcessDefinition], ServiceFn]


def _check_overdue_advances(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Số đề nghị tạm ứng đã duyệt, chưa hoàn ứng, quá hạn hoàn ứng của người đề nghị."""

    def op(requester: str) -> dict[str, int]:
        today = datetime.now(ZoneInfo(pc.timezone)).date().isoformat()
        where = {
            "and": [
                {"field": "owner_id", "op": "=", "value": str(requester)},
                {"field": "state", "op": "=", "value": "approved"},
                {"field": "settled", "op": "=", "value": False},
                {"field": "settlement_due", "op": "<", "value": today},
            ]
        }
        res = engine_call(
            lambda: pc.rt.records.search(pc.actor, "advance_request", where, ("-created_at",), 1, 0)
        )
        return {"overdue": int(res["total"])}

    return op


def _notify(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Thông báo leo thang SLA cho người đề nghị của instance (khóa idempotency = instance:task)."""

    def op(msg: str, idempotency_key: str) -> dict[str, int]:
        instance_id = idempotency_key.split(":", 1)[0]
        rec = PgWorkflowStore(pc.rt.uow).get_for_update(instance_id)
        requester = (rec.state.get("meta") or {}).get("requester") if rec else None
        ref = PgWorkflowStore(pc.rt.uow).ref_for(instance_id)
        if requester:
            pc.rt.store.notify(pc.actor, UUID(str(requester)), str(msg), ref, idempotency_key)
        pc.rt.store.process_event(
            pc.actor, instance_id, "notify", {"msg": str(msg), "key": idempotency_key}
        )
        return {"ok": 1}

    return op


OPS: Mapping[str, tuple[tuple[str, ...], OpFactory]] = {
    "check_overdue_advances": (("requester",), _check_overdue_advances),
    "notify": (("msg",), _notify),
    **PACK_OPS,
}


def check_pack_ops(allowed: Mapping[str, list[str]]) -> list[str]:
    """Lỗi nếu pack khai op mà deployment không có, hoặc khai tham số khác."""
    problems = []
    for op, params in allowed.items():
        impl = OPS.get(op)
        if impl is None:
            problems.append(f"op {op!r} chưa có hiện thực trong deployment (có: {sorted(OPS)})")
        elif tuple(params) != impl[0]:
            problems.append(f"op {op!r}: tham số {list(params)} khác hiện thực {list(impl[0])}")
    return problems


# ------------------------------------------------------------------------- notifications ----
ESCALATION_EVENTS = frozenset({"assignment.escalated", "escalation"})


def enqueue_notification(
    sources: NotificationSources,
    repo: PgNotificationRepository,
    source_key: str,
    event: NotifyEvent | None,
    *,
    actor_id: UUID,
    confirm_base_url: str | None = None,
) -> bool:
    """Ghi thông báo của một nguồn (sự kiện/xác nhận/giao việc) qua Notifier, đúng một lần.

    Claim `notification_source` và hộp thư cùng transaction của người gọi; nguồn không có người
    nhận hợp lệ vẫn được checkpoint. Trả về True nếu lần này claim được nguồn."""
    if not sources.claim(source_key):
        return False
    if event is None:
        return True
    active = [p for p in (repo.resolve_person(ref) for ref in event.recipients) if p is not None]
    if active:
        NotificationService(repo, confirm_base_url=confirm_base_url).send(
            actor_id=actor_id,
            recipients=active,
            message=event.message,
            idempotency_key=event.idempotency_key,
            kind=event.kind,
            subject_ref=event.subject_ref,
            confirmation_code=event.confirmation_code,
            confirm_url=event.confirm_url,
        )
    return True


# ---------------------------------------------------------------------- catalog/runtime ----
_COMPILED: dict[tuple[UUID, str, int, str], CompiledProcess] = {}
_COMPILED_LOCK = threading.Lock()


@dataclass
class ProcessContext:
    """Một đơn vị công việc process: actor RLS cho op/sự kiện, múi giờ tenant."""

    rt: Runtime
    engine: SpiffProcessEngine
    actor: ModelActor
    timezone: str

    def compiled(self, d: ProcessDefinition) -> CompiledProcess:
        key = (self.actor.tenant.id, d.process_id, d.version, d.spec_sha256)
        with _COMPILED_LOCK:
            hit = _COMPILED.get(key)
        if hit is not None:
            return hit
        c: CompiledProcess = self.engine.compile(d.spec, d.allowed_ops)
        if c.sha256 != d.spec_sha256:
            raise EngineError(
                f"định nghĩa {d.process_id} v{d.version}: sha256 compile {c.sha256} khác bản đã"
                f" duyệt {d.spec_sha256}",
                reason="spec_mismatch",
            )
        with _COMPILED_LOCK:
            _COMPILED[key] = c
        return c

    def runtime(self, d: ProcessDefinition) -> ProcessRuntime:
        services = {op: OPS[op][1](self, d) for op in d.allowed_ops if op in OPS}
        return ProcessRuntime(
            self.compiled(d),
            self.engine,
            services,
            OrgView(self.rt, self.actor),
            on_event=self._event,
            scheduler=PgTimerScheduler(self.rt.uow),
        )

    def _event(self, ev: dict[str, Any]) -> None:
        iid = str(ev.get("instance", ""))
        type_ = str(ev.get("type", "event"))
        payload = jsonable(ev)
        event_id = self.rt.store.process_event(self.actor, iid, type_, payload)
        if type_ in ESCALATION_EVENTS:
            # Leo thang SLA báo ngay qua Notifier (in-app, cùng transaction với sự kiện); claim
            # `event:<id>` trùng khóa của NotificationRelay nên relay không gửi lại.
            event = notification_event(
                str(event_id), type_, payload, subject_ref=PgWorkflowStore(self.rt.uow).ref_for(iid)
            )
            try:
                sources = NotificationSources(self.rt.uow, self.actor)
                repo = PgNotificationRepository(self.rt.uow, self.actor)
                key = f"event:{event_id}"
                self.rt.uow.run(
                    lambda: enqueue_notification(
                        sources, repo, key, event, actor_id=self.actor.actor_id
                    )
                )
            except ToolError as error:
                if error.code != ErrorCode.RATE_LIMITED:
                    raise
                # Savepoint hoàn tác claim: relay của worker gửi lại ở chu kỳ sau.

    # ProcessCatalog
    def active(self, process_id: str) -> ProcessRuntime | None:
        defs = self.rt.store.definitions(self.actor, process_id=process_id, active_only=True)
        return self.runtime(defs[0]) if defs else None

    def get(self, process_id: str, version: int) -> ProcessRuntime | None:
        d = self.rt.store.definition(self.actor, process_id, version)
        return self.runtime(d) if d else None

    def service(self) -> EngineProcesses:
        return _Processes(self, PgWorkflowStore(self.rt.uow))


class _Processes(EngineProcesses):
    """`ProcessService` + ngữ cảnh task đang hoàn thành cho op dịch vụ chạy ngay sau (ACTING)."""

    def act(
        self,
        instance_id: str,
        task_id: str,
        actor: str,
        outcome: str,
        comment: str | None = None,
        **kw: Any,
    ) -> dict[str, Any]:
        token = ACTING.set((instance_id, task_id))
        try:
            return super().act(instance_id, task_id, actor, outcome, comment, **kw)
        finally:
            ACTING.reset(token)


# ------------------------------------------------------------------------- plan payload ----
@dataclass(frozen=True)
class SubmitPlan:
    actor_key: str
    ref: str
    model: str
    record_id: UUID
    revision: int
    process_id: str
    inputs: dict[str, Any]
    requester: str
    note: str | None
    resubmit_instance: str | None


@dataclass(frozen=True)
class DecidePlan:
    actor_key: str
    ref: str
    model: str
    record_id: UUID
    revision: int
    instance_id: str
    task_id: str
    step: str
    step_name: str
    outcome: str
    decision: str
    comment: str | None
    delegate_to: str | None
    person: str


@dataclass(frozen=True)
class StartPlan:
    actor_key: str
    process_id: str
    version: int
    inputs: dict[str, Any]
    requester: str
    subject_ref: str | None


@dataclass(frozen=True)
class WithdrawPlan:
    actor_key: str
    ref: str
    model: str
    record_id: UUID
    revision: int
    instance_id: str | None
    task_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class StepPlan:
    actor_key: str
    ref: str
    model: str
    record_id: UUID
    revision: int
    instance_id: str
    task_id: str
    step: str
    step_name: str
    outcome: str
    to_state: str
    state_field: str
    values: dict[str, Any]
    comment: str | None
    person: str


@dataclass(frozen=True)
class OpenStep:
    """Một task người đang mở có bản ghi chủ thể (`complete_via`) là bản ghi đang xét."""

    instance_id: str
    task: dict[str, Any]
    step: ApproveStep | TaskStep
    via: CompleteVia

    @property
    def human_only(self) -> bool:
        return self.via.autonomy == "human_only" or isinstance(self.step, ApproveStep)

    @property
    def level(self) -> RiskLevel:
        return RiskLevel.L3 if self.human_only else RiskLevel(self.via.autonomy)


def _norm_ref(v: Any) -> str:
    """`model:<uuid>` / `<uuid>` -> `<uuid>` (so khớp ref với giá trị lưu trong DB)."""
    s = str(v)
    try:
        return str(UUID(s.rsplit(":", 1)[-1]))
    except ValueError:
        return s


def human_step(spec: ProcessSpec, step_id: str) -> ApproveStep | TaskStep | None:
    for st, _ in spec.iter_steps():
        if st.id == step_id and isinstance(st, ApproveStep | TaskStep):
            return st
    return None


def _wanted(via: CompleteVia, data: Mapping[str, Any]) -> dict[str, str] | None:
    out: dict[str, str] = {}
    for f, expr in via.match.items():
        try:
            v = evaluate(expr, dict(data))
        except Exception:  # biến chưa có: bước chưa xác định được bản ghi
            return None
        if v is None or v == "":
            return None
        out[f] = _norm_ref(v)
    return out


def _matches(via: CompleteVia, data: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    want = _wanted(via, data)
    if want is None:
        return False
    for f, v in want.items():
        got = row.get("id") if f == "id" else row.get(f)
        if got is None or _norm_ref(got) != v:
            return False
    return True


def writable_fields(via: CompleteVia, model: Any) -> set[str]:
    """Trường được ghi kèm khi hoàn thành bước: `writable_fields` của bước, trừ khóa match,
    trường trạng thái, trường ref/user/file và trường managed_by của model."""
    by = model.by_name()
    out: set[str] = set()
    for name in via.writable_fields:
        f = by.get(name)
        if f is None or name in via.match or name == via.state_field:
            continue
        if f.type in ("ref", "user", "file") or getattr(f, "managed_by", None):
            continue
        out.add(name)
    return out


def bound_record(
    pc: ProcessContext, via: CompleteVia, data: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Bản ghi chủ thể của bước (đọc bằng vai trò xt_workflow), hoặc None."""
    want = _wanted(via, data)
    if want is None:
        return None
    try:
        if "id" in want:
            row = dict(pc.rt.records.get(pc.actor, via.model, UUID(want["id"]))["record"])
            return row if _matches(via, data, row) else None
        where = {"and": [{"field": f, "op": "=", "value": v} for f, v in want.items()]}
        res = pc.rt.records.search(pc.actor, via.model, where, ("created_at",), 1, 0)
    except Exception:  # model/bản ghi không có: bước không có chủ thể
        return None
    items = list(res["items"])
    return dict(items[0]) if items else None


def _intish(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, str):
        try:
            d = Decimal(v)
        except ArithmeticError:
            return v
        return int(d) if d == d.to_integral_value() else v
    return v


class DelegatePolicy(Protocol):
    """PEP cho đích ủy quyền (`ToolAccessGuard.person_allowed`)."""

    def person_allowed(self, ctx: CallContext, person: Any, action: str, ref: str) -> bool: ...


class ProcessToolAdapter:
    """`ProcessService` + `InboxService` port của tool, và transition `withdrawn` của request."""

    def __init__(
        self,
        rt: Runtime,
        *,
        cursor_key: bytes,
        engine: SpiffProcessEngine,
        model_changes: ModelChangeDecisions | None = None,
        access: DelegatePolicy | None = None,
    ) -> None:
        self.rt = rt
        self.cursor_key = cursor_key
        self.engine = engine
        self.model_changes = model_changes
        self.access = access
        # `RecordServiceAdapter` (values_in/record_out) do composition nối sau khi tạo.
        self.record_view: Any = None

    def pc(self, caller: Caller) -> ProcessContext:
        return ProcessContext(self.rt, self.engine, caller.workflow(), caller.profile.timezone)

    # ---- tiện ích -----------------------------------------------------------------------------
    def _request(
        self, ctx: CallContext, caller: Caller, ref: str
    ) -> tuple[str, UUID, dict[str, Any], Data]:
        model, rid = parse_record_ref(ctx, ref)
        binding = self.rt.store.bindings(caller.actor).get(model)
        if binding is None or binding.primitive != "request":
            raise invalid_request(
                f"'{model}' không phải loại đề nghị có quy trình duyệt.",
                "wrong_tool",
                remediation="Use update_record/transition_record for ordinary records.",
            )
        # Đọc bằng ngữ cảnh workflow: người duyệt không phải lúc nào cũng thấy bản ghi qua RLS
        # theo phòng; quyền đã được PEP kiểm (request.submit/decide) trước bước này.
        out = engine_call(lambda: self.rt.records.get(caller.workflow(), model, rid))
        return model, rid, binding.config, out["record"]

    @staticmethod
    def _cas(row: Mapping[str, Any], expected: Any) -> int:
        current = int(row["revision"])
        if expected is not None and int(expected) != current:
            raise revision_conflict(current, int(expected))
        return current

    def _write_state(
        self, caller: Caller, model: str, rid: UUID, revision: int, state: str, field: str
    ) -> Data:
        out = engine_call(
            lambda: self.rt.records.workflow_write(
                caller.workflow(), model, rid, revision, {field: state}
            )
        )
        return dict(out["record"])

    @staticmethod
    def _state_of(summary: Mapping[str, Any], tasks: list[dict[str, Any]]) -> str:
        status = summary["status"]
        if status == "completed":
            return "approved"
        if status in ("rejected", "withdrawn"):
            return str(status)
        if any(t["kind"] == "revise" for t in tasks):
            return "returned"
        return "in_review"

    def _person(self, caller: Caller, pid: Any) -> dict[str, str] | None:
        try:
            p = self.rt.store.person(caller.actor, UUID(str(pid)))
        except ValueError:
            return None
        return {"ref": f"person:{p.id}", "name": p.name} if p else None

    def _route(
        self,
        caller: Caller,
        spec: ProcessSpec,
        data: Mapping[str, Any],
        tasks: list[dict[str, Any]],
    ) -> list[Data]:
        active = {t["step"]: t for t in tasks}
        route: list[Data] = []
        for st in spec.steps:
            if not isinstance(st, ApproveStep | TaskStep):
                continue
            if st.when:
                try:
                    if not evaluate(st.when, dict(data)):
                        continue
                except Exception:  # biến chỉ có sau bước dịch vụ: điều kiện chưa xét được
                    pass
            t = active.get(st.id)
            approver = None
            if t and t.get("candidates"):
                approver = self._person(caller, t["candidates"][0])
            target = st.target()
            rule = target.kind + (f"({target.role})" if target.role else "")
            route.append(
                {
                    "step": st.id,
                    "label": st.name,
                    "approver": approver,
                    "approver_rule": rule,
                    "due_at": None,
                }
            )
        return route

    @staticmethod
    def _instance_data(pc: ProcessContext, iid: str) -> tuple[ProcessRuntime, dict[str, Any]]:
        """Runtime đúng version của instance + biến hiện tại (input + kết quả bước dịch vụ)."""
        rec = PgWorkflowStore(pc.rt.uow).get_for_update(iid)
        rt = pc.get(rec.process_id, rec.version) if rec else None
        if rec is None or rt is None:
            raise EngineError(f"instance {iid} không nạp được", reason="state_mismatch")
        return rt, dict(rt.load(rec.state).data())

    def _require_clean_files(self, caller: Caller, model: str, row: Mapping[str, Any]) -> None:
        """TCV1 §5.14: tệp đính kèm (field `file`) phải `clean` hoặc `not_scanned` mới gửi duyệt."""
        current = self.rt.catalogs.current(caller.actor)
        if current is None:
            return
        try:
            spec = current[1].model(model)
        except KeyError:
            return
        ids: list[UUID] = []
        for f in spec.fields:
            if f.type != "file" or row.get(f.name) is None:
                continue
            raw = row[f.name]
            for v in raw if isinstance(raw, list) else [raw]:
                try:
                    ids.append(UUID(str(v).split(":", 1)[-1]))
                except ValueError:
                    continue
        if not ids:
            return
        bad = self.rt.store.files_not_clean(caller.workflow(), ids)
        if bad:
            raise precondition_failed(
                "Tệp đính kèm chưa được quét sạch (hoặc bị chặn); chưa gửi duyệt được.",
                "file_not_clean",
                details={"files": [f"file:{i}" for i in bad]},
            )

    # ---- describe_process ---------------------------------------------------------------------
    def describe_process(self, ctx: CallContext, args: Args) -> Data | None:
        caller = self.rt.caller(ctx)
        defs = self.rt.store.definitions(
            caller.actor, process_id=args["process_id"], active_only=True
        )
        if not defs:
            return None
        d = defs[0]
        spec = ProcessSpec.model_validate(d.spec)
        values = dict(args.get("for_values") or {})
        steps: list[Data] = []
        for st in spec.steps:
            kind = {"approve": "decision", "task": "task", "service": "action"}.get(st.type, "rule")
            row: Data = {"id": st.id, "label": st.name, "kind": kind}
            if isinstance(st, ApproveStep | TaskStep):
                t = st.target()
                row["assignee_rule"] = t.kind + (f"({t.role})" if t.role else "")
                row["sla"] = st.sla.after if st.sla else None
                row["human_gate"] = isinstance(st, ApproveStep)
            when = getattr(st, "when", None)
            if values and when:
                try:
                    row["applies"] = bool(evaluate(when, values))
                except Exception:
                    row["applies"] = None
            elif values:
                row["applies"] = True
            steps.append(row)
        binding_model = next(
            (m for m, b in self.rt.store.bindings(caller.actor).items()
             if b.primitive == "request" and b.config.get("process") == d.process_id),
            None,
        )  # fmt: skip
        trigger: Data = {"kind": "on_submit" if binding_model else spec.trigger.kind}
        if binding_model:
            trigger["model_id"] = binding_model
        return {
            "process": {
                "id": d.process_id,
                "label": spec.name,
                "revision": d.version,
                "status": "active",
                "trigger": trigger,
                "parameters": [
                    {"id": k, "type": v.type, "required": v.required, "label": v.description or k}
                    for k, v in spec.inputs.items()
                ],
                "steps": steps,
            }
        }

    # ---- submit_request -----------------------------------------------------------------------
    def plan_submit(self, ctx: CallContext, args: Args) -> MutationPlan:
        caller = self.rt.caller(ctx)
        model, rid, config, row = self._request(ctx, caller, args["ref"])
        state_field = str(config.get("state_field", "state"))
        state = row.get(state_field)
        if state not in OPEN_STATES:
            raise precondition_failed(
                f"Đề nghị đang ở trạng thái '{state}', chỉ gửi được khi là nháp hoặc bị trả lại.",
                "invalid_state",
                details={"state": state},
            )
        revision = self._cas(row, args.get("expected_revision"))
        self._require_clean_files(caller, model, row)
        pid = str(config["process"])
        inputs: dict[str, Any] = {}
        for name, src in dict(config.get("inputs", {})).items():
            if "const" in src:
                inputs[name] = src["const"]
            elif "field" in src:
                v = row.get(src["field"])
                if v is not None:
                    inputs[name] = _intish(v)
            elif src.get("arg") == "note" and args.get("note"):
                inputs[name] = str(args["note"])
        pc = self.pc(caller)
        rt = pc.active(pid)
        if rt is None:
            raise precondition_failed(f"Quy trình '{pid}' chưa được kích hoạt.", "process_inactive")
        engine_call(lambda: rt.validate_inputs(inputs))
        owner = str(row.get("owner_id"))
        resubmit = None
        if state == "returned":
            resubmit = PgWorkflowStore(self.rt.uow).instance_for(f"{model}:{rid}")
        plan = SubmitPlan(
            ctx.actor.key,
            f"{model}:{rid}",
            model,
            rid,
            revision,
            pid,
            inputs,
            owner,
            str(args["note"]) if args.get("note") else None,
            resubmit,
        )
        # Đề nghị của chính người được đại diện: L1; của người khác: L2 (TCV1 §5.14).
        level = RiskLevel.L1 if owner == str(caller.person.id) else RiskLevel.L2
        return MutationPlan(
            level,
            f"Gửi duyệt {model}:{rid} (quy trình {pid})",
            {
                "ref": plan.ref,
                "revision": revision,
                "process": pid,
                "process_version": rt.compiled.version,
                "inputs": jsonable(inputs),
                "resubmit": resubmit,
            },
            target_ref=plan.ref,
            payload=plan,
        )

    def _commit_submit(self, ctx: CallContext, caller: Caller, p: SubmitPlan) -> CommitResult:
        pc = self.pc(caller)
        svc = pc.service()
        store = PgWorkflowStore(self.rt.uow)
        _, _, config, row = self._request(ctx, caller, p.ref)
        self._cas(row, p.revision)
        state_field = str(config.get("state_field", "state"))
        if p.resubmit_instance:
            iid = p.resubmit_instance
            info = engine_call(lambda: svc.get(iid))
            task = next((t for t in info["tasks"] if t["kind"] == "revise"), None)
            if task is None:
                raise precondition_failed("Đề nghị không còn chờ sửa.", "invalid_state")
            active = pc.active(p.process_id)
            spec_inputs = active.manifest["inputs"] if active else {}
            updates = {
                k: v
                for k, v in p.inputs.items()
                if spec_inputs.get(k, {}).get("editable_on_revision")
            }
            summary = engine_call(
                lambda: svc.act(
                    iid, task["task_id"], p.requester, "resubmit", p.note,
                    updates=updates or None,
                )
            )  # fmt: skip
        else:
            iid = str(uuid.uuid4())
            summary = engine_call(
                lambda: svc.start(p.process_id, p.inputs, p.requester, instance_id=iid)
            )
            store.link(p.ref, iid)
        tasks = list(summary["tasks"])
        state = self._state_of(summary, tasks)
        rec = self._write_state(caller, p.model, p.record_id, p.revision, state, state_field)
        rt, data = self._instance_data(pc, iid)
        route = self._route(caller, rt.compiled.spec, data, tasks)
        return CommitResult(
            {
                "request": {"ref": p.ref, "state": state, "revision": int(rec["revision"])},
                "route": route,
                "process_instance": f"process_instance:{iid}",
                "suggested_next": [
                    {
                        "tool": "get_record",
                        "why": "Theo dõi trạng thái đề nghị.",
                        "arguments": {"ref": p.ref},
                    }
                ],
            },
            f"operation:{uuid.uuid4().hex}",
        )

    # ---- start_process ------------------------------------------------------------------------
    def _request_model_of(self, caller: Caller, process_id: str) -> str | None:
        return next(
            (m for m, b in self.rt.store.bindings(caller.actor).items()
             if b.primitive == "request" and b.config.get("process") == process_id),
            None,
        )  # fmt: skip

    def plan_start(self, ctx: CallContext, args: Args) -> MutationPlan:
        """Quy trình trigger `manual` không gắn model đề nghị (giao việc, tiếp nhận/nghỉ việc...).
        Người được đại diện là người khởi tạo (`requester`) -> L1 (TCV1 §3.5: khởi tạo việc của
        chính mình); trần tĩnh của contract vẫn là L3."""
        caller = self.rt.caller(ctx)
        pid = str(args["process_id"])
        pc = self.pc(caller)
        rt = pc.active(pid)
        if rt is None:
            raise not_found(f"process:{pid}")
        spec = rt.compiled.spec
        if spec.trigger.kind != "manual":
            raise invalid_request(
                f"Quy trình '{pid}' chạy định kỳ theo lịch; xt-worker tự khởi tạo mỗi kỳ.",
                "not_manual_trigger",
                remediation="Do not start it by hand; read its results with search_records.",
            )
        bound = self._request_model_of(caller, pid)
        if bound is not None:
            raise invalid_request(
                f"Quy trình '{pid}' khởi động khi gửi duyệt một bản ghi '{bound}'.",
                "wrong_tool",
                remediation=f"Create the {bound} record with create_record, then submit_request.",
                model_id=bound,
            )
        inputs: dict[str, Any] = {}
        for k, v in dict(args.get("parameters") or {}).items():
            ispec = spec.inputs.get(k)
            if ispec is not None and ispec.type == "user" and isinstance(v, str):
                try:
                    v = str(UUID(v))
                except ValueError:
                    v = str(parse_person_ref(caller, v))  # 'me' / person:<id>
            elif ispec is not None and ispec.type in ("int", "number"):
                v = _intish(v)
            inputs[k] = v
        subject: str | None = None
        if args.get("subject_ref"):
            model, rid = parse_record_ref(ctx, str(args["subject_ref"]))
            if model == "person":
                if self.rt.store.person(caller.actor, rid) is None:
                    raise not_found(str(args["subject_ref"]))
            else:  # RLS + IAM của chính người gọi: không thấy = không có (PSA-03)
                self._require_record(ctx, caller, model, f"{model}:{rid}", False)
            subject = f"{model}:{rid}"
            # Quy ước dữ liệu: input `<model>_id` kiểu str nhận ref của subject khi chưa truyền.
            key = f"{model}_id"
            hint = spec.inputs.get(key)
            if hint is not None and hint.type == "str" and key not in inputs:
                inputs[key] = subject
        engine_call(lambda: rt.validate_inputs(inputs))
        self._check_start_refs(ctx, caller, spec, inputs)
        requester = str(caller.person.id)
        if not rt.is_active(requester):
            raise access_denied("Người khởi tạo không còn hoạt động.", "inactive_requester", None)
        version = rt.compiled.version
        plan = StartPlan(ctx.actor.key, pid, version, inputs, requester, subject)
        return MutationPlan(
            RiskLevel.L1,
            f"Khởi động quy trình {pid} v{version}" + (f" cho {subject}" if subject else ""),
            {
                "process": pid,
                "process_version": version,
                "subject": subject,
                "inputs": jsonable(inputs),
            },
            target_ref=subject or f"process:{pid}",
            payload=plan,
        )

    def _start_targets(self, caller: Caller, spec: ProcessSpec) -> dict[str, tuple[str, bool]]:
        """input -> (model, quy trình ghi lên bản ghi?) từ `complete_via.match` của các bước:
        `id: <input>` = bản ghi chủ thể (quy trình ghi), `<field ref>: <input>` = bản ghi được
        tham chiếu (chỉ đọc)."""
        current = self.rt.catalogs.current(caller.actor)
        out: dict[str, tuple[str, bool]] = {}
        for st, _ in spec.iter_steps():
            via = getattr(st, "complete_via", None)
            if via is None:
                continue
            for f, expr in via.match.items():
                name = expr.strip()
                if name not in spec.inputs:
                    continue
                if f == "id":
                    out[name] = (via.model, True)
                    continue
                try:
                    fld = current[1].model(via.model).by_name().get(f) if current else None
                except KeyError:
                    fld = None
                if fld is not None and fld.type == "ref" and name not in out:
                    out[name] = (str(fld.ref), False)
        return out

    def _require_record(
        self, ctx: CallContext, caller: Caller, model: str, value: Any, write: bool
    ) -> None:
        """Người khởi tạo phải đọc được (RLS + IAM `record.read`) — và nếu quy trình ghi lên bản
        ghi thì có `record.update` — đúng bản ghi đó, đúng model, cùng tenant. Mọi từ chối là
        `not_found` (không phân biệt với id không tồn tại) — PSA-03."""
        raw = str(value)
        head, sep, tail = raw.rpartition(":")
        if sep and head.split(":")[-1] != model:
            raise not_found(raw)
        try:
            rid = UUID(tail if sep else raw)
        except ValueError:
            raise not_found(raw) from None
        if sep and head.startswith("xrn:") and head.split(":")[1] != ctx.tenant:
            raise not_found(raw)
        if not self._readable(caller, model, rid):
            raise not_found(raw)
        if self.access is not None:
            ref = f"{model}:{rid}"
            actions = ("record.read", "record.update") if write else ("record.read",)
            for action in actions:
                if not self.access.person_allowed(ctx, caller.person, action, ref):
                    raise not_found(raw)

    def _check_start_refs(
        self, ctx: CallContext, caller: Caller, spec: ProcessSpec, inputs: Mapping[str, Any]
    ) -> None:
        targets = self._start_targets(caller, spec)
        current = self.rt.catalogs.current(caller.actor)
        names = {m.name for m in current[1].models} if current is not None else set()
        for k, v in inputs.items():
            if k in targets:
                model, write = targets[k]
            elif isinstance(v, str) and ":" in v and v.rsplit(":", 2)[-2] in names:
                model, write = v.rsplit(":", 2)[-2], False  # mọi ref bản ghi khác: phải đọc được
            else:
                continue
            if v is None or v == "":
                continue
            self._require_record(ctx, caller, model, v, write)

    def _commit_start(self, ctx: CallContext, caller: Caller, p: StartPlan) -> CommitResult:
        svc = self.pc(caller).service()
        iid = str(uuid.uuid4())
        summary = engine_call(
            lambda: svc.start(
                p.process_id, p.inputs, p.requester, instance_id=iid, version=p.version
            )
        )
        if p.subject_ref:
            # Subject đã gắn quy trình khác (vd hồ sơ cũ) thì giữ liên kết cũ.
            PgWorkflowStore(self.rt.uow).link(p.subject_ref, iid, if_absent=True)
        first = [
            {
                "label": str(t["name"]),
                "assignee": self._person(caller, t["candidates"][0])
                if t.get("candidates")
                else None,
            }
            for t in summary["tasks"]
        ]
        data: Data = {
            "process_instance": f"process_instance:{iid}",
            "state": str(summary["status"]),
            "first_steps": first,
        }
        if p.subject_ref:
            data["subject_ref"] = p.subject_ref
        return CommitResult(data, f"operation:{uuid.uuid4().hex}")

    # ---- decide_request -----------------------------------------------------------------------
    def plan_decide(self, ctx: CallContext, args: Args) -> MutationPlan:
        caller = self.rt.caller(ctx)
        kind, pid = parse_record_ref(ctx, args["ref"])
        if kind == MODEL_PROPOSAL:
            if self.model_changes is None:
                unavailable("model change approval")
            return self.model_changes.plan_decide(ctx, caller, pid, args)
        model, rid, _config, row = self._request(ctx, caller, args["ref"])
        revision = self._cas(row, args["expected_revision"])
        ref = f"{model}:{rid}"
        iid = PgWorkflowStore(self.rt.uow).instance_for(ref)
        if iid is None:
            raise precondition_failed("Đề nghị chưa được gửi duyệt.", "not_submitted")
        svc = self.pc(caller).service()
        me = str(caller.person.id)
        info = engine_call(lambda: svc.get(iid, me))
        tasks = [t for t in info["tasks"] if t["kind"] in ("decision", "task")]
        if not tasks:
            raise access_denied(
                "Bạn không phải người được giao bước đang chờ của đề nghị này.",
                "not_assigned_approver",
                None,
            )
        task = tasks[0]
        outcome = DECISIONS[str(args["decision"])]
        if outcome not in task["outcomes"]:
            raise invalid_request(
                f"Bước '{task['name']}' không nhận quyết định '{args['decision']}'.",
                "outcome_not_allowed",
                allowed=[k for k, v in DECISIONS.items() if v in task["outcomes"]],
            )
        delegate = None
        if args.get("delegate_to"):
            delegate = str(parse_person_ref(caller, str(args["delegate_to"])))
        if outcome == "delegate":
            self._check_delegate(ctx, caller, svc, iid, task, me, delegate, ref)
        if caller.person.subject is None:
            raise access_denied(
                "Người duyệt chưa liên kết tài khoản đăng nhập.", "invalid_approver", None
            )
        plan = DecidePlan(
            ctx.actor.key,
            ref,
            model,
            rid,
            revision,
            iid,
            str(task["task_id"]),
            str(task["step"]),
            str(task["name"]),
            outcome,
            str(args["decision"]),
            str(args["reason"]) if args.get("reason") else None,
            delegate,
            me,
        )
        confirmer = PersonRef(
            f"person:{caller.person.id}", caller.person.name, caller.person.subject
        )
        return MutationPlan(
            RiskLevel.HUMAN_ONLY,
            f"{caller.person.name} {args['decision']} bước '{task['name']}' của {ref}",
            {
                "ref": ref,
                "revision": revision,
                "instance": iid,
                "task": plan.task_id,
                "decision": plan.decision,
                "reason": plan.comment,
                "delegate_to": delegate,
                "requester": str(row.get("owner_id")),
            },
            target_ref=ref,
            confirmers=(confirmer,),
            payload=plan,
        )

    def _check_delegate(
        self,
        ctx: CallContext,
        caller: Caller,
        svc: EngineProcesses,
        iid: str,
        task: Mapping[str, Any],
        me: str,
        delegate: str | None,
        ref: str,
    ) -> None:
        """Đích ủy quyền phải hợp lệ TRƯỚC khi tạo PendingOperation (WA-02/WA-03): tồn tại và
        đang hoạt động trong tenant (RLS: người tenant khác = không tồn tại), khác người gọi và
        người đề nghị, không vướng SoD của bước, và qua PEP `request.decide` trên đúng đề nghị
        (nếu không, bước sẽ kẹt vì người nhận không quyết định được)."""
        if delegate is None:
            raise invalid_request(
                "Ủy quyền cần delegate_to (người nhận).",
                "invalid_delegate",
                remediation="Find the person with find_people, then send delegate_to=person:<id>.",
            )
        person = self.rt.store.person(caller.actor, UUID(delegate))
        if person is None or not person.active:
            raise invalid_request(
                "Người được ủy quyền không tồn tại hoặc không còn hoạt động trong tổ chức.",
                "invalid_delegate",
                remediation="Find an active colleague with find_people and use their person ref.",
            )
        engine_call(lambda: svc.check_delegate(iid, str(task["task_id"]), me, delegate))
        if self.access is not None and not self.access.person_allowed(
            ctx, person, "request.decide", ref
        ):
            raise access_denied(
                f"{person.name} không có quyền duyệt bước '{task['name']}' của đề nghị này;"
                " ủy quyền sẽ làm bước bị kẹt.",
                "delegate_not_permitted",
                None,
                remediation="Delegate to someone who may approve this step (same rules as the "
                "approver), or decide it yourself.",
            )

    def _commit_decide(self, ctx: CallContext, caller: Caller, p: DecidePlan) -> CommitResult:
        pc = self.pc(caller)
        svc = pc.service()
        _, _, config, row = self._request(ctx, caller, p.ref)
        self._cas(row, p.revision)
        owner = row.get("owner_id")
        try:  # W9: sổ quyết định + trigger SoD của DB (lớp dưới PEP/runtime), cùng transaction
            self.rt.store.record_decision(
                caller.actor,
                kind="request",
                ref=p.ref,
                step=p.step,
                decision=p.outcome,
                approver=UUID(p.person),
                requester=UUID(str(owner)) if owner else None,
                actor_kind=ctx.actor.kind.value,
                actor_client=ctx.actor.client_id,
                confirmed_by=ctx.confirmed_by,
                instance_id=p.instance_id,
                task_id=p.task_id,
            )
        except SodViolation as e:
            raise access_denied(
                "Quyết định vi phạm tách nhiệm vụ (người duyệt phải là người khác, tự xác nhận).",
                "sod",
                None,
            ) from e
        kw: dict[str, Any] = {}
        if p.delegate_to:
            kw["delegate_to"] = p.delegate_to
        try:
            summary = engine_call(
                lambda: svc.act(p.instance_id, p.task_id, p.person, p.outcome, p.comment, **kw)
            )
        except EngineError as e:
            self._incident(caller, p.instance_id, e)
            raise ToolError(
                ErrorCode.INTERNAL_ERROR, "Quy trình gặp sự cố; quản trị đã được ghi nhận."
            ) from e
        tasks = list(summary["tasks"])
        state = self._state_of(summary, tasks)
        rec = self._write_state(
            caller, p.model, p.record_id, p.revision, state, str(config.get("state_field", "state"))
        )
        outcome = {
            "approve": "request_approved" if state == "approved" else "step_completed",
            "reject": "request_rejected",
            "return_for_revision": "returned",
            "delegate": "delegated",
        }[p.outcome]
        nxt = next((t for t in tasks if t["kind"] in ("decision", "task")), None)
        next_step = None
        if nxt is not None:
            approver = self._person(caller, nxt["candidates"][0]) if nxt.get("candidates") else None
            next_step = {"label": nxt["name"], "approver": approver}
        now = iso(ctx.now)
        return CommitResult(
            {
                "request": {"ref": p.ref, "state": state, "revision": int(rec["revision"])},
                "decision": {
                    "ref": f"decision:{p.instance_id}.{p.task_id}"[:200],
                    "decision": p.decision,
                    "step": p.step,
                    "decided_by": {"ref": f"person:{caller.person.id}", "name": caller.person.name},
                    "confirmed_via": "desk",
                    "decided_at": now,
                },
                "outcome": outcome,
                "next_step": next_step,
            },
            f"operation:{uuid.uuid4().hex}",
        )

    def _incident(self, caller: Caller, instance_id: str, e: EngineError) -> None:
        actor = caller.workflow()
        payload = {"instance": instance_id, "error": str(e)[:500], "reason": e.reason}

        def write() -> None:
            self.rt.store.process_event(actor, instance_id, "engine.error", payload)

        after_rollback(write)

    # ---- transition_record: hoàn thành bước người (complete_via) ------------------------------
    def open_steps(
        self, caller: Caller, pc: ProcessContext, model: str, rid: UUID, row: Mapping[str, Any]
    ) -> list[OpenStep]:
        """Task người đang mở mà bản ghi `model:rid` là chủ thể. Instance ứng viên: instance gắn
        với chính bản ghi hoặc bản ghi nó tham chiếu (vd lifecycle_task -> lifecycle_case), và
        instance đang có việc của người gọi (instance không gắn subject)."""
        store = PgWorkflowStore(self.rt.uow)
        refs = [f"{model}:{rid}"]
        current = self.rt.catalogs.current(caller.actor)
        if current is not None:
            try:
                fields = current[1].model(model).fields
            except KeyError:
                fields = []
            refs += [
                f"{f.ref}:{_norm_ref(row[f.name])}"
                for f in fields
                if f.type == "ref" and row.get(f.name)
            ]
        ids: list[str] = []
        for r in refs:
            found = store.instance_for(r)
            if found and found not in ids:
                ids.append(found)
        ids += [i for i in store.open_for(str(caller.person.id)) if i not in ids]
        me_row = {**row, "id": str(rid)}
        out: list[OpenStep] = []
        for iid in ids:
            rec = store.get_for_update(iid)
            if rec is None or rec.status != "running":
                continue
            rt = pc.get(rec.process_id, rec.version)
            if rt is None:
                continue
            try:
                inst = rt.load(rec.state)
            except Exception:  # một instance hỏng không chặn các instance khác
                continue
            data = inst.data()
            for t in inst.inbox(None):
                if t["kind"] not in ("decision", "task"):
                    continue
                st = human_step(rt.compiled.spec, str(t["step"]))
                via = st.complete_via if st is not None else None
                if st is None or via is None or via.model != model:
                    continue
                if _matches(via, data, me_row):
                    out.append(OpenStep(iid, t, st, via))
        return out

    def available_steps(
        self, caller: Caller, model: str, rid: UUID, row: Mapping[str, Any]
    ) -> list[Data]:
        """`available_transitions` của get_record: transition hoàn thành bước người gọi giữ."""
        me = str(caller.person.id)
        out: list[Data] = []
        for s in self.open_steps(caller, self.pc(caller), model, rid, row):
            if me not in s.task.get("candidates", ()):
                continue
            for to_state, outcome in s.via.states.items():
                out.append(
                    {
                        "to_state": to_state,
                        "label": f"{s.task['name']} ({outcome})",
                        "level": s.level.value,
                    }
                )
        return out

    def _readable(self, caller: Caller, model: str, rid: UUID) -> bool:
        try:
            self.rt.records.get(caller.actor, model, rid)
        except Exception:
            return False
        return True

    def _step_model(self, caller: Caller, model: str) -> bool:
        """Có quy trình đang kích hoạt nào khai bước hoàn thành qua model này không."""
        for d in self.rt.store.definitions(caller.actor, active_only=True):
            try:
                spec = ProcessSpec.model_validate(d.spec)
            except ValueError:
                continue
            for st, _ in spec.iter_steps():
                via = getattr(st, "complete_via", None)
                if via is not None and via.model == model:
                    return True
        return False

    def _plan_step(self, ctx: CallContext, caller: Caller, args: Args) -> MutationPlan:
        model, rid = parse_record_ref(ctx, args["ref"])
        current = self.rt.catalogs.current(caller.actor)
        try:
            spec_m = current[1].model(model) if current is not None else None
        except KeyError:
            spec_m = None
        if spec_m is None:
            raise not_found(str(args["ref"]))
        wf = caller.workflow()
        row = dict(engine_call(lambda: self.rt.records.get(wf, model, rid))["record"])
        ref = f"{model}:{rid}"
        to_state = str(args["to_state"])
        me = str(caller.person.id)
        pc = self.pc(caller)
        steps = self.open_steps(caller, pc, model, rid, row)
        mine = [s for s in steps if me in s.task.get("candidates", ())]
        hit = next((s for s in mine if to_state in s.via.states), None)
        if hit is None:
            if not mine and not self._readable(caller, model, rid):
                raise not_found(ref)  # không đọc được, không giữ bước: như không tồn tại
            if mine:
                raise precondition_failed(
                    f"Bước '{mine[0].task['name']}' không hoàn thành bằng trạng thái '{to_state}'.",
                    "transition_not_allowed",
                    remediation="Use one of available_transitions as to_state.",
                    available_transitions=sorted({x for m in mine for x in m.via.states}),
                )
            if steps:
                names = [
                    p["name"]
                    for uid in steps[0].task.get("candidates", ())
                    if (p := self._person(caller, uid))
                ]
                raise access_denied(
                    f"Bước '{steps[0].task['name']}' đang giao cho "
                    f"{', '.join(names) or 'người khác'}; chỉ người được giao mới hoàn thành được.",
                    "not_assigned",
                    None,
                    remediation="Tell the user who holds this step; do not retry for them.",
                )
            if self._step_model(caller, model):
                raise precondition_failed(
                    "Bản ghi không có bước quy trình nào đang chờ hoàn thành.",
                    "no_open_step",
                    remediation="Check get_record / get_inbox: the process may be finished or not"
                    " started yet (start_process).",
                )
            unavailable("state transitions for ordinary models")
        revision = self._cas(row, args["expected_revision"])
        task_id = str(hit.task["task_id"])
        engine_call(lambda: pc.service().check_act(hit.instance_id, task_id, me))
        outcome = hit.via.states[to_state]
        comment = str(args["reason"]) if args.get("reason") else None
        if outcome == "return_for_revision" and not comment:
            raise invalid_request(
                "Trả lại để làm lại cần lý do.",
                "reason_required",
                remediation="Ask the user what must be redone, then send it as reason.",
            )
        values: dict[str, Any] = dict(args.get("values") or {})
        sf = hit.via.state_field
        if sf in values:
            raise invalid_request(
                f"Trạng thái đích gửi bằng to_state, không đặt '{sf}' trong values.",
                "state_in_values",
            )
        unknown = sorted(set(values) - set(spec_m.by_name()))
        if unknown:
            raise invalid_request(
                "Unknown fields.",
                "unknown_field",
                fields=unknown,
                valid_fields=sorted(spec_m.by_name()),
            )
        allowed = writable_fields(hit.via, spec_m)
        blocked = sorted(set(values) - allowed)
        if blocked:
            # PSA-01: chỉ trường bước khai `writable_fields` (đã duyệt cùng quy trình) mới ghi
            # được kèm hoàn thành bước; khóa match, trạng thái, ref/user, managed_by: không bao giờ.
            raise invalid_request(
                f"Bước '{hit.task['name']}' không cho ghi các trường {blocked}.",
                "field_not_writable",
                remediation="Send only writable_fields of this step in values (or none).",
                fields=blocked,
                writable_fields=sorted(allowed),
            )
        if self.record_view is not None:
            values = dict(self.record_view.values_in(caller, ctx, spec_m, values))
        confirmers: tuple[PersonRef, ...] = ()
        if hit.human_only:
            if caller.person.subject is None:
                raise access_denied(
                    "Người được giao bước chưa liên kết tài khoản đăng nhập.",
                    "invalid_approver",
                    None,
                )
            confirmers = (
                PersonRef(f"person:{caller.person.id}", caller.person.name, caller.person.subject),
            )
        name = str(hit.task["name"])
        plan = StepPlan(
            ctx.actor.key,
            ref,
            model,
            rid,
            revision,
            hit.instance_id,
            task_id,
            str(hit.task["step"]),
            name,
            outcome,
            to_state,
            sf,
            values,
            comment,
            me,
        )
        return MutationPlan(
            hit.level,
            f"{caller.person.name} hoàn thành bước '{name}' ({to_state}) của {ref}",
            {
                "ref": ref,
                "revision": revision,
                "instance": hit.instance_id,
                "task": task_id,
                "to_state": to_state,
                "outcome": outcome,
                "values": jsonable(values),
                "reason": comment,
            },
            target_ref=ref,
            confirmers=confirmers,
            payload=plan,
        )

    def _record_out(self, caller: Caller, model: str, row: Mapping[str, Any]) -> Data:
        current = self.rt.catalogs.current(caller.actor)
        if self.record_view is not None and current is not None:
            spec_m = current[1].model(model)
            out: Data = self.record_view.record_out(caller, spec_m, row, current[0])
            return out
        return {
            "ref": f"{model}:{row['id']}",
            "model_id": model,
            "title": f"{model}:{row['id']}",
            "state": row.get("state"),
            "revision": int(row["revision"]),
            "model_revision": self._model_revision(caller),
            "values": {},
            "available_transitions": [],
        }

    def _commit_step(self, ctx: CallContext, caller: Caller, p: StepPlan) -> CommitResult:
        pc = self.pc(caller)
        svc = pc.service()
        wf = caller.workflow()
        row = dict(engine_call(lambda: self.rt.records.get(wf, p.model, p.record_id))["record"])
        self._cas(row, p.revision)
        try:
            summary = engine_call(
                lambda: svc.act(p.instance_id, p.task_id, p.person, p.outcome, p.comment)
            )
        except EngineError as e:
            self._incident(caller, p.instance_id, e)
            raise ToolError(
                ErrorCode.INTERNAL_ERROR, "Quy trình gặp sự cố; quản trị đã được ghi nhận."
            ) from e
        # Bước dịch vụ phía sau (vd close_task) có thể đã ghi bản ghi: đọc lại rồi mới ghi phần
        # còn thiếu (trạng thái đích + values), một lần, bằng vai trò xt_workflow.
        after = dict(engine_call(lambda: self.rt.records.get(wf, p.model, p.record_id))["record"])
        patch = dict(p.values)
        if after.get(p.state_field) != p.to_state:
            patch[p.state_field] = p.to_state
        if patch:
            rev = int(after["revision"])
            after = dict(
                engine_call(
                    lambda: self.rt.records.workflow_write(wf, p.model, p.record_id, rev, patch)
                )["record"]
            )
        rec = self._record_out(caller, p.model, after)
        rec["available_transitions"] = self.available_steps(caller, p.model, p.record_id, after)
        data: Data = {
            "record": rec,
            "from_state": str(row.get(p.state_field) or ""),
            "to_state": p.to_state,
            "started_process": None,
        }
        nxt = next((t for t in summary["tasks"] if t["kind"] in ("decision", "task")), None)
        if nxt is not None:
            who = self._person(caller, nxt["candidates"][0]) if nxt.get("candidates") else None
            data["suggested_next"] = [
                {
                    "tool": "get_inbox",
                    "why": f"Bước kế tiếp '{nxt['name']}'"
                    + (f" do {who['name']} làm." if who else "."),
                }
            ]
        return CommitResult(data, f"operation:{uuid.uuid4().hex}")

    # ---- transition_record: `withdrawn` của request (TCV1 §3.10) ------------------------------
    def plan_transition(self, ctx: CallContext, args: Args) -> MutationPlan:
        caller = self.rt.caller(ctx)
        model, _ = parse_record_ref(ctx, args["ref"])
        binding = self.rt.store.bindings(caller.actor).get(model)
        if binding is None or binding.primitive != "request":
            return self._plan_step(ctx, caller, args)
        if args.get("to_state") != "withdrawn":
            raise precondition_failed(
                "Trạng thái đề nghị do quy trình điều khiển; chỉ rút được (to_state='withdrawn').",
                "transition_not_allowed",
                details={"available_transitions": ["withdrawn"]},
            )
        model, rid, config, row = self._request(ctx, caller, args["ref"])
        revision = self._cas(row, args["expected_revision"])
        if str(row.get("owner_id")) != str(caller.person.id):
            raise access_denied("Chỉ người đề nghị được rút đề nghị.", "not_owner", None)
        state = row.get(str(config.get("state_field", "state")))
        ref = f"{model}:{rid}"
        iid, task_id = None, None
        if state == "returned":
            found = PgWorkflowStore(self.rt.uow).instance_for(ref)
            iid = found
            if found is not None:
                info = engine_call(lambda: self.pc(caller).service().get(found))
                t = next((t for t in info["tasks"] if t["kind"] == "revise"), None)
                task_id = str(t["task_id"]) if t else None
        elif state == "in_review":
            # Đang chờ duyệt (kể cả bước bị kẹt): người đề nghị rút, engine hủy instance.
            iid = PgWorkflowStore(self.rt.uow).instance_for(ref)
            if iid is None:
                raise precondition_failed("Đề nghị chưa có quy trình đang chạy.", "invalid_state")
        elif state != "draft":
            raise precondition_failed(
                f"Không rút được đề nghị ở trạng thái '{state}'.", "invalid_state"
            )
        reason = str(args["reason"]) if args.get("reason") else None
        plan = WithdrawPlan(ctx.actor.key, ref, model, rid, revision, iid, task_id, reason)
        return MutationPlan(
            RiskLevel.L1,
            f"Rút đề nghị {ref}",
            {"ref": ref, "revision": revision, "to_state": "withdrawn"},
            target_ref=ref,
            payload=plan,
        )

    def _commit_withdraw(self, ctx: CallContext, caller: Caller, p: WithdrawPlan) -> CommitResult:
        _, _, config, row = self._request(ctx, caller, p.ref)
        self._cas(row, p.revision)
        if p.instance_id and p.task_id:
            svc = self.pc(caller).service()
            iid, tid = p.instance_id, p.task_id
            engine_call(lambda: svc.act(iid, tid, str(caller.person.id), "withdraw", "Rút đề nghị"))
        elif p.instance_id:
            svc = self.pc(caller).service()
            iid, why = p.instance_id, p.reason or "Rút đề nghị"
            engine_call(lambda: svc.withdraw(iid, str(caller.person.id), why))
        rec = self._write_state(
            caller,
            p.model,
            p.record_id,
            p.revision,
            "withdrawn",
            str(config.get("state_field", "state")),
        )
        return CommitResult(
            {
                "record": {
                    "ref": p.ref,
                    "model_id": p.model,
                    "title": p.ref,
                    "state": "withdrawn",
                    "revision": int(rec["revision"]),
                    # Hợp đồng RECORD_FULL (thiếu hai trường này -> output contract violation).
                    "model_revision": self._model_revision(caller),
                    "values": {},
                    "available_transitions": [],
                },
                "from_state": row.get(str(config.get("state_field", "state"))),
                "to_state": "withdrawn",
            },
            f"operation:{uuid.uuid4().hex}",
        )

    def _model_revision(self, caller: Caller) -> int:
        current = self.rt.catalogs.current(caller.actor)
        return int(current[0]) if current is not None else 0

    # ---- commit chung -------------------------------------------------------------------------
    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        p = plan.payload
        caller = self.rt.caller(ctx)
        if (
            not isinstance(
                p, SubmitPlan | DecidePlan | WithdrawPlan | StartPlan | StepPlan | ModelDecidePlan
            )
            or p.actor_key != ctx.actor.key
        ):
            raise invalid_request("Unknown process mutation plan.", "invalid_plan")
        if isinstance(p, ModelDecidePlan):
            assert self.model_changes is not None
            return self.model_changes.commit(ctx, caller, p)
        if isinstance(p, SubmitPlan):
            return self._commit_submit(ctx, caller, p)
        if isinstance(p, DecidePlan):
            return self._commit_decide(ctx, caller, p)
        if isinstance(p, StartPlan):
            return self._commit_start(ctx, caller, p)
        if isinstance(p, StepPlan):
            return self._commit_step(ctx, caller, p)
        return self._commit_withdraw(ctx, caller, p)

    # ---- get_inbox ----------------------------------------------------------------------------
    def get_inbox(self, ctx: CallContext, args: Args) -> Data:
        caller = self.rt.caller(ctx)
        me = str(caller.person.id)
        pc = self.pc(caller)
        svc = pc.service()
        store = PgWorkflowStore(self.rt.uow)
        kinds = set(args.get("kinds") or ("task", "approval", "notification", "mention"))
        due_before = args.get("due_before")
        now = ctx.now
        items: list[Data] = []
        counts = {"task": 0, "approval": 0, "notification": 0, "overdue": 0}
        for t in engine_call(lambda: svc.inbox(me)):
            kind = "approval" if t["kind"] == "decision" else "task"
            iid = str(t["instance"])
            ref = store.ref_for(iid) or f"process_instance:{iid}"
            rec = store.get_for_update(iid)
            due = rec.next_due if rec else None
            title, state, revision, owner = t["name"], "in_review", None, None
            bound = self._inbox_bound(pc, iid, t)
            if bound is not None:
                via, row, label = bound
                ref = f"{via.model}:{row['id']}"
                state = str(row.get(via.state_field) or state)
                revision = int(row["revision"])
                if self._readable(caller, via.model, UUID(str(row["id"]))):
                    owner = row.get("owner_id")
                    title = f"{t['name']}: {label}"
                else:  # không lộ nội dung bản ghi người xem không đọc được (PSA-03)
                    title = f"{t['name']}: {ref[: len(via.model) + 9]}"
            elif ":" in ref and not ref.startswith("process_instance:"):
                model, rid = ref.split(":", 1)
                try:
                    out = self.rt.records.get(caller.workflow(), model, UUID(rid))["record"]
                    state = str(out.get("state") or state)
                    revision = int(out["revision"])
                    owner = out.get("owner_id")
                    title = f"{t['name']}: {model}:{rid[:8]}"
                except Exception:
                    pass
            sla = "none"
            if due is not None:
                sla = "overdue" if due <= now else "on_track"
            if sla == "overdue":
                counts["overdue"] += 1
            if due_before and (due is None or iso(due) >= str(due_before)):
                continue
            counts[kind] += 1
            if kind not in kinds:
                continue
            item: Data = {
                "ref": ref,
                "kind": kind,
                "title": title,
                "state": state,
                "from": self._person(caller, owner) if owner else None,
                "due_at": iso(due) if due else None,
                "sla_status": sla,
                "suggested_tool": "decide_request" if kind == "approval" else "update_record",
                "step": t["step"],
            }
            if bound is not None:
                item["suggested_tool"] = "transition_record"
                item["to_states"] = list(bound[0].states)
            if revision is not None:
                item["revision"] = revision
            items.append(item)
        if "notification" in kinds:
            for n in self.rt.store.notifications(
                caller.actor, caller.person.id, open_only=args.get("status", "open") == "open"
            ):
                counts["notification"] += 1
                items.append(
                    {
                        "ref": f"notification:{n['id']}",
                        "kind": "notification",
                        "title": n["title"],
                        "state": "read" if n["read_at"] else "unread",
                        "from": None,
                        "due_at": None,
                        "sla_status": "none",
                        "received_at": iso(n["created_at"]),
                    }
                )
        limit = int(args.get("limit", 20))
        page = signed_page(self.cursor_key, ctx, args)
        off = page.offset()
        window = items[off : off + limit]
        nxt = page.next(off + limit) if off + limit < len(items) else None
        return {"counts": counts, "items": window, "next_cursor": nxt}

    def _inbox_bound(
        self, pc: ProcessContext, iid: str, t: Mapping[str, Any]
    ) -> tuple[CompleteVia, dict[str, Any], str] | None:
        """(complete_via, bản ghi chủ thể, nhãn) của task inbox, hoặc None."""
        if t.get("kind") not in ("decision", "task"):
            return None
        try:
            rt, data = self._instance_data(pc, iid)
        except EngineError:
            return None
        st = human_step(rt.compiled.spec, str(t["step"]))
        if st is None or st.complete_via is None:
            return None
        row = bound_record(pc, st.complete_via, data)
        if row is None:
            return None
        label = f"{st.complete_via.model}:{str(row['id'])[:8]}"
        current = self.rt.catalogs.current(pc.actor)
        if current is not None:
            try:
                tf = current[1].model(st.complete_via.model).title_field
            except KeyError:
                tf = None
            if tf and row.get(tf):
                label = str(row[tf])
        return st.complete_via, row, label


def today_in(tz: str) -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(tz)).date()
