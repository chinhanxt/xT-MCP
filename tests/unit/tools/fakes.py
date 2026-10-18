# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bản giả in-memory cho mọi port của `xt.application.tools` (chỉ dùng trong test).

Mô phỏng đủ hành vi để chạy mọi nhánh lỗi: field lạ, rule, CAS, state machine, SoD, người được
giao bước, field `impact: high`, làn PII theo lớp client. Không import adapter thật.
"""

from __future__ import annotations

import copy
import itertools
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from xt.application.tools import (
    Actor,
    CallContext,
    ClientClass,
    PersonRef,
    ToolDispatcher,
    ToolServices,
    ToolSettings,
)
from xt.application.tools.errors import (
    invalid_request,
    not_found,
    precondition_failed,
    revision_conflict,
    rule_violation,
)
from xt.application.tools.pii_scrub import ErrorScrubber
from xt.application.tools.ports import (
    IN_PROGRESS,
    AccessCheck,
    AccessDecision,
    Args,
    ClaimBusy,
    CommitResult,
    Data,
    MutationPlan,
    OperationKey,
    PendingOperation,
    PendingStatus,
    PiiProjection,
    Receipt,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import InvalidTokenError, PrincipalKind

T = TypeVar("T")
NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
TENANT = "acme"

PEOPLE: dict[str, PersonRef] = {
    "sub-viet": PersonRef("person:viet", "Trần Văn Việt", "sub-viet"),
    "sub-mai": PersonRef("person:mai", "Nguyễn Thị Mai", "sub-mai"),
    "sub-long": PersonRef("person:long", "Đỗ Long", "sub-long"),
    "sub-binh": PersonRef("person:binh", "Hoàng Bình", "sub-binh"),
    "sub-tuan": PersonRef("person:tuan", "Phạm Tuấn", "sub-tuan"),
}
BY_REF = {p.ref: p for p in PEOPLE.values()}
MANAGER = {"person:tuan": "person:viet", "person:binh": "person:mai", "person:viet": "person:mai"}

MODELS: dict[str, dict[str, Any]] = {
    "advance_request": {
        "id": "advance_request",
        "label": "Đề nghị tạm ứng",
        "primitive": "request",
        "revision": 3,
        "title_field": "purpose",
        "fields": [
            {"id": "amount", "label": "Số tiền", "type": "money", "required": True},
            {"id": "purpose", "label": "Mục đích", "type": "string", "required": True},
            {"id": "trip_start", "label": "Ngày đi", "type": "date"},
            {"id": "trip_end", "label": "Ngày về", "type": "date"},
            {
                "id": "national_id",
                "label": "CCCD",
                "type": "string",
                "pii": True,
                "sensitive": True,
            },
        ],
        "states": [
            {"id": "draft", "label": "Nháp", "initial": True},
            {"id": "in_review", "label": "Đang duyệt"},
            {"id": "approved", "label": "Đã duyệt", "terminal": True},
            {"id": "withdrawn", "label": "Đã rút", "terminal": True},
        ],
        "transitions": [
            {"from": ["draft", "in_review"], "to": "withdrawn", "label": "Rút", "level": "L1"}
        ],
        "process": "advance_approval",
    },
    "task": {
        "id": "task",
        "label": "Công việc",
        "primitive": "task",
        "revision": 1,
        "fields": [
            {"id": "title", "label": "Tiêu đề", "type": "string", "required": True},
            {"id": "assignee", "label": "Người phụ trách", "type": "person", "impact": "high"},
            {"id": "result_note", "label": "Kết quả", "type": "text"},
        ],
        "states": [
            {"id": "todo", "label": "Cần làm", "initial": True},
            {"id": "in_progress", "label": "Đang làm"},
            {"id": "done", "label": "Xong", "terminal": True},
        ],
        "transitions": [
            {"from": ["todo"], "to": "in_progress", "label": "Bắt đầu", "level": "L1"},
            {"from": ["in_progress"], "to": "done", "label": "Xong", "level": "L1"},
        ],
    },
    "employee": {
        "id": "employee",
        "label": "Nhân sự",
        "primitive": "record",
        "revision": 1,
        "fields": [{"id": "full_name", "label": "Họ tên", "type": "string", "required": True}],
        "states": [
            {"id": "active", "label": "Đang làm", "initial": True},
            {"id": "offboarding", "label": "Nghỉ việc"},
        ],
        "transitions": [
            {"from": ["active"], "to": "offboarding", "label": "Cho nghỉ", "level": "L3"}
        ],
    },
}
CREATE_LEVEL = {"advance_request": RiskLevel.L1, "employee": RiskLevel.L2}


def _transitions(model_id: str, state: str) -> list[dict[str, Any]]:
    return [
        {"to_state": t["to"], "label": t["label"], "level": t["level"]}
        for t in MODELS[model_id]["transitions"]
        if state in t["from"]
    ]


@dataclass
class Rec:
    ref: str
    model_id: str
    title: str
    state: str
    revision: int
    values: dict[str, Any]
    owner_subject: str

    def full(self) -> Data:
        return {
            "ref": self.ref,
            "model_id": self.model_id,
            "title": self.title,
            "state": self.state,
            "revision": self.revision,
            "model_revision": MODELS[self.model_id]["revision"],
            "values": copy.deepcopy(self.values),
            "authored_by_kind": "agent",
            "available_transitions": _transitions(self.model_id, self.state),
        }

    def summary(self) -> Data:
        d = self.full()
        return {k: d[k] for k in ("ref", "model_id", "title", "state", "revision", "values")}


class SeqIds:
    def __init__(self) -> None:
        self._n = itertools.count(1)

    def new_id(self, prefix: str) -> str:
        return f"{prefix}_{next(self._n):04d}"


class Store:
    """Dữ liệu dùng chung giữa các port giả."""

    def __init__(self, ids: SeqIds) -> None:
        self.ids = ids
        self.records: dict[str, Rec] = {}
        self.commits: list[str] = []
        self.add(
            Rec(
                "advance_request:tu-0042",
                "advance_request",
                "Công tác Đà Nẵng",
                "in_review",
                2,
                {"amount": "8000000", "purpose": "Công tác Đà Nẵng", "national_id": "079123456789"},
                "sub-viet",
            )
        )
        self.add(
            Rec(
                "advance_request:tu-0050",
                "advance_request",
                "Hội thảo",
                "draft",
                1,
                {"amount": "2000000", "purpose": "Hội thảo"},
                "sub-viet",
            )
        )
        self.add(
            Rec(
                "task:t1",
                "task",
                "Báo cáo tuần",
                "in_progress",
                5,
                {"title": "Báo cáo tuần", "assignee": "person:tuan"},
                "sub-viet",
            )
        )
        self.add(
            Rec(
                "employee:e1",
                "employee",
                "Phạm Tuấn",
                "active",
                1,
                {"full_name": "Phạm Tuấn"},
                "sub-mai",
            )
        )

    def add(self, r: Rec) -> None:
        self.records[r.ref] = r

    def get(self, ref: str) -> Rec:
        r = self.records.get(ref)
        if r is None:
            raise not_found(ref)
        return r


def _person_of(ctx: CallContext) -> PersonRef:
    sub = ctx.actor.represented_subject or ctx.actor.subject
    return PEOPLE.get(sub, PersonRef(f"person:{sub}", sub, sub))


def _cas(r: Rec, expected: int | None) -> None:
    if expected is not None and expected != r.revision:
        raise revision_conflict(r.revision, expected)


# ---- port giả ------------------------------------------------------------------------------


@dataclass
class FakeAccess:
    ceiling: RiskLevel = RiskLevel.L1
    deny_tools: dict[str, str] = field(default_factory=dict)
    deny_actions: dict[str, str] = field(default_factory=dict)
    checks: list[AccessCheck] = field(default_factory=list)
    store: Store | None = None

    def check(self, ctx: CallContext, check: AccessCheck) -> AccessDecision:
        self.checks.append(check)
        if check.tool in self.deny_tools:
            return AccessDecision(
                False, self.deny_tools[check.tool], "Policy denies it.", "explain:deny-0001"
            )
        if check.action in self.deny_actions:
            return AccessDecision(
                False, self.deny_actions[check.action], "Policy denies it.", "explain:deny-0002"
            )
        if check.tool == "decide_request" and self.store and check.resource:
            r = self.store.records.get(check.resource)
            if r and r.owner_subject == ctx.actor.represented_subject:
                return AccessDecision(
                    False,
                    "sod",
                    "The requester cannot decide their own request.",
                    "explain:sod-0001",
                )
        return AccessDecision(True)

    def autonomy_ceiling(self, ctx: CallContext) -> RiskLevel:
        return self.ceiling


class FakeWorkspace:
    def inspect(self, ctx: CallContext, args: Args) -> Data:
        p = _person_of(ctx)
        return {
            "me": {
                "ref": p.ref,
                "name": p.name,
                "kind": "agent" if ctx.actor.is_agent else "human",
                "autonomy_ceiling": "L1",
                "client_class": ctx.client_class.value,
            },
            "models": [
                {"id": m["id"], "label": m["label"], "primitive": m["primitive"]}
                for m in MODELS.values()
            ],
        }


class FakeModels:
    def describe_model(self, ctx: CallContext, args: Args) -> Data | None:
        m = MODELS.get(args["model_id"])
        return {"model": copy.deepcopy(m)} if m else None

    def model_primitive(self, ctx: CallContext, model_id: str) -> str | None:
        m = MODELS.get(model_id)
        return m["primitive"] if m else None


class FakeDirectory:
    def find_people(self, ctx: CallContext, args: Args) -> Data:
        if "direct_reports_of" in args:
            anchor = next((p for p in PEOPLE.values() if args["direct_reports_of"] in p.name), None)
            if anchor is None:
                raise not_found()
            people = [BY_REF[r] for r, m in MANAGER.items() if m == anchor.ref]
            return {
                "relation": "direct_reports_of",
                "anchor": anchor.named(),
                "summary": f"{len(people)} người báo cáo trực tiếp cho {anchor.name}.",
                "people": [p.named() for p in people],
                "next_cursor": None,
                "total": len(people),
            }
        q = str(args.get("query", "")).lower()
        people = [p for p in PEOPLE.values() if q in p.name.lower()]
        return {
            "relation": "search",
            "anchor": None,
            "summary": f"{len(people)} kết quả.",
            "people": [p.named() for p in people],
            "next_cursor": None,
            "total": len(people),
        }

    def person_for_subject(self, ctx: CallContext, subject: str) -> PersonRef | None:
        return PEOPLE.get(subject)


@dataclass
class Plan:
    kind: str
    ref: str | None = None
    model_id: str | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    to_state: str | None = None


class FakeRecords:
    def __init__(self, store: Store) -> None:
        self.s = store
        self.bad_output = False
        self.explode = False

    def search(self, ctx: CallContext, args: Args) -> Data:
        if self.explode:
            raise RuntimeError("db down: password=hunter2")
        items = [
            r.summary()
            for r in self.s.records.values()
            if r.model_id == args.get("model_id", r.model_id)
        ]
        limit = int(args.get("limit", 20))
        if self.bad_output:
            return {"items": "not-a-list", "next_cursor": None}
        return {"items": items[:limit], "next_cursor": None, "total": len(items)}

    def aggregate(self, ctx: CallContext, args: Args) -> Data:
        rows = [r for r in self.s.records.values() if r.model_id == args["model_id"]]
        return {
            "model_id": args["model_id"],
            "as_of": ctx.now_iso,
            "scope": "authorized_records",
            "rows": [],
            "totals": {"count": len(rows)},
            "summary": f"{len(rows)} bản ghi.",
            "lineage_ref": "explain:agg-0001",
            "next_cursor": None,
        }

    def get(self, ctx: CallContext, args: Args) -> Data | None:
        r = self.s.records.get(args["ref"])
        return {"record": r.full()} if r else None

    def _check_values(self, model_id: str, values: Mapping[str, Any]) -> None:
        known = [f["id"] for f in MODELS[model_id]["fields"]]
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise invalid_request(
                f"Unknown field(s): {', '.join(unknown)}.",
                "unknown_field",
                fields=unknown,
                valid_fields=known,
            )

    def plan_create(self, ctx: CallContext, args: Args) -> MutationPlan:
        model_id, values = args["model_id"], dict(args["values"])
        self._check_values(model_id, values)
        amount = values.get("amount")
        if isinstance(amount, str) and amount.isdigit() and int(amount) > 30_000_000:
            raise rule_violation(
                [
                    {
                        "field": "amount",
                        "rule": "policy:advance_limit@1",
                        "message": "Tối đa 30.000.000 mỗi lần",
                    }
                ],
                "explain:rv-0001",
            )
        level = CREATE_LEVEL.get(model_id, RiskLevel.L1)
        return MutationPlan(
            level,
            f"Tạo {MODELS[model_id]['label']}",
            {"values": values},
            payload=Plan("create", model_id=model_id, changes=values),
        )

    def plan_update(self, ctx: CallContext, args: Args) -> MutationPlan:
        r = self.s.get(args["ref"])
        _cas(r, args["expected_revision"])
        changes = dict(args["set"])
        self._check_values(r.model_id, changes)
        high = {f["id"] for f in MODELS[r.model_id]["fields"] if f.get("impact") == "high"}
        level = RiskLevel.L2 if high & set(changes) else RiskLevel.L1
        return MutationPlan(
            level,
            f"Sửa {r.ref}: {sorted(changes)}",
            {"revision": r.revision, "set": changes},
            target_ref=r.ref,
            payload=Plan("update", ref=r.ref, changes=changes),
        )

    def plan_transition(self, ctx: CallContext, args: Args) -> MutationPlan:
        r = self.s.get(args["ref"])
        _cas(r, args["expected_revision"])
        t = next(
            (
                t
                for t in MODELS[r.model_id]["transitions"]
                if r.state in t["from"] and t["to"] == args["to_state"]
            ),
            None,
        )
        if t is None:
            raise precondition_failed(
                f"Cannot go from {r.state} to {args['to_state']}.",
                "transition_not_allowed",
                current_state=r.state,
            )
        return MutationPlan(
            RiskLevel(t["level"]),
            f"{t['label']} {r.ref}",
            {"revision": r.revision, "from": r.state, "to": t["to"]},
            target_ref=r.ref,
            payload=Plan(
                "transition", ref=r.ref, to_state=t["to"], changes=dict(args.get("values") or {})
            ),
        )

    def plan_create_task(self, ctx: CallContext, args: Args) -> MutationPlan:
        assignee = args["assignee"]
        if assignee != "me" and assignee not in BY_REF:
            raise not_found(assignee)
        me = _person_of(ctx).ref
        direct = assignee == "me" or MANAGER.get(assignee) == me
        level = RiskLevel.L1 if direct else RiskLevel.L2
        return MutationPlan(
            level,
            f"Giao việc '{args['title']}' cho {assignee}",
            {"title": args["title"], "assignee": assignee},
            payload=Plan("task", changes={"title": args["title"], "assignee": assignee}),
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        p: Plan = plan.payload
        self.s.commits.append(p.kind)
        if p.kind == "create":
            assert p.model_id
            ref = f"{p.model_id}:{self.s.ids.new_id('r')}"
            m = MODELS[p.model_id]
            initial = next(s["id"] for s in m["states"] if s.get("initial"))
            title = str(p.changes.get(m.get("title_field", ""), p.model_id))
            r = Rec(
                ref, p.model_id, title, initial, 1, p.changes, ctx.actor.represented_subject or ""
            )
            self.s.add(r)
            data: Data = {"record": r.full()}
            if m["primitive"] == "request":
                data["suggested_next"] = [
                    {"tool": "submit_request", "why": "Gửi đi duyệt", "arguments": {"ref": ref}}
                ]
        elif p.kind == "update":
            assert p.ref
            r = self.s.get(p.ref)
            r.values.update(p.changes)
            r.revision += 1
            data = {"record": r.full(), "changed_fields": sorted(p.changes)}
        elif p.kind == "transition":
            assert p.ref and p.to_state
            r = self.s.get(p.ref)
            before = r.state
            r.state = p.to_state
            r.values.update(p.changes)
            r.revision += 1
            data = {
                "record": r.full(),
                "from_state": before,
                "to_state": r.state,
                "started_process": None,
            }
        else:
            ref = f"task:{self.s.ids.new_id('t')}"
            who = p.changes["assignee"]
            person = _person_of(ctx) if who == "me" else BY_REF[who]
            r = Rec(
                ref,
                "task",
                p.changes["title"],
                "todo",
                1,
                {"title": p.changes["title"], "assignee": person.ref},
                "",
            )
            self.s.add(r)
            data = {
                "task": {
                    "ref": ref,
                    "title": r.title,
                    "state": "todo",
                    "revision": 1,
                    "assignee": person.named(),
                },
                "notified": [person.named()],
            }
        return CommitResult(data, self.s.ids.new_id("op"))


class FakeProcesses:
    def __init__(self, store: Store) -> None:
        self.s = store

    def describe_process(self, ctx: CallContext, args: Args) -> Data | None:
        if args["process_id"] != "advance_approval":
            return None
        return {
            "process": {
                "id": "advance_approval",
                "label": "Duyệt tạm ứng",
                "revision": 2,
                "trigger": {"kind": "on_submit", "model_id": "advance_request"},
                "steps": [
                    {
                        "id": "head",
                        "label": "Trưởng bộ phận duyệt",
                        "kind": "decision",
                        "applies": True,
                    }
                ],
            }
        }

    def plan_submit(self, ctx: CallContext, args: Args) -> MutationPlan:
        r = self.s.get(args["ref"])
        _cas(r, args.get("expected_revision"))
        if r.state != "draft":
            raise precondition_failed(
                "Only drafts can be submitted.", "not_draft", current_state=r.state
            )
        own = r.owner_subject == ctx.actor.represented_subject
        return MutationPlan(
            RiskLevel.L1 if own else RiskLevel.L2,
            f"Gửi {r.ref} đi duyệt",
            {"revision": r.revision},
            target_ref=r.ref,
            payload=Plan("submit", ref=r.ref),
        )

    def plan_decide(self, ctx: CallContext, args: Args) -> MutationPlan:
        r = self.s.get(args["ref"])
        _cas(r, args["expected_revision"])
        if r.state != "in_review":
            raise precondition_failed("No step is waiting for a decision.", "not_in_review")
        if args["decision"] == "delegate" and args.get("delegate_to") not in BY_REF:
            raise not_found(args.get("delegate_to"))
        return MutationPlan(
            RiskLevel.L3,
            f"{args['decision']} bước 'Trưởng bộ phận' cho {r.ref} ({r.values.get('amount')} VND)",
            {"revision": r.revision, "decision": args["decision"]},
            target_ref=r.ref,
            confirmers=(PEOPLE["sub-mai"],),
            payload=Plan("decide", ref=r.ref, changes={"decision": args["decision"]}),
        )

    def plan_start(self, ctx: CallContext, args: Args) -> MutationPlan:
        if args["process_id"] == "advance_approval":
            raise invalid_request("Started by submitting a request.", "wrong_tool")
        if args["process_id"] != "onboarding":
            raise not_found(f"process:{args['process_id']}")
        subject = args.get("subject_ref")
        if subject is not None and subject not in BY_REF:
            raise not_found(subject)
        return MutationPlan(
            RiskLevel.L1,
            f"Khởi động quy trình onboarding cho {subject}",
            {"process": "onboarding", "subject": subject, "inputs": args.get("parameters") or {}},
            target_ref=subject,
            payload=Plan("start", ref=subject),
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        p: Plan = plan.payload
        if p.kind == "start":
            self.s.commits.append(p.kind)
            return CommitResult(
                {
                    "process_instance": "process_instance:pi9",
                    "state": "running",
                    "first_steps": [
                        {"label": "Nhân sự chuẩn bị hồ sơ", "assignee": PEOPLE["sub-mai"].named()}
                    ],
                },
                self.s.ids.new_id("op"),
            )
        assert p.ref
        r = self.s.get(p.ref)
        r.revision += 1
        self.s.commits.append(p.kind)
        if p.kind == "submit":
            r.state = "in_review"
            return CommitResult(
                {
                    "request": {"ref": r.ref, "state": r.state, "revision": r.revision},
                    "route": [
                        {
                            "step": "head",
                            "label": "Trưởng bộ phận duyệt",
                            "approver": PEOPLE["sub-mai"].named(),
                            "due_at": "2026-11-16T09:00:00Z",
                        }
                    ],
                    "process_instance": "process_instance:pi1",
                },
                self.s.ids.new_id("op"),
            )
        decision = p.changes["decision"]
        r.state = {"approve": "approved", "reject": "rejected"}.get(decision, r.state)
        confirmer = BY_REF["person:mai"]
        return CommitResult(
            {
                "request": {"ref": r.ref, "state": r.state, "revision": r.revision},
                "decision": {
                    "ref": self.s.ids.new_id("decision"),
                    "decision": decision,
                    "step": "head",
                    "decided_by": confirmer.named(),
                    "confirmed_via": "desk",
                    "decided_at": ctx.now_iso,
                },
                "outcome": "request_approved" if decision == "approve" else "returned",
                "next_step": None,
            },
            self.s.ids.new_id("op"),
        )


class FakeInbox:
    def get_inbox(self, ctx: CallContext, args: Args) -> Data:
        return {
            "counts": {"approval": 1},
            "items": [
                {
                    "ref": "advance_request:tu-0042",
                    "kind": "approval",
                    "title": "Tạm ứng 8.000.000",
                    "state": "in_review",
                    "suggested_tool": "decide_request",
                }
            ],
            "next_cursor": None,
        }


class FakeHistory:
    def get_history(self, ctx: CallContext, args: Args) -> Data | None:
        if args["ref"].startswith("explain:"):
            return {
                "subject": {"ref": args["ref"]},
                "view": "explain",
                "explanation": {
                    "outcome": "denied",
                    "reasons": [{"source": "sod", "message": "SoD"}],
                },
                "next_cursor": None,
            }
        return None


class FakeFiles:
    def __init__(self, store: Store) -> None:
        self.s = store

    def plan_upload(self, ctx: CallContext, args: Args) -> MutationPlan:
        return MutationPlan(
            RiskLevel.L1, f"Tải tệp {args['name']}", {"name": args["name"]}, payload=dict(args)
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        a = plan.payload
        return CommitResult(
            {
                "file": {
                    "ref": f"file:{self.s.ids.new_id('f')}",
                    "name": a["name"],
                    "status": "not_scanned",
                }
            },
            self.s.ids.new_id("op"),
        )


class FakeModelChanges:
    def __init__(self, store: Store) -> None:
        self.s = store

    def preview(self, ctx: CallContext, args: Args) -> Data:
        return {
            "dry_run": True,
            "base_revision": 0,
            "classification": "additive",
            "diff": [{"op": o["op"], "path": f"/{args['model_id']}"} for o in args["operations"]],
            "findings": [],
            "affected_records": 0,
        }

    def plan_propose(self, ctx: CallContext, args: Args) -> MutationPlan:
        if args.get("base_revision", 0) != 0:
            raise revision_conflict(0, args["base_revision"])
        return MutationPlan(
            RiskLevel.L1,
            f"Đề xuất đổi model {args['model_id']}",
            {"ops": len(args["operations"])},
            payload=dict(args),
        )

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        return CommitResult(
            {
                "dry_run": False,
                "base_revision": 0,
                "classification": "additive",
                "diff": [],
                "findings": [],
                "affected_records": 0,
                "approvals_required": 1,
                "proposal": "proposal:p1",
                "request": "model_change_request:m1",
            },
            self.s.ids.new_id("op"),
        )


SENSITIVE = {"national_id"}


class FakePii:
    """`cloud` -> masked; `local`/`human` -> full (policy `llm_egress=masked`)."""

    def __init__(self) -> None:
        self.inbound_calls: list[str] = []
        self.scrubbed: list[str] = []

    def inbound(self, ctx: CallContext, tool: str, args: Args) -> Args:
        self.inbound_calls.append(tool)
        return args

    def outbound(self, ctx: CallContext, tool: str, data: Data) -> PiiProjection:
        if ctx.client_class is not ClientClass.CLOUD:
            return PiiProjection(data, "full")
        masked: list[str] = []

        def walk(x: Any) -> Any:
            if isinstance(x, dict):
                out = {}
                for k, v in x.items():
                    if k in SENSITIVE and isinstance(v, str):
                        out[k] = "[PII:k3f9a2]"
                        masked.append(k)
                    else:
                        out[k] = walk(v)
                return out
            if isinstance(x, list):
                return [walk(v) for v in x]
            return x

        return PiiProjection(walk(data), "masked", tuple(sorted(set(masked))))

    def scrub_error(self, ctx: CallContext, tool: str, error: Data) -> Data:
        """`ErrorScrubber` chung với field PII của MODELS; client không phải `cloud`: giữ nguyên."""
        self.scrubbed.append(tool)
        if ctx.client_class is not ClientClass.CLOUD:
            return error
        return ErrorScrubber(lambda v: "[PII:k3f9a2]", pii_fields=SENSITIVE).error(error)


CCCD = re.compile(r"(?<!\d)0\d{11}(?!\d)")  # bộ nhận dạng CCCD 12 số (spike-pii-lane §3.5)


class MemoryOperations:
    """Kho receipt/PendingOperation in-memory với ngữ nghĩa của bảng UNIQUE trong Postgres:
    `claim` của transaction B chờ transaction A (đang giữ key) commit/rollback; rollback hoàn tác
    mọi ghi của transaction đó (kể cả claim); `lock_pending` khóa hàng tới hết transaction."""

    def __init__(self, wait_seconds: float = 5.0) -> None:
        self.by_key: dict[OperationKey, Receipt | PendingOperation] = {}
        self.pending: dict[str, PendingOperation] = {}
        self.wait_seconds = wait_seconds
        self._cond = threading.Condition()
        self._holders: dict[Any, int] = {}  # key / ("cf", id) -> thread đang giữ
        self._local = threading.local()

    # ---- transaction (do MemoryTransactions gọi) ------------------------------------------
    def _journal(self) -> list[tuple[str, Any, Any]]:
        j: list[tuple[str, Any, Any]] | None = getattr(self._local, "journal", None)
        if j is None:
            raise AssertionError("ghi OperationStore ngoài transaction")
        return j

    def begin(self) -> None:
        self._local.journal = []

    def end(self, commit: bool) -> None:
        journal = self._local.journal
        self._local.journal = None
        with self._cond:
            if not commit:
                for table, k, old in reversed(journal):
                    store = self.by_key if table == "key" else self.pending
                    if old is _MISSING:
                        store.pop(k, None)
                    else:
                        store[k] = old
            me = threading.get_ident()
            for k in [k for k, v in self._holders.items() if v == me]:
                del self._holders[k]
            self._cond.notify_all()

    def _set(self, table: str, k: Any, v: Any) -> None:
        store: dict[Any, Any] = self.by_key if table == "key" else self.pending
        self._journal().append((table, k, store.get(k, _MISSING)))
        store[k] = v

    def _hold(self, lock: Any) -> bool:
        me = threading.get_ident()
        deadline = time.monotonic() + self.wait_seconds
        while self._holders.get(lock, me) != me:
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            self._cond.wait(left)
        self._holders[lock] = me
        return True

    # ---- OperationStore ---------------------------------------------------------------------
    def claim(
        self, key: OperationKey, fingerprint: str, now: datetime
    ) -> Receipt | PendingOperation | ClaimBusy | None:
        self._journal()
        with self._cond:
            if not self._hold(key):
                return IN_PROGRESS
            return self.by_key.get(key)

    def save_receipt(self, receipt: Receipt) -> None:
        with self._cond:
            assert self._holders.get(receipt.key) == threading.get_ident(), "chưa claim key"
            self._set("key", receipt.key, receipt)

    def save_pending(self, op: PendingOperation) -> None:
        with self._cond:
            assert self._holders.get(op.key) == threading.get_ident(), "chưa claim key"
            self._set("key", op.key, op)
            self._set("cf", op.confirmation_id, op)

    def update_pending(self, op: PendingOperation) -> bool:
        with self._cond:
            cur = self.pending.get(op.confirmation_id)
            receipt = self.by_key.get(op.key)
            if cur is None or not cur.status.open:
                return False
            if isinstance(receipt, Receipt) and op.status is not PendingStatus.CONFIRMED:
                return False
            self._set("cf", op.confirmation_id, op)
            if not isinstance(receipt, Receipt):
                self._set("key", op.key, op)
            return True

    def get_pending(self, confirmation_id: str) -> PendingOperation | None:
        return self.pending.get(confirmation_id)

    def lock_pending(self, confirmation_id: str) -> PendingOperation | None:
        self._journal()
        with self._cond:
            if not self._hold(("cf", confirmation_id)):
                raise TimeoutError("lock_timeout")
            op = self.pending.get(confirmation_id)
            if op is not None:
                self._hold(op.key)  # receipt sẽ thay hàng của key này
            return op

    def count_open_pending(self, tenant: str, actor: str, now: datetime) -> int:
        return sum(
            1
            for p in self.pending.values()
            if p.key.tenant == tenant
            and p.key.actor == actor
            and p.status.open
            and now < p.expires_at
        )


_MISSING = object()


class MemoryTransactions:
    """Commit/rollback thật cho `MemoryOperations` (mutation của port giả không hoàn tác)."""

    def __init__(self, ops: MemoryOperations) -> None:
        self.ops = ops
        self.runs = 0
        self.rollbacks = 0

    def run(self, fn: Callable[[], T]) -> T:
        self.runs += 1
        self.ops.begin()
        try:
            out = fn()
        except BaseException:
            self.rollbacks += 1
            self.ops.end(commit=False)
            raise
        self.ops.end(commit=True)
        return out


@dataclass
class World:
    services: ToolServices
    store: Store
    access: FakeAccess
    records: FakeRecords
    pii: FakePii
    operations: MemoryOperations
    transactions: MemoryTransactions


def make_world() -> World:
    ids = SeqIds()
    store = Store(ids)
    access = FakeAccess(store=store)
    records = FakeRecords(store)
    pii = FakePii()
    ops = MemoryOperations()
    tx = MemoryTransactions(ops)
    services = ToolServices(
        access=access,
        workspace=FakeWorkspace(),
        models=FakeModels(),
        directory=FakeDirectory(),
        records=records,
        processes=FakeProcesses(store),
        inbox=FakeInbox(),
        history=FakeHistory(),
        files=FakeFiles(store),
        model_changes=FakeModelChanges(store),
        pii=pii,
        operations=ops,
        transactions=tx,
        ids=ids,
    )
    return World(services, store, access, records, pii, ops, tx)


SETTINGS = ToolSettings(confirm_base_url="https://xt.example.test")


def make_dispatcher(world: World, **settings: Any) -> ToolDispatcher:
    return ToolDispatcher(world.services, replace(SETTINGS, **settings))


def agent_ctx(
    behalf: str | None = "sub-viet",
    *,
    client_class: ClientClass = ClientClass.CLOUD,
    now: datetime = NOW,
    revoked: bool = False,
    client_id: str = "claude-connector",
) -> CallContext:
    def ensure_active(level: RiskLevel) -> None:
        if revoked:
            raise InvalidTokenError("token_revoked")

    return CallContext(
        actor=Actor(
            TENANT, behalf or f"service-account-{client_id}", PrincipalKind.AGENT, client_id, behalf
        ),
        client_class=client_class,
        trace_id="tr_test",
        now=now,
        ensure_active=ensure_active,
    )


def human_ctx(subject: str = "sub-mai", now: datetime = NOW) -> CallContext:
    return CallContext(
        actor=Actor(TENANT, subject, PrincipalKind.HUMAN, "xt-desk", None),
        client_class=ClientClass.HUMAN,
        trace_id="tr_human",
        now=now,
    )


def later(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)


def services_factory(env: Mapping[str, str]) -> ToolServices:
    """Cho `XT_MCP_SERVICES=tests.unit.tools.fakes:services_factory` trong smoke test `xt serve`."""
    return make_world().services
