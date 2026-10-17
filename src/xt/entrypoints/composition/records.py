# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Port tool trên model engine (describe_model, search/get/create/update, create_task).

Plan chạy đúng validator của engine trên một repository dừng ngay trước lệnh ghi đầu tiên: không
bịa bản ghi, không ghi-rồi-rollback. Commit chạy lại engine trên repository thật (CAS, ràng buộc,
RLS, audit trigger của DB) trong transaction của lời gọi tool.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, ClassVar, NoReturn
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from xt.application.model_engine.ports import RecordRepository
from xt.application.model_engine.service import RecordService as EngineRecords
from xt.application.model_engine.service import jsonable
from xt.application.tools.context import CallContext
from xt.application.tools.errors import ToolError, invalid_request, not_found
from xt.application.tools.ports import Args, CommitResult, Data, MutationPlan
from xt.contracts.model import ProposeModelChange
from xt.contracts.tools import ErrorCode
from xt.domain.autonomy import RiskLevel
from xt.domain.model.context import ModelActor
from xt.domain.model.spec import ModelSpec
from xt.entrypoints.composition.model_changes import MODEL_PROPOSAL, ModelChangeDecisions
from xt.entrypoints.composition.runtime import Caller, Runtime
from xt.entrypoints.composition.util import (
    engine_call,
    parse_person_ref,
    parse_record_ref,
    signed_page,
    unavailable,
)

REF_TYPES = ("ref", "user", "file")


def _prefix(field: Any) -> str:
    return str(field.ref) if field.type == "ref" else ("person" if field.type == "user" else "file")


class ModelCatalogAdapter:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def describe_model(self, ctx: CallContext, args: Args) -> Data | None:
        caller = self.rt.caller(ctx)
        try:
            out: Data = engine_call(
                lambda: self.rt.models.describe_model(
                    caller.actor,
                    args["model_id"],
                    include_values_schema=bool(args.get("include_values_schema", False)),
                )
            )
        except ToolError as exc:
            if exc.code in (ErrorCode.NOT_FOUND, ErrorCode.ACCESS_DENIED):
                return None
            raise
        binding = self.rt.store.bindings(caller.actor).get(args["model_id"])
        if binding is not None:
            out["model"]["primitive"] = binding.primitive
            if binding.primitive == "request":
                out["model"]["process"] = binding.config.get("process")
        return out

    def model_primitive(self, ctx: CallContext, model_id: str) -> str | None:
        out = self.describe_model(ctx, {"model_id": model_id})
        return str(out["model"]["primitive"]) if out else None


class _ValidatedWrite(Exception):
    def __init__(self, values: Mapping[str, Any]) -> None:
        self.values = dict(values)


