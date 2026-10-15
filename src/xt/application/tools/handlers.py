# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Một handler cho mỗi tool của catalog v0.1. Handler chỉ biết port; pipeline chung (IAM trước,
idempotency, CAS qua plan, tự chủ -> PendingOperation) nằm ở `dispatcher.py`.

Kiểm chéo field mà schema không diễn đạt (C3: không combinator ở root) làm ở `precheck`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from xt.application.tools.context import CallContext
from xt.application.tools.errors import (
    continuation,
    invalid_request,
    not_found,
    precondition_failed,
)
from xt.application.tools.ports import (
    AccessCheck,
    AccessGuard,
    Args,
    CommitResult,
    Data,
    Directory,
    FileService,
    HistoryService,
    IdGenerator,
    InboxService,
    ModelCatalog,
    ModelChangeService,
    MutationPlan,
    OperationStore,
    PiiLane,
    ProcessService,
    RecordService,
    TransactionRunner,
    WorkspaceService,
)
from xt.contracts.tools import BY_NAME, ToolContract

_NOT_ATTRIBUTES = frozenset({"idempotency_key", "cursor", "text_cursor"})


@dataclass(frozen=True, slots=True)
class ToolServices:
    """Bộ port mà dispatcher cần. Composition root (entrypoint) dựng từ adapter thật."""

    access: AccessGuard
    workspace: WorkspaceService
    models: ModelCatalog
    directory: Directory
    records: RecordService
    processes: ProcessService
    inbox: InboxService
    history: HistoryService
    files: FileService
    model_changes: ModelChangeService
    pii: PiiLane
    operations: OperationStore
    transactions: TransactionRunner
    ids: IdGenerator


class Handler:
    name: ClassVar[str]

    @property
    def contract(self) -> ToolContract:
        return BY_NAME[self.name]

    def access(self, s: ToolServices, ctx: CallContext, args: Args) -> AccessCheck:
        """IAM: action đầu tiên của contract, tài nguyên = ref nếu có, thuộc tính = args."""
        attrs = {k: v for k, v in args.items() if k not in _NOT_ATTRIBUTES}
        ref = args.get("ref")
        return AccessCheck(
            self.name, self.contract.iam[0], ref if isinstance(ref, str) else None, attrs
        )

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        return None


class ReadHandler(Handler):
    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        raise NotImplementedError


