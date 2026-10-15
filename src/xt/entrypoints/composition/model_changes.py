# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Duyệt đề xuất thay đổi model (`model_proposal:<id>`) bằng `decide_request` (gap-review #3).

Luồng: `propose_model_change(dry_run=false)` lưu `xt_meta.model_proposal` (bất biến, L1) ->
`decide_request(ref="model_proposal:<id>")` (human-only: `/desk/confirm` + step-up) -> commit ghi
`xt_meta.model_change_request` (outbox) cùng transaction -> `xt worker`: relay -> job
`model.apply` = `ModelService.approve_and_apply` (pha 1) + `PgMigrator.run_jobs` (pha 2).

- Revalidate lúc plan VÀ lúc xác nhận: kế hoạch tính lại trên catalog hiện hành phải trùng
  `plan_hash` đã lưu, không thì `revision_conflict` (đề xuất cũ phải soạn lại).
- Tách nhiệm vụ: người được đại diện trong `proposed_by` không được duyệt.
- PHÁ HỦY: hai người duyệt khác nhau (`awaiting_second_approver`): người thứ hai là quản lý trực
  tiếp nếu policy cho phép `request.decide` trên đề xuất, nếu không thì người đủ quyền đầu tiên
  theo mã nhân sự (docs/decisions.md D-INT-6).
- Trạng thái: `get_record("model_proposal:<id>")`: pending_approval | approved (chờ worker) |
  applied | failed | rejected.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from xt.adapters.db.integration import SodViolation
from xt.application.model_engine.ports import StoredProposal
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
from xt.domain.model.context import TenantRef
from xt.domain.model.plan import RiskClass
from xt.entrypoints.composition.runtime import Caller, Runtime
from xt.entrypoints.composition.security import ToolAccessGuard

MODEL_PROPOSAL = "model_proposal"
_PERSON = re.compile(r"person:([0-9a-fA-F-]{36})")
STEP = "model_change"


def proposer_person(proposed_by: str) -> UUID | None:
    """`agent:<azp>/person:<id>` hoặc `person:<id>` -> id người thật đứng sau đề xuất."""
    m = _PERSON.search(proposed_by)
    return UUID(m.group(1)) if m else None


@dataclass(frozen=True)
class ModelDecidePlan:
    actor_key: str
    ref: str
    proposal_id: UUID
    plan_hash: str
    decision: str  # approve | reject
    destructive: tuple[str, ...]
    reason: str | None
    decided_by: tuple[str, ...]  # person:<id> của những người phải xác nhận


