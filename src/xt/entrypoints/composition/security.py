# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ranh giới tin cậy: danh tính đã kiểm -> IAM của host (PEP) và thư mục người.

- Chủ thể IAM: người = id của `xt_meta.person`; agent = `agent:<azp>` với `behalf` = người được
  đại diện, vai trò IAM + trần tự chủ từ `xt_meta.agent_grant` (không bao giờ từ claim token).
- Policy: bản đã duyệt mới nhất của tenant (`xt_meta.iam_policy`, cài qua `xt pack install`).
- Thuộc tính tài nguyên (owner, creator, department, amount) do PEP đọc bằng ngữ cảnh workflow
  của chính người gọi, không lấy từ tham số tool.
- Mức tự chủ/xác nhận của người thuộc dispatcher (TCV1 §3.5); PEP nhận catalog L0 để trần tĩnh
  của nó không chặn trước pipeline xác nhận hai pha. Kind, đại diện và SoD giữ nguyên.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from xt.application.iam.guard import AccessGuard as IamGuard
from xt.application.model_engine.service import APP_ADMIN_ROLE
from xt.application.tools.context import CallContext, PersonRef
from xt.application.tools.errors import ToolError, invalid_request, not_found
from xt.application.tools.ports import APP_DISABLED, AccessCheck, AccessDecision, Args, Data
from xt.domain.autonomy import RiskLevel
from xt.domain.iam import DEFAULT_ACTION_LEVELS, AccessRequest, PolicyDocument, PolicyEvaluator
from xt.domain.iam import Principal as IamPrincipal
from xt.entrypoints.composition.runtime import Caller, Runtime, iam_person
from xt.entrypoints.composition.util import parse_person_ref, parse_record_ref, signed_page

_PEP_CATALOG = {name: RiskLevel.L0 for name in DEFAULT_ACTION_LEVELS}
# Action của host ngoài catalog mặc định: kênh người xem PII `sensitive` (/pii/reveal).
_PEP_CATALOG["pii.reveal"] = RiskLevel.L0


class _People:
    """`PrincipalDirectory` của PEP trong phạm vi một tenant (đọc cùng transaction)."""

    def __init__(self, rt: Runtime, caller: Caller) -> None:
        self.rt, self.caller = rt, caller

    def get(self, key: str) -> IamPrincipal | None:
        try:
            pid = UUID(key)
        except ValueError:
            return None
        p = self.rt.store.person(self.caller.actor, pid)
        return iam_person(self.caller.alias, p) if p and p.active else None


class _NoGrant:
    def get(self, key: str) -> None:
        return None


