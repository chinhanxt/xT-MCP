# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Op dịch vụ của gói `giao-viec-sla` và `nhan-su-vong-doi` (registry `OPS` của deployment).

Ràng buộc chung (docs/decisions.md "Op dịch vụ của gói giao việc / vòng đời nhân sự"):
- Không có mã tùy ý: pack chỉ khai tên op + tham số (biểu thức AST whitelist); tham số được kiểm
  kiểu ở đây (uuid bản ghi, ngày ISO, chuỗi giới hạn) trước khi chạm dữ liệu.
- Mọi ghi đi qua `RecordService` với vai trò dịch vụ `xt_workflow` (quyền model + rule + RLS +
  audit trigger như mọi lời ghi khác), trong CÙNG transaction của bước quy trình.
- Không gọi hệ thống ngoài: "cấp tài khoản / thu hồi quyền" là checklist `lifecycle_task` cho
  IT/HR (sinh từ chính các bước người của định nghĩa quy trình đã duyệt), không phải lệnh tới IdP.
- Idempotent theo dữ liệu: chạy lại (rollback rồi thử lại) không nhân bản checklist/bản tin.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from uuid import UUID
from zoneinfo import ZoneInfo

from xt.application.process.ports import ServiceFn
from xt.domain.process.spec import ApproveStep, ProcessSpec, TaskStep
from xt.entrypoints.composition.util import engine_call

if TYPE_CHECKING:
    from xt.adapters.db.integration import ProcessDefinition
    from xt.entrypoints.composition.process import ProcessContext

TASK, SLA_LOG, DIGEST = "task", "sla_log", "weekly_digest"
CASE, LTASK, EMPLOYEE = "lifecycle_case", "lifecycle_task", "employee"
ASSET, GRANT = "asset_issue", "access_grant"
TASK_OPEN_END = ("done", "cancelled", "not_accepted")
MAX_ROWS = 200
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_DEPT = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


class OpInputError(ValueError):
    """Tham số op sai kiểu/giá trị: bước dịch vụ lỗi, runtime khôi phục state (fail closed)."""


# ------------------------------------------------------------------------ kiểm tham số ----
def record_id(value: object, model: str) -> UUID:
    """`<model>:<uuid>` hoặc `<uuid>`; tiền tố model khác -> lỗi (không ghi nhầm model)."""
    s = str(value or "").strip()
    if ":" in s:
        prefix, s = s.split(":", 1)
        if prefix != model:
            raise OpInputError(f"tham chiếu {value!r} không thuộc model {model!r}")
    try:
        return UUID(s)
    except ValueError:
        raise OpInputError(f"tham chiếu bản ghi {model} không hợp lệ: {value!r}") from None