class _PlanningRepository:
    """Đọc như repository thật, dừng chính xác trước lệnh ghi đầu tiên của engine."""

    def __init__(self, real: RecordRepository) -> None:
        self.real = real

    def get(self, actor: ModelActor, model: ModelSpec, rid: UUID) -> dict[str, Any] | None:
        return self.real.get(actor, model, rid)

    def insert(self, actor: ModelActor, model: ModelSpec, values: Mapping[str, Any]) -> NoReturn:
        raise _ValidatedWrite(values)

    def update(
        self,
        actor: ModelActor,
        model: ModelSpec,
        rid: UUID,
        revision: int,
        values: Mapping[str, Any],
        *,
        workflow: bool = False,
    ) -> NoReturn:
        raise _ValidatedWrite(values)

    def delete(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("delete is not a v0.1 tool operation")

    def search(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise RuntimeError("search must use the real repository")


@dataclass(frozen=True)
class RecordMutation:
    actor_key: str
    tenant: str
    action: str  # create | update
    model: str
    catalog_revision: int
    values: dict[str, Any]
    record_id: UUID | None = None
    expected_revision: int | None = None
    task: bool = False


class RecordServiceAdapter:
    def __init__(
        self,
        rt: Runtime,
        *,
        cursor_key: bytes,
        transitions: Callable[[CallContext, Args], MutationPlan] | None = None,
        transition_commit: Callable[[CallContext, MutationPlan], CommitResult] | None = None,
        model_changes: ModelChangeDecisions | None = None,
        step_transitions: Callable[[CallContext, Caller, str, UUID, Mapping[str, Any]], list[Data]]
        | None = None,
    ) -> None:
        if len(cursor_key) < 32:
            raise ValueError("cursor signing key must contain at least 32 bytes")
        self.rt = rt
        self.cursor_key = cursor_key
        self.planner = EngineRecords(rt.catalogs, _PlanningRepository(rt.repo))
        self.transitions = transitions
        self.transition_commit = transition_commit
        self.model_changes = model_changes
        self.step_transitions = step_transitions

    # ---- tiện ích ----------------------------------------------------------------------------
    def _model(self, actor: ModelActor, name: str) -> tuple[int, ModelSpec]:
        current = self.rt.catalogs.current(actor)
        if current is None:
            raise not_found(f"model:{name}")
        try:
            return current[0], current[1].model(name)
        except KeyError as exc:
            raise not_found(f"model:{name}") from exc

    def values_in(self, caller: Caller, ctx: CallContext, model: ModelSpec, values: Args) -> Data:
        """Ref ở bề mặt tool (`model:id`, `person:id`, `me`) -> id lưu trong DB."""
        out = dict(values)
        for f in model.fields:
            v = out.get(f.name)
            if f.type not in REF_TYPES or not isinstance(v, str):
                continue
            if f.type == "user":
                out[f.name] = str(parse_person_ref(caller, v))
                continue
            if ":" in v:
                prefix, rid = parse_record_ref(ctx, v)
                if prefix != _prefix(f):
                    raise invalid_request(
                        f"Trường '{f.label}' cần ref tới '{_prefix(f)}'.",
                        "bad_reference",
                        field=f.name,
                    )
                out[f.name] = str(rid)
        return out

    def record_out(
        self,
        caller: Caller,
        model: ModelSpec,
        raw: Mapping[str, Any],
        version: int,
        fields: Any = None,
        people: dict[str, Any] | None = None,
    ) -> Data:
        people = {} if people is None else people

        def named(pid: Any) -> Any:
            if pid is None:
                return None
            key = str(pid)
            if key not in people:
                p = self.rt.store.person(caller.actor, UUID(key))
                people[key] = {"ref": f"person:{p.id}", "name": p.name} if p else None
            return people[key]

        values: Data = {}
        for f in model.fields:
            if fields is not None and f.name not in fields:
                continue
            v = jsonable(raw.get(f.name))
            if v is not None and f.type in REF_TYPES:
                v = f"{_prefix(f)}:{v}"
            values[f.name] = v
        title = str(raw.get(model.title_field) or "") if model.title_field else ""
        out: Data = {
            "ref": f"{model.name}:{raw['id']}",
            "model_id": model.name,
            "model_revision": version,
            "revision": int(raw["revision"]),
            "title": title or f"{model.label} {str(raw['id'])[:8]}",
            "state": raw.get("state") if "state" in model.by_name() else None,
            "values": values,
            "owner": named(raw.get("owner_id")),
            "available_transitions": [],
        }
        creator = named(raw.get("created_by"))
        if creator:
            out["created_by"] = {**creator, "kind": "human"}
        for key in ("created_at", "updated_at"):
            if raw.get(key) is not None:
                out[key] = jsonable(raw[key])
        return out

    @staticmethod
    def _check_fields(model: ModelSpec, args: Args) -> None:
        unknown = set(args.get("fields", ())) - set(model.by_name())
        if unknown:
            raise invalid_request(
                "Unknown fields.",
                "unknown_field",
                fields=sorted(unknown),
                valid_fields=sorted(model.by_name()),
            )

    def read_raw(self, caller: Caller, ctx: CallContext, ref: str) -> tuple[ModelSpec, int, Data]:
        name, rid = parse_record_ref(ctx, ref)
        ver, model = self._model(caller.actor, name)
        out = engine_call(lambda: self.rt.records.get(caller.actor, name, rid))
        return model, ver, out["record"]

    # ---- đọc -----------------------------------------------------------------------------------
    def get(self, ctx: CallContext, args: Args) -> Data | None:
        if args.get("include") or args.get("text_cursor"):
            unavailable("record expansions (include/text_cursor)")
        caller = self.rt.caller(ctx)
        name, rid = parse_record_ref(ctx, args["ref"])
        if name == MODEL_PROPOSAL and self.model_changes is not None:
            return self.model_changes.record(caller, rid)
        ver, model = self._model(caller.actor, name)
        self._check_fields(model, args)
        try:
            _, _, raw = self.read_raw(caller, ctx, args["ref"])
        except ToolError as exc:
            if exc.code in (ErrorCode.NOT_FOUND, ErrorCode.ACCESS_DENIED):
                return None
            raise
        rec = self.record_out(caller, model, raw, ver, args.get("fields"))
        if self.step_transitions is not None:
            # Bước quy trình người gọi đang giữ trên bản ghi (complete_via) -> transition_record.
            rec["available_transitions"] = self.step_transitions(ctx, caller, name, rid, raw)
        return {"record": rec}

    _OPS: ClassVar[dict[str, str]] = {
        "eq": "=",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "contains": "contains",
        "in": "in",
    }

    def _where(self, caller: Caller, ctx: CallContext, model: ModelSpec, args: Args) -> Data | None:
        clauses: list[Data] = []
        for field, raw in dict(args.get("where", {})).items():
            name = field
            value: Any = raw
            if field == "owner":
                name = "owner_id"
            ops = list(value.items()) if isinstance(value, Mapping) else [("eq", value)]
            for op, val in ops:
                if isinstance(val, str) and (field == "owner" or field in model.by_name()):
                    if field == "owner":
                        val = str(parse_person_ref(caller, val))
                    else:
                        val = self.values_in(caller, ctx, model, {field: val})[field]
                if op == "is_null" or val is None:
                    is_null = bool(val) if op == "is_null" else op == "eq"
                    clauses.append({"field": name, "op": "is_null" if is_null else "not_null"})
                elif op in self._OPS:
                    clauses.append({"field": name, "op": self._OPS[op], "value": val})
                else:
                    raise invalid_request(f"Toán tử lọc không hợp lệ: {op}", "validation")
        if args.get("query"):
            text = [f for f in model.fields if f.type == "text"]
            clauses.append(
                {"or": [{"field": f.name, "op": "contains", "value": args["query"]} for f in text]}
            )
        return {"and": clauses} if clauses else None

    def search(self, ctx: CallContext, args: Args) -> Data:
        if "workspace" in args:
            unavailable("workspace-scoped search")
        caller = self.rt.caller(ctx)
        current = self.rt.catalogs.current(caller.actor)
        if current is None:
            return {"items": [], "total": 0, "next_cursor": None}
        version, catalog = current
        if "model_id" in args:
            names = [str(args["model_id"])]
        else:
            names = sorted(m.name for m in catalog.models)
        limit = int(args.get("limit", 20))
        page = signed_page(self.cursor_key, ctx, args)
        offset = page.offset()
        skip, total = offset, 0
        items: list[Data] = []
        people: dict[str, Any] = {}
        for name in names:
            _, model = self._model(caller.actor, name)
            readable = any(
                r in model.permissions and "read" in model.permissions[r].actions
                for r in caller.actor.roles
            )
            if not readable:
                if "model_id" in args:
                    raise not_found(f"model:{name}")
                continue
            if args.get("query") and not any(f.type == "text" for f in model.fields):
                continue
            self._check_fields(model, args)
            where = self._where(caller, ctx, model, args)
            sort = tuple(args.get("sort", ("-created_at",)))
            start = min(skip, 10_000)
            res = self._search(caller.actor, name, where, sort, limit, start)
            total += int(res["total"])
            if skip >= res["total"]:
                skip -= int(res["total"])
                continue
            for row in res["items"][: max(0, limit - len(items))]:
                items.append(
                    self.record_out(caller, model, row, version, args.get("fields"), people)
                )
            skip = 0
        nxt = offset + len(items)
        return {
            "items": items,
            "total": total,
            "next_cursor": page.next(nxt) if nxt < total and nxt <= 10_000 else None,
        }

    def _search(
        self, actor: ModelActor, name: str, where: Data | None, sort: Any, limit: int, start: int
    ) -> Data:
        out: Data = engine_call(
            lambda: self.rt.records.search(actor, name, where, sort, limit, start)
        )
        return out

    def aggregate(self, ctx: CallContext, args: Args) -> Data:
        unavailable("aggregate_records")

    # ---- ghi -----------------------------------------------------------------------------------
    def _plan(
        self, ctx: CallContext, args: Args, action: str, *, task: bool = False
    ) -> MutationPlan:
        if "workspace" in args:
            unavailable("workspace-scoped writes")
        caller = self.rt.caller(ctx)
        if action == "create":
            name, rid = str(args["model_id"]), None
            raw_values = dict(args.get("values") or {})
        else:
            name, rid = parse_record_ref(ctx, args["ref"])
            raw_values = dict(args["set"])
        ver, model = self._model(caller.actor, name)
        values = self.values_in(caller, ctx, model, raw_values)
        try:
            if rid is None:
                engine_call(lambda: self.planner.create(caller.actor, name, values))
            else:
                engine_call(
                    lambda: self.planner.update(
                        caller.actor, name, rid, int(args["expected_revision"]), values
                    )
                )
        except _ValidatedWrite as ok:
            mutation = RecordMutation(
                ctx.actor.key,
                ctx.tenant,
                action,
                name,
                ver,
                values,
                rid,
                int(args["expected_revision"]) if rid else None,
                task,
            )
            label = "Tạo" if action == "create" else "Sửa"
            return MutationPlan(
                RiskLevel.L1,
                f"{label} {model.label}",
                {
                    "action": action,
                    "model": name,
                    "catalog_revision": ver,
                    "record": str(rid) if rid else None,
                    "expected_revision": mutation.expected_revision,
                    "values": jsonable(ok.values),
                },
                target_ref=f"{name}:{rid}" if rid else f"model:{name}",
                payload=mutation,
            )
        raise RuntimeError("model engine did not reach its write boundary")

    def plan_create(self, ctx: CallContext, args: Args) -> MutationPlan:
        return self._plan(ctx, args, "create")

    def plan_update(self, ctx: CallContext, args: Args) -> MutationPlan:
        return self._plan(ctx, args, "update")

    def plan_transition(self, ctx: CallContext, args: Args) -> MutationPlan:
        if self.transitions is None:
            unavailable("transition_record")
        return self.transitions(ctx, args)

    # ---- create_task: primitive `task` gắn qua `xt_meta.model_binding` ---------------------
    def plan_create_task(self, ctx: CallContext, args: Args) -> MutationPlan:
        caller = self.rt.caller(ctx)
        task_type = str(args.get("task_type", "task"))
        binding = self.rt.store.bindings(caller.actor).get(task_type)
        if binding is None or binding.primitive != "task":
            raise not_found(f"model:{task_type}")
        fmap: Mapping[str, str] = binding.config.get("fields", {})
        _, model = self._model(caller.actor, task_type)
        values: dict[str, Any] = dict(args.get("values") or {})
        for arg in ("title", "assignee", "description", "due_at", "priority", "parent_ref"):
            if arg in args:
                if arg not in fmap:
                    raise invalid_request(
                        f"Loại việc '{model.label}' không có trường cho '{arg}'.",
                        "unknown_field",
                        parameter=arg,
                    )
                values[fmap[arg]] = args[arg]
        for arg in ("depends_on", "recurrence", "watchers", "source_ref"):
            if arg in args:
                unavailable(f"create_task.{arg}")
        priority_field = fmap.get("priority")
        if priority_field and isinstance(values.get(priority_field), str):
            priorities = binding.config.get("priorities", {})
            values[priority_field] = priorities.get(values[priority_field], values[priority_field])
        due_field = fmap.get("due_at")
        if due_field and isinstance(values.get(due_field), str):
            values[due_field] = self._due(caller, model, due_field, values[due_field])
        assignee = parse_person_ref(caller, str(args["assignee"]))
        person = self.rt.store.person(caller.actor, assignee)
        if person is None or not person.active:
            raise not_found(str(args["assignee"]))
        plan = self._plan(ctx, {"model_id": task_type, "values": values}, "create", task=True)
        # Giao cho chính mình hoặc cấp dưới trực tiếp: L1; ngoài tuyến báo cáo: L2 (TCV1 §3.5).
        level = (
            RiskLevel.L1
            if assignee == caller.person.id or person.manager == caller.person.id
            else RiskLevel.L2
        )
        return MutationPlan(
            level,
            f"Giao việc '{args['title']}' cho {person.name}",
            {**plan.digest_source, "assignee": str(assignee)},
            target_ref=plan.target_ref,
            payload=plan.payload,
        )

    @staticmethod
    def _due(caller: Caller, model: ModelSpec, field: str, value: str) -> str:
        t = model.field(field).type
        try:
            if t == "datetime" and len(value) == 10:
                d = date.fromisoformat(value)
                end = datetime.combine(d, time(23, 59, 59), ZoneInfo(caller.profile.timezone))
                return end.isoformat()
            if t == "date" and len(value) > 10:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
        except ValueError as exc:
            raise invalid_request("Ngày hết hạn không hợp lệ.", "bad_date") from exc
        return value

    # ---- commit -------------------------------------------------------------------------------
    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        payload = plan.payload
        if not isinstance(payload, RecordMutation):
            if self.transition_commit is None:
                raise invalid_request("Unknown record mutation plan.", "invalid_plan")
            return self.transition_commit(ctx, plan)
        if (payload.actor_key, payload.tenant) != (ctx.actor.key, ctx.tenant):
            raise not_found(plan.target_ref)
        caller = self.rt.caller(ctx)
        ver, model = self._model(caller.actor, payload.model)
        if ver != payload.catalog_revision:
            raise ToolError(
                ErrorCode.REVISION_CONFLICT,
                "Model đã đổi phiên bản sau khi chuẩn bị thao tác; hãy đọc lại rồi làm lại.",
                details={"current_revision": ver},
            )
        if payload.record_id is None:
            out = engine_call(
                lambda: self.rt.records.create(caller.actor, payload.model, payload.values)
            )
        else:
            rid, rev = payload.record_id, int(payload.expected_revision or 0)
            out = engine_call(
                lambda: self.rt.records.update(
                    caller.actor, payload.model, rid, rev, payload.values
                )
            )
        rec = self.record_out(caller, model, out["record"], ver)
        op_ref = f"operation:{uuid4().hex}"
        if payload.task:
            return CommitResult({"task": self._task_out(caller, plan, rec), "notified": []}, op_ref)
        data: Data = {"record": rec}
        if payload.action == "update":
            data["changed_fields"] = sorted(payload.values)
        return CommitResult(data, op_ref)

    def _task_out(self, caller: Caller, plan: MutationPlan, rec: Data) -> Data:
        binding = self.rt.store.bindings(caller.actor)[rec["model_id"]]
        fmap: Mapping[str, str] = binding.config.get("fields", {})
        priorities = {v: k for k, v in binding.config.get("priorities", {}).items()}
        values = rec["values"]
        assignee = values.get(fmap.get("assignee", ""))
        person = None
        if isinstance(assignee, str) and assignee.startswith("person:"):
            p = self.rt.store.person(caller.actor, UUID(assignee.split(":", 1)[1]))
            person = {"ref": f"person:{p.id}", "name": p.name} if p else None
        return {
            "ref": rec["ref"],
            "title": rec["title"],
            "state": rec["state"] or "",
            "revision": rec["revision"],
            "assignee": person or {"ref": str(assignee), "name": ""},
            "due_at": values.get(fmap.get("due_at", "")),
            "priority": str(
                priorities.get(
                    values.get(fmap.get("priority", "")),
                    values.get(fmap.get("priority", "")) or "normal",
                )
            ),
            "parent_ref": values.get(fmap.get("parent_ref", "")),
            "sla_policy": None,
            "recurrence": None,
        }


@dataclass(frozen=True)
class _ProposalMutation:
    actor_key: str
    tenant: str
    args: Data
    plan_hash: str


class ModelChangeServiceAdapter:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    def _proposer(self, ctx: CallContext) -> str:
        caller = self.rt.caller(ctx)
        return (
            f"agent:{caller.client_id}/person:{caller.person.id}"
            if caller.is_agent
            else (f"person:{caller.person.id}")
        )

    def preview(self, ctx: CallContext, args: Args) -> Data:
        caller = self.rt.caller(ctx)
        req = ProposeModelChange.model_validate({**args, "dry_run": True})
        out: Data = engine_call(
            lambda: self.rt.models.propose(caller.actor, req, proposed_by=self._proposer(ctx))
        )
        return out

    def plan_propose(self, ctx: CallContext, args: Args) -> MutationPlan:
        out = self.preview(ctx, args)
        return MutationPlan(
            RiskLevel.L1,
            f"Đề xuất thay đổi model {args['model_id']}",
            {"plan_hash": out["plan_hash"], "model_id": args["model_id"]},
            target_ref=f"model:{args['model_id']}",
            payload=_ProposalMutation(
                ctx.actor.key, ctx.tenant, jsonable(dict(args)), out["plan_hash"]
            ),
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        payload = plan.payload
        if not isinstance(payload, _ProposalMutation):
            raise invalid_request("Unknown model proposal plan.", "invalid_plan")
        if (payload.actor_key, payload.tenant) != (ctx.actor.key, ctx.tenant):
            raise not_found(plan.target_ref)
        caller = self.rt.caller(ctx)
        req = ProposeModelChange.model_validate({**payload.args, "dry_run": False})
        result: Data = engine_call(
            lambda: self.rt.models.propose(caller.actor, req, proposed_by=self._proposer(ctx))
        )
        if result.get("plan_hash") not in (None, payload.plan_hash):
            raise ToolError(ErrorCode.REVISION_CONFLICT, "Kế hoạch thay đổi model đã đổi.")
        if result.get("request") is None and result.get("proposal"):
            # Request quản trị = chính đề xuất: theo dõi bằng get_record, duyệt bằng decide_request.
            result["request"] = result["proposal"]
        return CommitResult(result, f"operation:{uuid4().hex}")