class ToolAccessGuard:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self._policies: dict[tuple[UUID, int], PolicyEvaluator] = {}
        self._lock = threading.Lock()

    def _evaluator(self, caller: Caller) -> PolicyEvaluator | None:
        found = self.rt.store.active_policy(caller.actor)
        if found is None:
            return None
        key = (caller.tenant.id, found[0])
        with self._lock:
            hit = self._policies.get(key)
        if hit is not None:
            return hit
        doc = PolicyDocument.model_validate(found[1])
        ev = PolicyEvaluator(doc)
        with self._lock:
            self._policies[key] = ev
        return ev

    def _resource(
        self, ctx: CallContext, caller: Caller, check: AccessCheck
    ) -> tuple[str, dict[str, Any]]:
        ref = check.resource
        if ref is None:
            model = check.attributes.get("model_id")
            if not model:
                model = (
                    check.attributes.get("task_type") or "task"
                    if check.action == "task.create"
                    else "workspace"
                )
            model = str(model)
            # Tạo mới: chủ sở hữu tương lai là người thật đứng sau lời gọi.
            res: dict[str, Any] = {
                "owner": str(caller.person.id),
                "creator": str(caller.person.id),
            }
            values = check.attributes.get("values")
            proposed = values if isinstance(values, Mapping) else {}
            raw_owner = proposed.get("owner_id")
            if raw_owner is not None:
                try:
                    proposed_owner_id = (
                        caller.person.id if raw_owner == "me" else UUID(str(raw_owner))
                    )
                except ValueError as exc:
                    raise invalid_request("Invalid owner_id.", "bad_reference") from exc
                owner = self.rt.store.person(caller.actor, proposed_owner_id)
                if owner is None:
                    raise not_found(f"person:{proposed_owner_id}")
                res["owner"] = str(proposed_owner_id)
            if "department_id" in proposed and proposed["department_id"] is not None:
                try:
                    dept_id = UUID(str(proposed["department_id"]))
                except ValueError as exc:
                    raise invalid_request("Invalid department_id.", "bad_reference") from exc
                unit = self.rt.store.unit(caller.actor, uid=dept_id)
                if unit is None:
                    raise not_found(f"department:{dept_id}")
                res["department"] = unit.key
            elif (
                "department_id" not in proposed
                and res["owner"] == str(caller.person.id)
                and caller.person.unit_key
            ):
                res["department"] = caller.person.unit_key
            if model == "process" and isinstance(check.attributes.get("process_id"), str):
                # start_process: tài nguyên là chính quy trình, người khởi tạo là chủ (owner).
                return f"xrn:{ctx.tenant}:process:{check.attributes['process_id']}", res
            return f"xrn:{ctx.tenant}:{model}:{model}", res
        model, rid = parse_record_ref(ctx, ref)
        res = {}
        if model == "model_proposal":
            from xt.entrypoints.composition.model_changes import proposer_person

            prop = self.rt.models.proposals.get(caller.tenant, rid)
            owner_id = proposer_person(prop.proposed_by) if prop else None
            if owner_id is not None:
                res = {"owner": str(owner_id), "creator": str(owner_id)}
            return f"xrn:{ctx.tenant}:{model}:{rid}", res
        res = self._record_attrs(caller, model, rid)
        return f"xrn:{ctx.tenant}:{model}:{rid}", res

    def _record_attrs(self, caller: Caller, model: str, rid: UUID) -> dict[str, Any]:
        """Thuộc tính tài nguyên (owner, creator, department, amount) đọc bằng ngữ cảnh workflow."""
        res: dict[str, Any] = {}
        try:
            out = self.rt.records.get(caller.workflow(), model, rid)
        except Exception:  # không thấy / không có model: policy quyết trên thuộc tính rỗng
            out = None
        if out is not None:
            row = out["record"]
            owner = row.get("owner_id")
            res["owner"] = str(owner) if owner else ""
            creator = row.get("created_by") or owner
            res["creator"] = str(creator) if creator else ""
            department = row.get("department_id")
            if department is not None:
                unit = self.rt.store.unit(caller.actor, uid=UUID(str(department)))
                if unit is not None:
                    res["department"] = unit.key
            elif owner:  # legacy records created before department attribution
                p = self.rt.store.person(caller.actor, UUID(str(owner)))
                if p is not None and p.unit_key:
                    res["department"] = p.unit_key
            amount = row.get("amount")
            if isinstance(amount, int | float | str) and str(amount).replace(".", "", 1).isdigit():
                res["amount"] = float(amount)
        return {k: v for k, v in res.items() if v != ""}

    def can_read(self, caller: Caller, ref: str, now: int) -> bool:
        """Người `caller.person` có đọc được bản ghi `ref` NGAY BÂY GIỜ không: IAM (`record.read`
        / `person.read` theo policy đã duyệt) VÀ RLS (đọc bằng ngữ cảnh của chính người đó)."""
        model, _, raw = ref.partition(":")
        try:
            rid = UUID(raw)
        except ValueError:
            return False
        xrn = f"xrn:{caller.alias}:{model}:{rid}"
        if model == "person":
            target = self.rt.store.person(caller.actor, rid)
            return target is not None and self.allows(
                caller, caller.person, "person.read", xrn, now
            )
        attrs = self._record_attrs(caller, model, rid)
        if not self.allows(caller, caller.person, "record.read", xrn, now, attrs):
            return False
        try:
            self.rt.records.get(caller.actor, model, rid)
        except Exception:  # RLS / quyền model không cho thấy bản ghi
            return False
        return True

    def check(self, ctx: CallContext, check: AccessCheck) -> AccessDecision:
        caller = self.rt.caller(ctx)
        ev = self._evaluator(caller)
        if ev is None:
            return AccessDecision(
                False,
                "no_policy",
                "Tổ chức chưa có policy IAM được duyệt (cài gói bằng `xt pack install`).",
            )
        try:
            xrn, resource = self._resource(ctx, caller, check)
        except ToolError as e:
            return AccessDecision(False, "bad_reference", e.message)
        pep = IamGuard(ev, _People(self.rt, caller), _NoGrant(), _NoGrant(), catalog=_PEP_CATALOG)
        now = int(ctx.now.timestamp())
        result = pep.evaluate(AccessRequest(caller.iam(), check.action, xrn, resource, now=now))
        if result.allowed:
            for subject in ctx.confirmed_by:
                p = self.rt.store.person_by_subject(caller.actor, subject)
                if p is None:
                    return AccessDecision(False, "invalid_approver", "Người xác nhận không hợp lệ.")
                result = pep.evaluate(
                    AccessRequest(iam_person(caller.alias, p), check.action, xrn, resource, now=now)
                )
                if not result.allowed:
                    break
        if not result.allowed:
            off = self._app_disabled(caller, xrn, check.action)
            if off is not None:
                return off
        return AccessDecision(
            result.allowed,
            result.reason.value,
            "" if result.allowed else "Không có quyền thực hiện thao tác này.",
            None if result.allowed else f"explain:{ctx.trace_id}",
            dict(result.detail),
        )

    def _app_disabled(self, caller: Caller, xrn: str, action: str) -> AccessDecision | None:
        """Gói gỡ quyền IAM khi tắt nên IAM luôn từ chối; model của ứng dụng đã cài nhưng đang
        tắt ở tenant của chính người gọi phải ra `app_disabled` (dispatcher đổi thành
        `capability_unavailable`), không phải `access_denied`. Chỉ áp cho model thuộc gói tắt của
        tenant này (RLS): model khác vẫn `access_denied`, không lộ tồn tại. Admin đọc giữ nguyên
        quyết định IAM."""
        parts = xrn.split(":")
        if len(parts) < 4:
            return None
        model = parts[2]
        if action.endswith(".read") and APP_ADMIN_ROLE in caller.actor.roles:
            return None
        try:
            pack = self.rt.catalogs.disabled(caller.actor).models.get(model)
        except Exception:  # không xác định được: giữ nguyên từ chối IAM (fail-closed)
            return None
        if pack is None:
            return None
        return AccessDecision(
            False,
            APP_DISABLED,
            f"Ứng dụng '{pack}' đang tắt ở đơn vị này nên không dùng được '{model}'",
            None,
            {"pack": pack, "model": model},
        )

    def allows(
        self,
        caller: Caller,
        person: Any,
        action: str,
        xrn: str,
        now: int,
        resource: Mapping[str, Any] | None = None,
    ) -> bool:
        """`person` (xt_meta.person) có được `action` trên `xrn` theo policy đã duyệt không (chọn
        người duyệt thứ hai cho đề xuất model PHÁ HỦY)."""
        ev = self._evaluator(caller)
        if ev is None or not person.active:
            return False
        pep = IamGuard(ev, _People(self.rt, caller), _NoGrant(), _NoGrant(), catalog=_PEP_CATALOG)
        req = AccessRequest(
            iam_person(caller.alias, person), action, xrn, dict(resource or {}), now=now
        )
        return bool(pep.evaluate(req).allowed)

    def person_allowed(self, ctx: CallContext, person: Any, action: str, ref: str) -> bool:
        """`person` có qua PEP `action` trên bản ghi `ref` không (thuộc tính tài nguyên đọc như
        `check`). Dùng để kiểm đích ủy quyền trước khi tạo PendingOperation (WA-02)."""
        caller = self.rt.caller(ctx)
        try:
            xrn, resource = self._resource(ctx, caller, AccessCheck("", action, ref))
        except ToolError:
            return False
        return self.allows(caller, person, action, xrn, int(ctx.now.timestamp()), resource)

    def autonomy_ceiling(self, ctx: CallContext) -> RiskLevel:
        caller = self.rt.caller(ctx)
        if not caller.is_agent:
            return RiskLevel.L3
        return RiskLevel(f"L{min(max(caller.autonomy, 0), 3)}")