def _date(value: object, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise OpInputError(f"{name} phải là ngày ISO (YYYY-MM-DD), nhận {value!r}") from None


def _instance(idempotency_key: str) -> str:
    return idempotency_key.split(":", 1)[0]


def _now(pc: ProcessContext) -> datetime:
    return datetime.now(ZoneInfo(pc.timezone))


def _parse_dt(v: object) -> datetime | None:
    if not v:
        return None
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# ------------------------------------------------------------------------ truy cập dữ liệu ----
class _Data:
    """Đọc/ghi bản ghi bằng vai trò `xt_workflow` (đường duy nhất ghi trường managed_by)."""

    def __init__(self, pc: ProcessContext) -> None:
        self.pc = pc
        self.records = pc.rt.records
        self.actor = pc.actor

    def get(self, model: str, rid: UUID) -> dict[str, Any]:
        out = engine_call(lambda: self.records.get(self.actor, model, rid))
        return dict(out["record"])

    def write(self, model: str, row: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
        rid, rev = UUID(str(row["id"])), int(row["revision"])
        out = engine_call(lambda: self.records.workflow_write(self.actor, model, rid, rev, patch))
        return dict(out["record"])

    def create(self, model: str, values: Mapping[str, Any]) -> dict[str, Any]:
        out = engine_call(lambda: self.records.create(self.actor, model, values))
        return dict(out["record"])

    def count(self, model: str, where: Mapping[str, Any]) -> int:
        res = engine_call(
            lambda: self.records.search(self.actor, model, where, ("-created_at",), 1, 0)
        )
        return int(res["total"])

    def rows(self, model: str, where: Mapping[str, Any]) -> list[dict[str, Any]]:
        res = engine_call(
            lambda: self.records.search(self.actor, model, where, ("created_at",), MAX_ROWS, 0)
        )
        if res["has_more"]:
            raise OpInputError(f"{model}: quá {MAX_ROWS} bản ghi cho một bước dịch vụ")
        return [dict(r) for r in res["items"]]

    def fields(self, model: str) -> set[str]:
        current = self.pc.rt.catalogs.current(self.actor)
        return set(current[1].model(model).by_name()) if current is not None else set()

    def enum_options(self, model: str, field: str) -> set[str]:
        current = self.pc.rt.catalogs.current(self.actor)
        if current is None:
            return set()
        f = current[1].model(model).by_name().get(field)
        return {o.value for o in (f.options or [])} if f is not None else set()

    def history_comment(self, instance_id: str, step: str, outcome: str) -> str | None:
        from xt.adapters.db.workflow_bridge import PgWorkflowStore

        rec = PgWorkflowStore(self.pc.rt.uow).get_for_update(instance_id)
        hist = (rec.state.get("meta") or {}).get("history", []) if rec else []
        for h in reversed(hist):
            if h.get("step") == step and h.get("outcome") == outcome and h.get("comment"):
                return str(h["comment"])
        return None


def _eq(field: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": "=", "value": value}


# ------------------------------------------------------------------------ giao-viec-sla ----
def close_task(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Nghiệm thu đạt -> task `done` (completed_at, progress 100) + `sla_log` đúng/trễ hạn.

    Kết quả nộp: giữ `result` đã có; chưa có thì lấy ghi chú của bước `work` (người làm nộp khi
    hoàn thành) hoặc ghi rõ "nghiệm thu qua quy trình <instance>" (rule r1 của model)."""

    def op(task_id: str, idempotency_key: str) -> dict[str, Any]:
        db = _Data(pc)
        iid = _instance(idempotency_key)
        row = db.get(TASK, record_id(task_id, TASK))
        if row.get("state") == "done":
            return {"task": str(row["id"]), "state": "done", "on_time": None, "already": True}
        now = _now(pc)
        patch: dict[str, Any] = {"state": "done", "completed_at": now.isoformat(), "progress": 100}
        if not row.get("result"):
            note = db.history_comment(iid, "work", "done")
            patch["result"] = (note or f"Nghiệm thu đạt qua quy trình {iid}")[:4000]
        if not row.get("process_instance_id"):
            patch["process_instance_id"] = iid[:64]
        db.write(TASK, row, patch)
        deadline = _parse_dt(row.get("deadline"))
        on_time = deadline is None or now <= deadline
        db.create(
            SLA_LOG,
            {
                "task": str(row["id"]),
                "event": "completed_on_time" if on_time else "completed_late",
                "at_time": now.isoformat(),
                "note": f"Đóng bởi quy trình {d.process_id} v{d.version} ({iid})"[:500],
            },
        )
        return {"task": str(row["id"]), "state": "done", "on_time": on_time, "already": False}

    return op


def _iso_week_code(start: date, department: str) -> str:
    y, w, _ = start.isocalendar()
    code = f"BT-{y:04d}-W{w:02d}"
    return f"{code}-{department.upper()}" if department else code


def weekly_rollup(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Tổng hợp việc trong [from_date, to_date] (theo múi giờ tenant) -> bản ghi `weekly_digest`
    (một bản theo mã tuần + phòng; chạy lại thì cập nhật số liệu nếu chưa gửi)."""

    def op(from_date: Any, to_date: Any, department: Any) -> dict[str, Any]:
        db = _Data(pc)
        start, end = _date(from_date, "from_date"), _date(to_date, "to_date")
        if end < start:
            raise OpInputError("to_date phải sau hoặc bằng from_date")
        dept = str(department or "").strip()
        if dept and not _DEPT.match(dept):
            raise OpInputError(f"department không hợp lệ: {department!r}")
        tz = ZoneInfo(pc.timezone)
        lo = datetime.combine(start, time.min, tz).isoformat()
        hi = datetime.combine(end, time.max, tz).isoformat()
        scope: list[dict[str, Any]] = []
        if dept:
            unit = pc.rt.store.unit(pc.actor, key=dept)
            if unit is None:
                raise OpInputError(f"không có phòng ban {dept!r} trong tổ chức")
            _, people = pc.rt.store.people(pc.actor, unit=unit.id, limit=1000)
            ids = [str(p.id) for p in people] or ["00000000-0000-0000-0000-000000000000"]
            scope = [{"field": "assignee", "op": "in", "value": ids}]

        def where(*conds: dict[str, Any]) -> dict[str, Any]:
            return {"and": [*scope, *conds]}

        assigned = db.count(
            TASK,
            where({"field": "created_at", "op": ">=", "value": lo},
                  {"field": "created_at", "op": "<=", "value": hi}),
        )  # fmt: skip
        completed = db.count(
            TASK,
            where(_eq("state", "done"),
                  {"field": "completed_at", "op": ">=", "value": lo},
                  {"field": "completed_at", "op": "<=", "value": hi}),
        )  # fmt: skip
        now = _now(pc).isoformat()
        overdue = db.count(
            TASK,
            where({"not": {"field": "state", "op": "in", "value": list(TASK_OPEN_END)}},
                  {"field": "deadline", "op": "<", "value": now}),
        )  # fmt: skip
        blocked = db.count(TASK, where(_eq("state", "blocked")))
        label = f"phòng {dept}" if dept else "toàn công ty"
        summary = (
            f"Bản tin công việc {start.isoformat()}..{end.isoformat()} ({label}): giao mới"
            f" {assigned}, hoàn thành {completed}, quá hạn {overdue}, đang bị chặn {blocked}."
        )
        code = _iso_week_code(start, dept)
        values = {
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "department": dept or None,
            "total_assigned": assigned,
            "total_completed": completed,
            "total_overdue": overdue,
            "total_blocked": blocked,
            "content": summary,
            "detail": {"process": d.process_id, "version": d.version, "generated_at": now},
        }
        found = db.rows(DIGEST, _eq("code", code))
        if found and found[0].get("sent"):
            row = found[0]
        elif found:
            row = db.write(DIGEST, found[0], values)
        else:
            row = db.create(DIGEST, {"code": code, **values})
        return {
            "digest_id": str(row["id"]),
            "code": code,
            "summary": str(row.get("content") or summary),
            "assigned": assigned,
            "completed": completed,
            "overdue": overdue,
            "blocked": blocked,
        }

    return op


def send_weekly_digest(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Gửi bản tin vào hộp thông báo của người có vai trò khai trong pack (`to_roles`, phân cách
    dấu phẩy) và trưởng phòng của bản tin (nếu bản tin theo phòng); đánh dấu `sent`."""

    def op(digest_id: str, to_roles: str) -> dict[str, Any]:
        from xt.entrypoints.composition.process import OrgView

        db = _Data(pc)
        roles = [r.strip() for r in str(to_roles or "").split(",") if r.strip()]
        if not roles or any(not _ROLE.match(r) for r in roles):
            raise OpInputError(f"to_roles không hợp lệ: {to_roles!r}")
        row = db.get(DIGEST, record_id(digest_id, DIGEST))
        org = OrgView(pc.rt, pc.actor)
        recipients: set[str] = set()
        for r in roles:
            recipients.update(org.users_with_role(r))
        dept = row.get("department")
        if dept:
            head = org.department_head(str(dept))
            if head:
                recipients.add(head)
        ref = f"{DIGEST}:{row['id']}"
        title = str(row.get("content") or row["code"])
        sent = 0
        for uid in sorted(recipients):
            if pc.rt.store.notify(pc.actor, UUID(uid), title, ref, f"digest:{row['code']}:{uid}"):
                sent += 1
        if not row.get("sent"):
            db.write(DIGEST, row, {"sent": True})
        return {"recipients": len(recipients), "sent": sent}

    return op


# ------------------------------------------------------------------------ nhan-su-vong-doi ----
def _checklist(d: ProcessDefinition, units: set[str]) -> list[tuple[str, str, str]]:
    """(bộ phận, tên việc, id bước) cho MỌI bước người của CHÍNH định nghĩa đã duyệt (kể cả bước có
    `when`): mỗi dòng là bản ghi chủ thể của bước (`complete_via` khớp theo `step`). Bộ phận = id
    bước, hoặc tiền tố trước `_`, hoặc vai trò `role(x)` của người nhận, nếu trùng một giá trị
    `owner_unit` của model; không trùng thì bỏ qua."""
    spec = ProcessSpec.model_validate(d.spec)
    out: list[tuple[str, str, str]] = []
    for st, _ in spec.iter_steps():
        if not isinstance(st, ApproveStep | TaskStep):
            continue
        target = st.target()
        for unit in (st.id, st.id.split("_", 1)[0], target.role if target.kind == "role" else None):
            if unit in units:
                out.append((unit, st.name[:200], st.id))
                break
    return out


def create_lifecycle_tasks(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Sinh checklist liên phòng ban của hồ sơ (một lần); nghỉ việc: nhân viên chuyển
    `offboarding_in_progress` với ngày làm việc cuối = ngày hiệu lực của hồ sơ."""

    def op(case_id: str, kind: str, idempotency_key: str) -> dict[str, Any]:
        db = _Data(pc)
        case = db.get(CASE, record_id(case_id, CASE))
        if case.get("kind") != kind:
            raise OpInputError(f"hồ sơ {case['code']} là {case.get('kind')!r}, không phải {kind!r}")
        if case.get("state") != "processing":
            raise OpInputError(f"hồ sơ {case['code']} đang ở trạng thái {case.get('state')!r}")
        existing = db.rows(LTASK, _eq("lifecycle_case", str(case["id"])))
        created: list[str] = []
        if not existing:
            has_step = "step" in db.fields(LTASK)
            for unit, name, step in _checklist(d, db.enum_options(LTASK, "owner_unit")):
                values = {
                    "lifecycle_case": str(case["id"]),
                    "owner_unit": unit,
                    "name": name,
                    "state": "waiting",
                    "due_on": case.get("effective_on"),
                }
                if has_step:
                    values["step"] = step
                row = db.create(LTASK, values)
                created.append(str(row["id"]))
        if not case.get("process_instance_id"):
            db.write(CASE, case, {"process_instance_id": _instance(idempotency_key)[:64]})
        if kind == "offboarding":
            emp = db.get(EMPLOYEE, record_id(case["employee"], EMPLOYEE))
            if emp.get("state") not in ("offboarding_in_progress", "resigned"):
                db.write(
                    EMPLOYEE,
                    emp,
                    {
                        "state": "offboarding_in_progress",
                        "left_on": emp.get("left_on") or case.get("effective_on"),
                    },
                )
        return {"created": len(created), "existing": len(existing), "tasks": created}

    return op


def activate_employee(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Nhân viên mới `waiting_to_start` -> `probation`; ngày vào làm = ngày hiệu lực của hồ sơ
    tiếp nhận đang xử lý (không có hồ sơ thì hôm nay theo múi giờ tenant)."""

    def op(employee_id: str) -> dict[str, Any]:
        db = _Data(pc)
        emp = db.get(EMPLOYEE, record_id(employee_id, EMPLOYEE))
        state = emp.get("state")
        if state != "waiting_to_start":
            if state in ("probation", "permanent"):
                return {"employee": str(emp["id"]), "state": state, "already": True}
            raise OpInputError(f"nhân viên {emp['emp_no']} đang {state!r}, không kích hoạt được")
        patch: dict[str, Any] = {"state": "probation"}
        if not emp.get("hired_on"):
            cases = db.rows(
                CASE,
                {"and": [_eq("employee", str(emp["id"])), _eq("kind", "onboarding"),
                         _eq("state", "processing")]},
            )  # fmt: skip
            patch["hired_on"] = cases[-1]["effective_on"] if cases else _now(pc).date().isoformat()
        db.write(EMPLOYEE, emp, patch)
        return {"employee": str(emp["id"]), "state": "probation", "already": False}

    return op


def _unreturned(db: _Data, employee: str) -> dict[str, int]:
    open_ = {"and": [_eq("employee", employee), {"field": "revoked_on", "op": "is_null"}]}
    assets, grants = db.count(ASSET, open_), db.count(GRANT, open_)
    return {"unreturned": assets + grants, "assets": assets, "grants": grants}


def check_unreturned_assets(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Số tài sản chưa thu hồi + quyền truy cập chưa thu hồi của nhân viên."""

    def op(employee_id: str) -> dict[str, int]:
        return _unreturned(_Data(pc), str(record_id(employee_id, EMPLOYEE)))

    return op


def _acted_steps(pc: ProcessContext, instance_id: str) -> set[str]:
    from xt.adapters.db.workflow_bridge import PgWorkflowStore

    rec = PgWorkflowStore(pc.rt.uow).get_for_update(instance_id)
    hist = (rec.state.get("meta") or {}).get("history", []) if rec else []
    return {str(h["step"]) for h in hist if h.get("step")}


def close_lifecycle_case(pc: ProcessContext, d: ProcessDefinition) -> ServiceFn:
    """Hồ sơ -> `completed` (closed_at); checklist còn chờ: bước đã làm -> `done`, bước không
    áp dụng (điều kiện `when` không thỏa, chưa từng chạy) -> `skipped` kèm ghi chú; nghỉ việc:
    nhân viên -> `resigned`. Chốt "không đóng được khi chưa thu hồi": hồ sơ nghỉ việc còn tài
    sản/quyền truy cập chưa thu hồi -> lỗi (bước dịch vụ thất bại, instance đứng nguyên)."""

    def op(case_id: str, idempotency_key: str = "") -> dict[str, Any]:
        db = _Data(pc)
        case = db.get(CASE, record_id(case_id, CASE))
        if case.get("state") == "completed":
            return {"case": str(case["id"]), "state": "completed", "already": True}
        if case.get("state") != "processing":
            raise OpInputError(f"hồ sơ {case['code']} đang ở trạng thái {case.get('state')!r}")
        if case.get("kind") == "offboarding":
            left = _unreturned(db, str(record_id(case["employee"], EMPLOYEE)))
            if left["unreturned"]:
                raise OpInputError(
                    f"hồ sơ {case['code']}: còn {left['assets']} tài sản và {left['grants']} quyền"
                    " truy cập chưa thu hồi, chưa đóng được"
                )
        done = 0
        # Bước có điều kiện chưa từng chạy -> skipped. Lịch sử đọc từ DB là trạng thái TRƯỚC lần
        # hoàn thành đang chạy, nên chỉ xét bước có `when` (bước thường chắc chắn đã chạy).
        spec = ProcessSpec.model_validate(d.spec)
        conditional = {st.id for st, _ in spec.iter_steps() if getattr(st, "when", None)}
        acted = _acted_steps(pc, _instance(idempotency_key)) if idempotency_key else set()
        for t in db.rows(LTASK, _eq("lifecycle_case", str(case["id"]))):
            if t.get("state") != "waiting":
                continue
            if t.get("step") in conditional and t["step"] not in acted:
                db.write(LTASK, t, {"state": "skipped", "note": "Bước không áp dụng cho hồ sơ này"})
                continue
            db.write(LTASK, t, {"state": "done"})
            done += 1
        if case.get("kind") == "offboarding":
            emp = db.get(EMPLOYEE, record_id(case["employee"], EMPLOYEE))
            if emp.get("state") != "resigned":
                db.write(
                    EMPLOYEE,
                    emp,
                    {"state": "resigned", "left_on": emp.get("left_on") or case["effective_on"]},
                )
        db.write(CASE, case, {"state": "completed", "closed_at": _now(pc).isoformat()})
        return {"case": str(case["id"]), "state": "completed", "tasks_done": done}

    return op


PackOpFactory = Callable[["ProcessContext", "ProcessDefinition"], ServiceFn]

PACK_OPS: dict[str, tuple[tuple[str, ...], PackOpFactory]] = {
    "close_task": (("task_id",), close_task),
    "weekly_rollup": (("from_date", "to_date", "department"), weekly_rollup),
    "send_weekly_digest": (("digest_id", "to_roles"), send_weekly_digest),
    "create_lifecycle_tasks": (("case_id", "kind"), create_lifecycle_tasks),
    "activate_employee": (("employee_id",), activate_employee),
    "check_unreturned_assets": (("employee_id",), check_unreturned_assets),
    "close_lifecycle_case": (("case_id",), close_lifecycle_case),
}