class ModelChangeDecisions:
    def __init__(self, rt: Runtime, guard: ToolAccessGuard) -> None:
        self.rt, self.guard = rt, guard

    # ---- tiện ích -----------------------------------------------------------------------------
    def _proposal(self, tenant: TenantRef, pid: UUID) -> StoredProposal:
        p = self.rt.models.proposals.get(tenant, pid)
        if p is None:
            raise not_found(f"{MODEL_PROPOSAL}:{pid}")
        return p

    @staticmethod
    def _revision(row: Mapping[str, Any] | None) -> int:
        if row is None:
            return 1
        return 3 if row["state"] in ("applied", "failed") else 2

    def _replan(self, tenant: TenantRef, p: StoredProposal) -> list[str]:
        """Revalidate: kế hoạch trên catalog hiện hành phải đúng kế hoạch người sẽ duyệt."""
        try:
            plan = self.rt.models.migrator.plan(tenant, p.spec, rehearse=False)
        except Exception as e:
            raise revision_conflict_msg(
                f"Đề xuất không còn áp được lên model hiện hành ({e}); hãy soạn đề xuất mới."
            ) from e
        if plan.hash != p.plan_hash:
            raise revision_conflict_msg(
                "Model đã đổi sau khi đề xuất được soạn: kế hoạch không còn như lúc đề xuất."
                " Hãy chạy lại propose_model_change."
            )
        if plan.ids(RiskClass.BLOCKED):
            raise ToolError(
                ErrorCode.RULE_VIOLATION,
                "Đề xuất có thao tác BỊ CHẶN, không duyệt được.",
                details={"blocked": plan.ids(RiskClass.BLOCKED)},
            )
        return plan.ids(RiskClass.DESTRUCTIVE)

    def _second_approver(
        self, caller: Caller, exclude: set[UUID], xrn: str, now: int, owner: UUID | None
    ) -> Any:
        store, actor = self.rt.store, caller.actor
        candidates = []
        if caller.person.manager:
            m = store.person(actor, caller.person.manager)
            if m is not None:
                candidates.append(m)
        _, people = store.people(actor, active_only=True, limit=1000)
        candidates += sorted(people, key=lambda p: p.key)
        for p in candidates:
            if p.id in exclude or not p.subject:
                continue
            res = {"owner": str(owner), "creator": str(owner)} if owner else {}
            if self.guard.allows(caller, p, "request.decide", xrn, now, res):
                return p
        raise precondition_failed(
            "Thay đổi PHÁ HỦY cần người duyệt thứ hai nhưng tổ chức chưa có ai khác đủ quyền.",
            "no_second_approver",
        )

    # ---- get_record ---------------------------------------------------------------------------
    def record(self, caller: Caller, pid: UUID) -> Data | None:
        p = self.rt.models.proposals.get(caller.tenant, pid)
        if p is None:
            return None
        row = self.rt.store.model_change(caller.actor, pid)
        state = row["state"] if row else "pending_approval"
        owner_id = proposer_person(p.proposed_by)
        owner = self.rt.store.person(caller.actor, owner_id) if owner_id else None
        values: Data = {
            "plan_hash": p.plan_hash,
            "base_version": p.base_version,
            "proposed_by": p.proposed_by,
            "catalog_models": sorted(m.name for m in p.spec.models),
            "decided_by": list(row["decided_by"]) if row else [],
            "destructive_confirmed": list(row["confirm"]) if row else [],
            "applied_version": row["applied_version"] if row else None,
            "error": row["error"] if row else None,
            "dispatched": bool(row and row["dispatched_at"]),
        }
        out: Data = {
            "ref": f"{MODEL_PROPOSAL}:{pid}",
            "model_id": MODEL_PROPOSAL,
            "model_revision": p.base_version,
            "revision": self._revision(row),
            "title": "Đề xuất thay đổi model",
            "state": state,
            "values": values,
            "owner": {"ref": f"person:{owner.id}", "name": owner.name} if owner else None,
            "available_transitions": [],
            "created_at": p.created_at,
            "authored_by_kind": "agent" if p.proposed_by.startswith("agent:") else "human",
        }
        if row:
            out["updated_at"] = iso(row["updated_at"])
        return {"record": out}

    # ---- decide_request -----------------------------------------------------------------------
    def plan_decide(self, ctx: CallContext, caller: Caller, pid: UUID, args: Args) -> MutationPlan:
        tenant = caller.tenant
        p = self._proposal(tenant, pid)
        ref = f"{MODEL_PROPOSAL}:{pid}"
        if proposer_person(p.proposed_by) == caller.person.id:
            raise access_denied(
                "Người đề xuất không được tự duyệt thay đổi model (tách nhiệm vụ).", "sod", None
            )
        row = self.rt.store.model_change(caller.actor, pid)
        if int(args["expected_revision"]) != self._revision(row):
            raise revision_conflict(self._revision(row), int(args["expected_revision"]))
        if row is not None:
            raise precondition_failed(
                f"Đề xuất đã được quyết định ({row['state']}).", "already_decided"
            )
        decision = str(args["decision"])
        if decision not in ("approve", "reject"):
            raise invalid_request(
                f"Đề xuất thay đổi model không nhận quyết định '{decision}'.",
                "outcome_not_allowed",
                allowed=["approve", "reject"],
            )
        destructive = self._replan(tenant, p) if decision == "approve" else []
        if caller.person.subject is None:
            raise access_denied(
                "Người duyệt chưa liên kết tài khoản đăng nhập.", "invalid_approver", None
            )
        first = caller.person
        confirmers = [PersonRef(f"person:{first.id}", first.name, first.subject)]
        if destructive:
            exclude = {first.id}
            owner = proposer_person(p.proposed_by)
            if owner:
                exclude.add(owner)
            second = self._second_approver(
                caller, exclude, f"xrn:{caller.alias}:{ref}", int(ctx.now.timestamp()), owner
            )
            confirmers.append(PersonRef(f"person:{second.id}", second.name, second.subject))
        plan = ModelDecidePlan(
            ctx.actor.key,
            ref,
            pid,
            p.plan_hash,
            decision,
            tuple(destructive),
            str(args["reason"]) if args.get("reason") else None,
            tuple(c.ref for c in confirmers),
        )
        what = "Duyệt" if decision == "approve" else "Từ chối"
        summary = (
            f"{what} đề xuất thay đổi model {', '.join(sorted(m.name for m in p.spec.models))}"
        )
        if destructive:
            summary += f" (PHÁ HỦY {destructive}: cần hai người duyệt)"
        return MutationPlan(
            RiskLevel.HUMAN_ONLY,
            summary,
            {
                "ref": ref,
                "plan_hash": p.plan_hash,
                "decision": decision,
                "reason": plan.reason,
                "destructive": list(destructive),
                "confirmers": list(plan.decided_by),
            },
            target_ref=ref,
            confirmers=tuple(confirmers),
            payload=plan,
        )

    def commit(self, ctx: CallContext, caller: Caller, plan: ModelDecidePlan) -> CommitResult:
        p = self._proposal(caller.tenant, plan.proposal_id)
        if plan.decision == "approve":
            destructive = self._replan(caller.tenant, p)  # revalidate lúc thực thi
            if tuple(destructive) != plan.destructive:
                raise revision_conflict_msg("Kế hoạch thay đổi model đã đổi sau khi chuẩn bị.")
        state = "approved" if plan.decision == "approve" else "rejected"
        owner = proposer_person(p.proposed_by)
        for approver in plan.decided_by:  # W9: sổ quyết định, trigger SoD của DB
            try:
                self.rt.store.record_decision(
                    caller.actor,
                    kind="model_change",
                    ref=plan.ref,
                    step=STEP,
                    decision=plan.decision,
                    approver=UUID(approver.removeprefix("person:")),
                    requester=owner,
                    actor_kind=ctx.actor.kind.value,
                    actor_client=ctx.actor.client_id,
                    confirmed_by=ctx.confirmed_by,
                )
            except SodViolation as e:
                raise access_denied(
                    "Quyết định vi phạm tách nhiệm vụ (người duyệt phải khác người đề xuất).",
                    "sod",
                    None,
                ) from e
        ok = self.rt.store.decide_model_change(
            caller.actor,
            plan.proposal_id,
            state=state,
            plan_hash=plan.plan_hash,
            confirm=plan.destructive,
            decided_by=plan.decided_by,
            reason=plan.reason,
        )
        if not ok:
            raise precondition_failed("Đề xuất đã được quyết định.", "already_decided")
        me = caller.person
        return CommitResult(
            {
                "request": {"ref": plan.ref, "state": state, "revision": 2},
                "decision": {
                    "ref": f"decision:{plan.ref}",
                    "decision": plan.decision,
                    "step": STEP,
                    "decided_by": {"ref": f"person:{me.id}", "name": me.name},
                    "confirmed_via": "desk",
                    "decided_at": iso(ctx.now),
                },
                "outcome": "request_approved" if state == "approved" else "request_rejected",
                "next_step": (
                    {"label": "xT worker áp thay đổi model (pha 1 DDL + pha 2)", "approver": None}
                    if state == "approved"
                    else None
                ),
            },
            f"operation:{uuid.uuid4().hex}",
        )

    # ---- trang xác nhận -----------------------------------------------------------------------
    def requester_subject(self, tenant: TenantRef, actor: Any, pid: UUID) -> str:
        p = self._proposal(tenant, pid)
        owner = proposer_person(p.proposed_by)
        person = self.rt.store.person(actor, owner) if owner else None
        if person is None or not person.subject:
            raise ValueError("người đề xuất chưa liên kết tài khoản")
        return person.subject


def revision_conflict_msg(message: str) -> ToolError:
    return ToolError(ErrorCode.REVISION_CONFLICT, message)