class WriteHandler(Handler):
    def preview(self, s: ToolServices, ctx: CallContext, args: Args) -> Data | None:
        """Không None = lời gọi chỉ xem trước (dry run): không receipt, không ghi."""
        return None

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        raise NotImplementedError

    def commit(self, s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        raise NotImplementedError


def _found(data: Data | None, ref: Any = None) -> Data:
    if data is None:
        raise not_found(ref if isinstance(ref, str) else None)
    return data


# ---- đọc (L0) --------------------------------------------------------------------------------


class InspectWorkspace(ReadHandler):
    name = "inspect_workspace"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return s.workspace.inspect(ctx, args)


class DescribeModel(ReadHandler):
    name = "describe_model"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return _found(s.models.describe_model(ctx, args), f"model:{args['model_id']}")


class DescribeProcess(ReadHandler):
    name = "describe_process"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return _found(s.processes.describe_process(ctx, args), f"process:{args['process_id']}")


RELATIONS = ("direct_reports_of", "manager_of", "org_unit", "role")


class FindPeople(ReadHandler):
    name = "find_people"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        given = [r for r in RELATIONS if r in args]
        if len(given) > 1:
            raise invalid_request(
                "Use only one of direct_reports_of, manager_of, org_unit, role per call.",
                "conflicting_parameters",
                remediation="Call find_people once per relation.",
                parameters=given,
            )
        if not given and "query" not in args:
            raise invalid_request(
                "Give a query or one relation (direct_reports_of, manager_of, org_unit, role).",
                "missing_parameter",
            )

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return s.directory.find_people(ctx, args)


class SearchRecords(ReadHandler):
    name = "search_records"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        if "model_id" not in args and not args.get("query"):
            raise invalid_request(
                "Without model_id a full-text query is required.",
                "missing_parameter",
                remediation="Pass model_id to filter one model, or query to search all models.",
                parameter="query",
            )

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return s.records.search(ctx, args)


class AggregateRecords(ReadHandler):
    name = "aggregate_records"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        if "metric" in args and "metrics" in args:
            raise invalid_request(
                "Use either a named metric or ad-hoc metrics, not both.",
                "conflicting_parameters",
                parameters=["metric", "metrics"],
            )

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return s.records.aggregate(ctx, args)


class GetRecord(ReadHandler):
    name = "get_record"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return _found(s.records.get(ctx, args), args["ref"])


class GetInbox(ReadHandler):
    name = "get_inbox"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return s.inbox.get_inbox(ctx, args)


class GetHistory(ReadHandler):
    name = "get_history"

    def read(self, s: ToolServices, ctx: CallContext, args: Args) -> Data:
        return _found(s.history.get_history(ctx, args), args["ref"])


# ---- ghi --------------------------------------------------------------------------------------


class _RecordWrite(WriteHandler):
    def commit(self, s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        return s.records.commit(ctx, plan)


class CreateRecord(_RecordWrite):
    name = "create_record"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        primitive = s.models.model_primitive(ctx, args["model_id"])
        if primitive is None:
            raise not_found(f"model:{args['model_id']}")
        if primitive == "task":
            values: Mapping[str, Any] = args.get("values") or {}
            cont: dict[str, Any] = {
                k: values[k]
                for k in ("title", "assignee", "due_at")
                if isinstance(values.get(k), str)
            }
            if args["model_id"] != "task":
                cont["task_type"] = args["model_id"]
            cont["idempotency_key"] = args["idempotency_key"]
            raise invalid_request(
                "Tasks are created with create_task.",
                "wrong_tool",
                remediation="Call the continuation instead (fill title/assignee if missing).",
                continuation=continuation("create_task", cont),
            )

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.records.plan_create(ctx, args)


class UpdateRecord(_RecordWrite):
    name = "update_record"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        changes: Mapping[str, Any] = args["set"]
        if "state" in changes:
            to_state = changes["state"]
            cont: dict[str, Any] = {
                "ref": args["ref"],
                "expected_revision": args["expected_revision"],
                "idempotency_key": args["idempotency_key"],
            }
            if isinstance(to_state, str):
                cont["to_state"] = to_state
            raise precondition_failed(
                "State changes only through transition_record.",
                "state_field",
                continuation=continuation("transition_record", cont),
                remediation="Call transition_record with the target state (see "
                "available_transitions in get_record).",
            )

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.records.plan_update(ctx, args)


class TransitionRecord(_RecordWrite):
    name = "transition_record"

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.records.plan_transition(ctx, args)


class UploadFile(WriteHandler):
    name = "upload_file"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        source = args["source"]
        for fieldname in ("text", "base64"):
            if fieldname in args and source != fieldname:
                raise invalid_request(
                    f"{fieldname} is only allowed when source={fieldname}.",
                    "conflicting_parameters",
                    parameters=["source", fieldname],
                )
        if source in ("text", "base64") and source not in args:
            raise invalid_request(
                f"source={source} needs the {source} parameter.",
                "missing_parameter",
                parameter=source,
            )

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.files.plan_upload(ctx, args)

    def commit(self, s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        return s.files.commit(ctx, plan)


class _ProcessWrite(WriteHandler):
    def commit(self, s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        return s.processes.commit(ctx, plan)


class SubmitRequest(_ProcessWrite):
    name = "submit_request"

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.processes.plan_submit(ctx, args)


class DecideRequest(_ProcessWrite):
    name = "decide_request"

    def precheck(self, s: ToolServices, ctx: CallContext, args: Args) -> None:
        decision = args["decision"]
        if decision in ("reject", "return") and not str(args.get("reason", "")).strip():
            raise invalid_request(
                f"A reason is required to {decision} a request.",
                "missing_parameter",
                remediation="Ask the user why, then call again with reason.",
                parameter="reason",
            )
        if decision == "delegate" and "delegate_to" not in args:
            raise invalid_request(
                "delegate_to is required when decision=delegate.",
                "missing_parameter",
                remediation="Find the person with find_people, then pass delegate_to.",
                parameter="delegate_to",
            )
        if decision != "delegate" and "delegate_to" in args:
            raise invalid_request(
                "delegate_to is only allowed when decision=delegate.",
                "conflicting_parameters",
                parameters=["decision", "delegate_to"],
            )

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.processes.plan_decide(ctx, args)


class CreateTask(_RecordWrite):
    name = "create_task"

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.records.plan_create_task(ctx, args)


class ProposeModelChange(WriteHandler):
    name = "propose_model_change"

    def preview(self, s: ToolServices, ctx: CallContext, args: Args) -> Data | None:
        if args.get("dry_run", True):
            return s.model_changes.preview(ctx, args)
        return None

    def plan(self, s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
        return s.model_changes.plan_propose(ctx, args)

    def commit(self, s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        return s.model_changes.commit(ctx, plan)


HANDLERS: Mapping[str, Handler] = {
    h.name: h
    for h in (
        InspectWorkspace(),
        DescribeModel(),
        DescribeProcess(),
        FindPeople(),
        SearchRecords(),
        AggregateRecords(),
        GetRecord(),
        GetInbox(),
        GetHistory(),
        CreateRecord(),
        UpdateRecord(),
        TransitionRecord(),
        UploadFile(),
        SubmitRequest(),
        DecideRequest(),
        CreateTask(),
        ProposeModelChange(),
    )
}