# ---------------------------------------------------------------------- thư mục người ----
def _named(p: Any) -> dict[str, str]:
    return {"ref": f"person:{p.id}", "name": p.name}


class PeopleDirectory:
    """`Directory` port trên `xt_meta.person` / `xt_meta.org_unit`."""

    def __init__(self, rt: Runtime, cursor_key: bytes) -> None:
        self.rt, self.cursor_key = rt, cursor_key

    def person_for_subject(self, ctx: CallContext, subject: str) -> PersonRef | None:
        caller = self.rt.caller(ctx)
        p = self.rt.store.person_by_subject(caller.actor, subject)
        if p is None or not p.active:
            return None
        return PersonRef(f"person:{p.id}", p.name, p.subject)

    def _unit(self, caller: Caller, uid: UUID | None) -> dict[str, str] | None:
        if uid is None:
            return None
        u = self.rt.store.unit(caller.actor, uid=uid)
        return {"ref": f"org_unit:{u.key.lower()}", "name": u.name} if u else None

    def _row(self, caller: Caller, p: Any) -> Data:
        row: Data = {
            "ref": f"person:{p.id}",
            "name": p.name,
            "org_unit": self._unit(caller, p.unit),
            "status": "active" if p.active else "inactive",
        }
        if p.title:
            row["title"] = p.title
        if p.manager:
            m = self.rt.store.person(caller.actor, p.manager)
            row["manager"] = _named(m) if m else None
        else:
            row["manager"] = None
        return row

    def person_details(self, ctx: CallContext, ref: str) -> Data | None:
        """`PersonDetails` cho MCP App sơ đồ tổ chức: như một phần tử `people[]` + liên hệ +
        cấp dưới trực tiếp; email là giá trị thật, làn PII che theo tier của client."""
        caller = self.rt.caller(ctx)
        try:
            pid = parse_person_ref(caller, ref)
        except ToolError:
            return None
        p = self.rt.store.person(caller.actor, pid)
        if p is None:
            return None
        row = self._row(caller, p)
        if p.email:
            row["email"] = p.email
        _, reports = self.rt.store.people(caller.actor, manager=p.id, limit=200)
        row["direct_reports"] = [_named(r) for r in reports]
        return row

    def find_people(self, ctx: CallContext, args: Args) -> Data:
        caller = self.rt.caller(ctx)
        store, actor = self.rt.store, caller.actor
        limit = int(args.get("limit", 20))
        page = signed_page(self.cursor_key, ctx, args)
        offset = page.offset()
        anchor = None
        relation = "search"
        kw: dict[str, Any] = {"query": args.get("query")}
        if "direct_reports_of" in args or "manager_of" in args:
            key = "direct_reports_of" if "direct_reports_of" in args else "manager_of"
            target = store.person(actor, parse_person_ref(caller, args[key]))
            if target is None:
                raise not_found(str(args[key]))
            anchor = _named(target)
            if key == "direct_reports_of":
                relation, kw["manager"] = "direct_reports_of", target.id
            else:
                relation = "manager_of"
                kw["ids"] = [target.manager] if target.manager else []
        elif "org_unit" in args:
            raw = str(args["org_unit"])
            unit_key = raw.split(":", 1)[1] if raw.startswith("org_unit:") else raw
            unit = store.unit(actor, key=unit_key.upper()) or store.unit(actor, key=unit_key)
            if unit is None:
                raise not_found(raw)
            relation, kw["unit"] = "members_of_unit", unit.id
            anchor = {"ref": f"org_unit:{unit.key.lower()}", "name": unit.name}
        elif "role" in args:
            relation, kw["role"] = "has_role", str(args["role"])
        total, people = store.people(actor, limit=limit, offset=offset, **kw)
        rows = [self._row(caller, p) for p in people]
        nxt = page.next(offset + len(rows)) if offset + len(rows) < total else None
        what = {
            "search": "khớp tìm kiếm",
            "direct_reports_of": f"báo cáo trực tiếp cho {anchor['name'] if anchor else ''}",
            "manager_of": f"là quản lý của {anchor['name'] if anchor else ''}",
            "members_of_unit": f"thuộc {anchor['name'] if anchor else ''}",
            "has_role": f"có vai trò {args.get('role')}",
        }[relation]
        return {
            "relation": relation,
            "anchor": anchor,
            "summary": f"Có {total} người {what}.",
            "people": rows,
            "next_cursor": nxt,
            "total": total,
        }


def reject_masked_input(args: Args) -> None:
    if "[PII:" in repr(dict(args)):
        raise invalid_request("Không thể dùng token PII khi chưa có làn giải mã.", "masked_value")


def person_named(rt: Runtime, caller: Caller, pid: Any) -> dict[str, str] | None:
    if pid is None:
        return None
    try:
        p = rt.store.person(caller.actor, UUID(str(pid)))
    except ValueError:
        return None
    return _named(p) if p else None


def resource_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:  # pragma: no cover - debug aid
    return {k: row.get(k) for k in ("owner_id", "created_by", "revision")}
