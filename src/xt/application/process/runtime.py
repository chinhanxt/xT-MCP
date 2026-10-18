# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Runtime instance: giao việc động, kiểm outcome (fail closed), SoD theo bước, vòng trả lại /
ủy quyền / hỏi thông tin, lưu/khôi phục JSON.

Mỗi `act` là nguyên tử: kiểm toàn bộ trước khi chạm engine (`DecisionError`, state không đổi);
engine lỗi thì khôi phục snapshot (`EngineError`). Chỉ runtime ghi các khóa
`{step}_decision/_comment/_actor/_info/_revise`; mỗi bước chỉ ghi khóa có tiền tố id bước (id duy
nhất toàn spec), nên hai nhánh song song không ghi cùng khóa.

Join của SpiffWorkflow gộp data kiểu "nhánh về sau ghi đè": khi quay vòng (resubmit, to_step)
runtime XÓA (không gán None) mọi biến của các bước từ điểm quay lại trở đi, và không khởi tạo sẵn
biến quyết định.
"""

from __future__ import annotations

import copy
import re
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from xt.application.process.ports import (
    CompiledProcess,
    EventSink,
    ProcessEngine,
    ServiceRegistry,
    TimerScheduler,
    UserTask,
    WorkflowHandle,
)
from xt.application.tools.errors import ToolError
from xt.domain.model.errors import ModelError
from xt.domain.process.assignment import AssigneeResolver, Assignment, OrgDirectory, OrgUser
from xt.domain.process.errors import DecisionError, EngineError
from xt.domain.process.schedule import validate_fire
from xt.domain.process.spec import Assignee, TimerTrigger

MAX_COMMENT = 2000
MAX_LOOPS_PER_STEP = 5  # delegate + request_info mỗi bước: chặn "chuyền bóng" làm SLA chạy lại mãi
MAX_NUMBER = 10**15
MAX_OBJECT_REPR = 16384
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STEP_SUFFIXES = ("_decision", "_comment", "_actor", "_info", "_result", "_revise")
DECISION_KINDS = ("decision", "task")


def _failure_details(error: BaseException) -> dict[str, Any]:
    """Read typed failures through engine wrappers; message text never identifies an app."""
    pending, seen = [error], set()
    fallback: dict[str, Any] = {"code": "internal_error", "reason": "engine_error"}
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ModelError | ToolError):
            code = current.code.value if isinstance(current, ToolError) else current.code
            details = current.details
            found = {"code": code, "reason": details.get("reason", "engine_error")}
            found.update({k: details[k] for k in ("model", "pack") if k in details})
            if code == "capability_unavailable" and found["reason"] == "app_disabled":
                return found
            fallback = found
        for nested in (current.__context__, current.__cause__):
            if nested is not None:
                pending.append(nested)
    return fallback


def _text(v: object, field_name: str, *, required: bool = False) -> str | None:
    if v is None or v == "":
        if required:
            raise DecisionError(f"cần {field_name}", code="invalid_request")
        return None
    if not isinstance(v, str) or len(v) > MAX_COMMENT:
        raise DecisionError(
            f"{field_name} phải là chuỗi ≤ {MAX_COMMENT} ký tự", code="invalid_request"
        )
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in v):
        raise DecisionError(f"{field_name} chứa ký tự điều khiển", code="invalid_request")
    return v


def _is_date(v: object) -> bool:
    if not isinstance(v, str) or not DATE_RE.match(v):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


class ProcessRuntime:
    """Runtime của MỘT định nghĩa (process_id, version) đã compile."""

    def __init__(
        self,
        compiled: CompiledProcess,
        engine: ProcessEngine,
        services: ServiceRegistry,
        org: OrgDirectory,
        *,
        admin_role: str | None = None,
        on_event: EventSink | None = None,
        scheduler: TimerScheduler | None = None,
    ):
        self.compiled = compiled
        self.engine = engine
        self.services = services
        self.org = org
        self.resolver = AssigneeResolver(org)
        self.admin_role = admin_role
        self.on_event: EventSink = on_event or (lambda ev: None)
        self.scheduler = scheduler
        m = compiled.manifest
        fb = m["fallback_assignee"]
        self.fallback = Assignee.model_validate(fb) if fb else None
        self.timer = m["trigger"]["kind"] == "timer"

    @property
    def manifest(self) -> dict[str, Any]:
        return self.compiled.manifest

    def user(self, uid: object) -> OrgUser | None:
        return self.org.get_user(uid)

    def is_active(self, uid: object) -> bool:
        u = self.org.get_user(uid)
        return bool(u and u.active)

    # ---------- input (G-P8: đầu vào được khai báo và kiểm kiểu)
    def check_value(self, name: str, spec: Mapping[str, Any], v: object) -> None:
        t = spec["type"]
        num = isinstance(v, int | float) and not isinstance(v, bool)
        good = {
            "str": lambda: isinstance(v, str) and len(v) <= MAX_COMMENT,
            "int": lambda: isinstance(v, int) and not isinstance(v, bool) and abs(v) < MAX_NUMBER,
            "number": lambda: num and abs(v) < MAX_NUMBER,  # type: ignore[arg-type]
            "bool": lambda: isinstance(v, bool),
            "user": lambda: self.org.get_user(v) is not None,
            "department": lambda: self.org.department_exists(v),
            "date": lambda: _is_date(v),
            "object": lambda: isinstance(v, dict | list) and len(repr(v)) <= MAX_OBJECT_REPR,
        }[t]()
        if not good:
            raise DecisionError(
                f"input {name}={v!r} không hợp lệ cho kiểu {t}", code="invalid_request"
            )

    def validate_inputs(
        self, data: object, *, partial: bool = False, only_editable: bool = False
    ) -> dict[str, Any]:
        inputs = self.manifest["inputs"]
        if not isinstance(data, dict):
            raise DecisionError("dữ liệu phải là object", code="invalid_request")
        for k, v in data.items():
            if k not in inputs:
                raise DecisionError(
                    f"input lạ {k!r}; chỉ chấp nhận {sorted(inputs)}", code="invalid_request"
                )
            if only_editable and not inputs[k]["editable_on_revision"]:
                raise DecisionError(
                    f"input {k!r} không được sửa khi trả lại bổ sung", code="invalid_request"
                )
            self.check_value(k, inputs[k], v)
        if not partial:
            miss = [k for k, s in inputs.items() if s["required"] and k not in data]
            if miss:
                raise DecisionError(f"thiếu input bắt buộc {miss}", code="invalid_request")
        return dict(data)

    # ---------- lifecycle
    def start(
        self,
        data: Mapping[str, Any] | None,
        requester: str | None = None,
        *,
        instance_id: str | None = None,
        fire: Mapping[str, Any] | None = None,
    ) -> ProcessInstance:
        values = self.validate_inputs(dict(data or {}))
        for k, s in self.manifest["inputs"].items():  # AUD-3: mọi input đều có mặt khi chạy
            if k not in values:
                values[k] = s.get("default")
        if self.timer:
            if fire is None or requester is not None:
                raise DecisionError(
                    "quy trình định kỳ chỉ được khởi tạo bởi poller (có fire, không có requester)"
                )
            trig = self.compiled.spec.trigger
            assert isinstance(trig, TimerTrigger)
            validate_fire(trig, fire)
            values.update({"xt_fire_at": fire["fire_at"], "xt_period_key": fire["period_key"]})
        else:
            if fire is not None:
                raise DecisionError("quy trình thủ công không nhận fire")
            if not self.is_active(requester):
                raise DecisionError(
                    f"người đề nghị {requester!r} không hợp lệ hoặc đã nghỉ", code="access_denied"
                )
            values["requester"] = requester
        iid = instance_id or str(uuid.uuid4())
        try:
            wf = self.engine.start(
                self.compiled, instance_id=iid, data=values, services=self.services
            )
        except Exception as e:
            raise EngineError(f"engine lỗi khi khởi tạo: {type(e).__name__}: {e}") from e
        m = self.manifest
        meta: dict[str, Any] = {
            "id": iid, "process_id": m["process_id"], "version": m["version"],
            "spec_sha256": m["spec_sha256"], "requester": requester, "assign": {},
            "history": [], "actors": {}, "returns": {}, "overrides": {}, "events": [],
            "editors": [], "loops": {}, "last_candidates": {},
        }  # fmt: skip
        inst = ProcessInstance(self, wf, meta)
        inst.refresh_assignments()
        inst._after_change()
        return inst

    def load(self, state: Mapping[str, Any]) -> ProcessInstance:
        """Nạp instance từ state JSON; từ chối state của định nghĩa/phiên bản khác."""
        if not isinstance(state, Mapping) or not isinstance(state.get("meta"), Mapping):
            raise DecisionError("state không hợp lệ", code="invalid_request")
        meta = copy.deepcopy(dict(state["meta"]))
        m = self.manifest
        mine = (m["process_id"], m["version"], m["spec_sha256"])
        if (meta.get("process_id"), meta.get("version"), meta.get("spec_sha256")) != mine:
            raise DecisionError("state không thuộc định nghĩa quy trình/phiên bản này")
        wf = self._load_wf(state["wf"], meta["id"])
        return ProcessInstance(self, wf, meta)

    def _load_wf(self, wf_state: Mapping[str, Any], instance_id: str) -> WorkflowHandle:
        return self.engine.load(
            self.compiled, wf_state, instance_id=instance_id, services=self.services
        )


class ProcessInstance:
    def __init__(self, rt: ProcessRuntime, wf: WorkflowHandle, meta: dict[str, Any]):
        self.rt = rt
        self.wf = wf
        self.meta = meta
        self._pending: list[dict[str, Any]] = []

    @property
    def id(self) -> str:
        return str(self.meta["id"])

    # ---------- state
    def to_state(self) -> dict[str, Any]:
        return {"wf": self.wf.to_state(), "meta": copy.deepcopy(self.meta)}

    def status(self) -> str:
        """running | completed | rejected | withdrawn."""
        if self.meta.get("withdrawn"):  # người đề nghị rút khi đang chạy (wf đã cancel)
            return "withdrawn"
        if not self.wf.is_completed():
            return "running"
        if "end_rejected" in self.wf.completed_task_names():
            hist = self.meta["history"]
            return "withdrawn" if hist and hist[-1]["outcome"] == "withdraw" else "rejected"
        return "completed"

    def data(self) -> dict[str, Any]:
        return self.wf.data()

    def candidates(self) -> list[str]:
        """Mọi ứng viên của các task đang mở (để lập chỉ mục inbox)."""
        return sorted({u for a in self.meta["assign"].values() for u in a["candidates"]})

    # ---------- assignment
    def _manifest_task(self, name: str) -> dict[str, Any]:
        tasks: dict[str, Any] = self.rt.manifest["user_tasks"]
        if name not in tasks:  # BPMN và manifest lệch nhau: fail closed
            raise EngineError(f"task {name!r} không có trong manifest")
        return dict(tasks[name])

    def _exclude(self, ut: Mapping[str, Any]) -> dict[str, str]:
        ex: dict[str, str] = {}
        sod = ut.get("sod") or {}
        requester = self.meta["requester"]
        if sod.get("not_requester") and requester:
            ex[requester] = "người đề nghị không tự xử lý"
        distinct = self.rt.manifest.get("distinct", {}).get(ut.get("step"), [])
        for s in sorted(set(sod.get("distinct_from", [])) | set(distinct)):
            for a in self.meta["actors"].get(s, []):
                ex[a] = f"đã làm bước {s}"
        if sod.get("not_requester"):
            for e in self.meta.get("editors", []):
                ex.setdefault(e, "đã sửa dữ liệu đề nghị")
        return ex

    def _emit(self, ev: dict[str, Any]) -> None:
        self.meta["events"].append(ev)
        self._pending.append(ev)

    def _dispatch(self, *, required: bool = False) -> None:
        """Sự kiện đã nằm trong meta (outbox); gửi best-effort sau khi state nhất quán."""
        evs, self._pending = self._pending, []
        for ev in evs:
            try:
                self.rt.on_event(ev)
            except Exception as e:
                if required:
                    raise
                self.meta["events"].append(
                    {"type": "event.delivery_failed", "of": ev["type"],
                     "error": f"{type(e).__name__}: {e}"[:200]}
                )  # fmt: skip

    def _assign(self, t: UserTask, m: Mapping[str, Any]) -> Assignment:
        name = t.name
        ov = self.meta["overrides"].pop(name, None)
        if ov:
            ex = self._exclude(m)
            ok = [u for u in ov["users"] if self.rt.is_active(u) and u not in ex]
            if ok == ov["users"]:
                return Assignment(list(ov["users"]), ov["reason"], "override")
        if m["kind"] == "revise":
            # chỉ người đề nghị được sửa dữ liệu của mình; nghỉ -> unresolved, không giao người khác
            req = self.meta["requester"]
            if self.rt.is_active(req):
                return Assignment([req], "người đề nghị sửa và nộp lại", "primary")
            return Assignment(
                [], "người đề nghị không còn hoạt động; chờ quản trị (chỉ được rút)", "unresolved"
            )
        ex = self._exclude(m)
        target = Assignee.model_validate(m["assignee"])
        a = self.rt.resolver.resolve(target, t.data, ex, self.rt.fallback)
        prev = self.meta["last_candidates"].get(f"ut_{m['step']}")
        if m.get("escalated") and prev and set(a.candidates) == set(prev):
            # leo thang về đúng người đang giữ việc quá hạn là vô nghĩa -> đi tiếp chuỗi fallback
            ex = {**ex, **{u: "đang giữ việc bị quá hạn" for u in prev}}
            a = self.rt.resolver.resolve(target, t.data, ex, self.rt.fallback)
        return a

    def refresh_assignments(self) -> None:
        """Bỏ assignment của task đã đóng; giải người nhận cho task mới READY."""
        ready = {t.id: t for t in self.wf.ready_user_tasks()}
        for k in list(self.meta["assign"]):
            if k not in ready:
                del self.meta["assign"][k]
        for tid, t in ready.items():
            stale = False
            cur = self.meta["assign"].get(tid)
            if cur is not None:
                cands = cur["candidates"]
                if not cands or any(self.rt.is_active(u) for u in cands):
                    continue
                # AUD-8: mọi ứng viên đã nghỉ -> giải lại (đi tiếp chuỗi fallback), có sự kiện
                stale = True
            m = self._manifest_task(t.name)
            if m.get("escalated"):  # đã leo thang: bỏ override còn treo của ut_X / ri_ut_X
                base = f"ut_{m['step']}"
                self.meta["overrides"].pop(base, None)
                self.meta["overrides"].pop(f"ri_{base}", None)
            a = self._assign(t, m)
            if stale:
                a.trail.insert(
                    0, f"ứng viên cũ {cur['candidates'] if cur else []} không còn hoạt động"
                )
            if m["kind"] in DECISION_KINDS and not m.get("escalated"):
                self.meta["last_candidates"][t.name] = list(a.candidates)
            self.meta["assign"][tid] = {
                "spec": t.name,
                "step": m["step"],
                "kind": m["kind"],
                **a.to_dict(),
            }
            if stale or a.via in ("fallback", "process_fallback", "unresolved"):
                kind = "assignment.unresolved" if a.via == "unresolved" else "assignment.escalated"
                self._emit(
                    {"type": kind, "instance": self.id, "task": t.name,
                     "candidates": list(a.candidates), "trail": list(a.trail)}
                )  # fmt: skip

    def _after_change(self) -> None:
        self._dispatch()
        if self.rt.scheduler is not None:
            due = self.wf.next_due()
            if due is not None:
                self.rt.scheduler.wake_at(
                    process_id=self.rt.manifest["process_id"], instance_id=self.id, due=due
                )

    def inbox(self, user: str | None = None) -> list[dict[str, Any]]:
        out = []
        for t in self.wf.ready_user_tasks():
            a = self.meta["assign"].get(t.id)
            if a is None:
                continue
            if user is None or user in a["candidates"]:
                m = self._manifest_task(t.name)
                out.append(
                    {"instance": self.id, "task_id": t.id, "spec": t.name, "step": m["step"],
                     "kind": m["kind"], "name": m["name"], "lane": t.lane,
                     "outcomes": list(m["outcomes"]),
                     **{k: copy.deepcopy(a[k]) for k in ("candidates", "reason", "via", "trail")}}
                )  # fmt: skip
        return out

    def _task(self, task_id: object) -> UserTask:
        for t in self.wf.ready_user_tasks():
            if t.id == str(task_id):
                return t
        raise DecisionError(f"task {task_id} không ở trạng thái chờ xử lý", reason="task_not_ready")

    def _active_user(self, uid: object, what: str) -> OrgUser:
        u = self.rt.user(uid)
        if not u or not u.active:
            raise DecisionError(
                f"{what} {uid!r} không tồn tại hoặc không còn hoạt động", code="access_denied"
            )
        return u

    def reassign(self, task_id: str, admin: str, to_user: str, reason: str) -> None:
        """Quản trị gán lại task (kể cả `unresolved`); vẫn qua SoD; không gán lại bước sửa."""
        if not self.rt.admin_role:
            raise DecisionError("runtime chưa cấu hình vai trò quản trị", code="access_denied")
        a = self._active_user(admin, "quản trị")
        if self.rt.admin_role not in a.roles:
            raise DecisionError(
                f"{admin} không có vai trò {self.rt.admin_role}", code="access_denied"
            )
        t = self._task(task_id)
        m = self._manifest_task(t.name)
        if m["kind"] == "revise":
            raise DecisionError(
                "không gán lại bước sửa đề nghị cho người khác "
                "(người khác không được sửa dữ liệu của người đề nghị)",
                code="access_denied",
            )
        self._active_user(to_user, "người nhận")
        if to_user in self._exclude(m):
            raise DecisionError(f"{to_user} bị loại do SoD", code="access_denied", reason="sod")
        _text(reason, "lý do", required=True)
        self.meta["assign"][t.id].update(
            {
                "candidates": [to_user],
                "reason": f"quản trị {admin} gán lại: {reason}",
                "via": "admin",
            }
        )
        self.meta["history"].append(
            {
                "task": t.name,
                "step": m["step"],
                "actor": admin,
                "outcome": "reassign",
                "to": to_user,
            }
        )

    def _delegate_target(self, m: Mapping[str, Any], actor: str, delegate_to: str | None) -> None:
        """Đích ủy quyền hợp lệ: người khác, đang hoạt động, không bị SoD của bước, không phải
        người đề nghị (bước quyết định). Dùng chung cho `check_delegate` (lúc plan) và `act`."""
        if not delegate_to or delegate_to == actor:
            raise DecisionError(
                "ủy quyền cần delegate_to là người khác",
                code="invalid_request",
                reason="invalid_delegate",
            )
        if m["kind"] in DECISION_KINDS and delegate_to == self.meta["requester"]:
            raise DecisionError(
                "không ủy quyền bước duyệt cho chính người đề nghị",
                code="access_denied",
                reason="sod",
            )
        u = self.rt.user(delegate_to)
        if u is None or not u.active:
            raise DecisionError(
                "người được ủy quyền không tồn tại hoặc không còn hoạt động",
                code="invalid_request",
                reason="invalid_delegate",
            )
        ex = self._exclude(m)
        if delegate_to in ex:
            raise DecisionError(
                f"không ủy quyền được cho {delegate_to}: {ex[delegate_to]}",
                code="access_denied",
                reason="sod",
            )

    def check_delegate(self, task_id: str, actor: str, delegate_to: str | None) -> None:
        """Kiểm trước (không đổi state) một lời ủy quyền: như nhánh `delegate` của `act`. Gọi lúc
        plan để lỗi hiện ngay, trước khi người duyệt phải step-up xác nhận (WA-03)."""
        t = self._task(task_id)
        m = self._manifest_task(t.name)
        a = self.meta["assign"].get(t.id) or {}
        if actor not in a.get("candidates", ()):
            raise DecisionError(
                f"{actor!r} không phải người được giao bước '{m['name']}'",
                code="access_denied",
                reason="not_assigned_approver",
            )
        if "delegate" not in m["outcomes"]:
            raise DecisionError(f"bước '{m['name']}' không cho ủy quyền", code="invalid_request")
        if self.meta["loops"].get(m["step"], 0) >= MAX_LOOPS_PER_STEP:
            raise DecisionError(
                f"bước '{m['name']}' đã ủy quyền/hỏi thêm {MAX_LOOPS_PER_STEP} lần; phải quyết định"
            )
        self._delegate_target(m, actor, delegate_to)

    def check_actor(self, task_id: str, actor: str) -> None:
        """Kiểm trước (không đổi state) rằng `actor` là người được giao task và không vướng SoD
        của bước: plan của transition_record báo lỗi trước khi tạo PendingOperation."""
        t = self._task(task_id)
        m = self._manifest_task(t.name)
        a = self.meta["assign"].get(t.id) or {}
        if actor not in a.get("candidates", ()):
            raise DecisionError(
                f"{actor!r} không phải người được giao bước '{m['name']}'",
                code="access_denied",
                reason="not_assigned_approver",
            )
        if m["kind"] in DECISION_KINDS and actor in self._exclude(m):
            raise DecisionError(
                f"{actor} bị loại do SoD ở bước '{m['name']}'", code="access_denied", reason="sod"
            )

    def withdraw(self, actor: str, reason: str | None = None) -> None:
        """Người đề nghị rút đề nghị đang chạy (kể cả bước đang kẹt): hủy mọi task mở, trạng thái
        `withdrawn`. Đề nghị bị trả lại thì rút qua task sửa (`act(..., "withdraw")`)."""
        if self.status() != "running":
            raise DecisionError("quy trình đã kết thúc, không rút được", reason="invalid_state")
        if actor != self.meta["requester"]:
            raise DecisionError(
                "chỉ người đề nghị được rút đề nghị", code="access_denied", reason="not_owner"
            )
        comment = _text(reason, "lý do rút")
        snapshot = self.wf.to_state()
        try:
            self.wf.cancel()
        except Exception as e:
            self.wf = self.rt._load_wf(snapshot, self.id)
            raise self._incident(None, actor, e, "engine lỗi khi rút đề nghị") from e
        self.meta["withdrawn"] = {"actor": actor, "comment": comment}
        self.meta["assign"] = {}
        self.meta["overrides"] = {}
        self.meta["history"].append(
            {"task": None, "step": None, "actor": actor, "outcome": "withdraw", "comment": comment}
        )
        self._emit({"type": "instance.withdrawn", "instance": self.id, "actor": actor})
        self._after_change()

    # ---------- act
    def act(
        self,
        task_id: str,
        actor: str,
        outcome: object,
        comment: str | None = None,
        *,
        delegate_to: str | None = None,
        ask: str | None = None,
        updates: Mapping[str, Any] | None = None,
    ) -> None:
        t = self._task(task_id)
        m = self._manifest_task(t.name)
        a = self.meta["assign"][t.id]
        step, kind = m["step"], m["kind"]
        if not self.rt.is_active(actor):
            raise DecisionError(
                f"{actor!r} không tồn tại hoặc không còn hoạt động", code="access_denied"
            )
        actor_user = self.rt.user(actor)
        admin_withdraw = (  # AUD-9: bước sửa không ai nhận -> quản trị được RÚT (không được sửa)
            kind == "revise"
            and outcome == "withdraw"
            and a["via"] == "unresolved"
            and bool(self.rt.admin_role)
            and actor_user is not None
            and self.rt.admin_role in actor_user.roles
        )
        if admin_withdraw:
            _text(comment, "lý do rút", required=True)
        if actor not in a["candidates"] and not admin_withdraw:
            raise DecisionError(
                f"{actor!r} không phải người được giao bước '{m['name']}' "
                f"(người được giao: {a['candidates'] or 'chưa có'})",
                code="access_denied",
                reason="not_assigned_approver",
            )
        if not isinstance(outcome, str) or outcome not in m["outcomes"]:
            raise DecisionError(
                f"kết quả {outcome!r} không hợp lệ cho bước '{m['name']}'; "
                f"chỉ chấp nhận {m['outcomes']}",
                code="invalid_request",
            )
        if delegate_to is not None and outcome != "delegate":
            raise DecisionError("delegate_to chỉ dùng với outcome delegate", code="invalid_request")
        if ask is not None and outcome != "request_info":
            raise DecisionError("ask chỉ dùng với outcome request_info", code="invalid_request")
        if updates is not None and outcome != "resubmit":
            raise DecisionError("updates chỉ dùng khi nộp lại (resubmit)", code="invalid_request")
        loops = self.meta["loops"]
        if outcome in ("delegate", "request_info") and loops.get(step, 0) >= MAX_LOOPS_PER_STEP:
            raise DecisionError(
                f"bước '{m['name']}' đã ủy quyền/hỏi thêm {MAX_LOOPS_PER_STEP} lần; phải quyết định"
            )
        comment = _text(comment, "ghi chú")
        data: dict[str, Any] = {}
        clear_from: str | None = None
        overrides: dict[str, Any] = {}
        if kind in DECISION_KINDS:
            ex = self._exclude(m)
            if actor in ex:  # kiểm lại lúc hành động (dữ liệu có thể đã đổi)
                raise DecisionError(
                    f"{actor} bị loại do SoD ở bước '{m['name']}'",
                    code="access_denied",
                    reason="sod",
                )
            if outcome == "return_for_revision":
                _text(comment, "lý do trả lại", required=True)
                n = self.meta["returns"].get(step, 0)
                if n >= m["on_return"]["max_rounds"]:
                    raise DecisionError(
                        f"đã trả lại {n} lần (tối đa {m['on_return']['max_rounds']}); "
                        "phải duyệt hoặc từ chối"
                    )
                if m["on_return"].get("to_step"):
                    clear_from = m["on_return"]["to_step"]
            elif outcome == "delegate":
                self._delegate_target(m, actor, delegate_to)
                assert delegate_to is not None
                overrides[t.name] = {"users": [delegate_to], "reason": f"được {actor} ủy quyền"}
            elif outcome == "request_info":
                _text(comment, "câu hỏi", required=True)
                ask = ask or self.meta["requester"]
                if not ask or ask == actor:
                    raise DecisionError(
                        "cần chỉ rõ người được hỏi (khác người hỏi)", code="invalid_request"
                    )
                self._active_user(ask, "người được hỏi")
                overrides[f"ri_{t.name}"] = {
                    "users": [ask],
                    "reason": f"{actor} hỏi thêm thông tin",
                }
                overrides[t.name] = {
                    "users": [actor], "reason": f"trả về {actor} sau khi bổ sung thông tin"
                }  # fmt: skip
            data = {f"{step}_decision": outcome, f"{step}_comment": comment, f"{step}_actor": actor}
        elif kind == "info":
            _text(comment, "thông tin bổ sung", required=True)
            data = {f"{step}_info": comment}
        elif kind == "revise":
            if updates:
                if m["resume"] == "same_step":
                    raise DecisionError(
                        "resume same_step không cho sửa input", code="invalid_request"
                    )
                data.update(
                    self.rt.validate_inputs(dict(updates), partial=True, only_editable=True)
                )
            data[f"{step}_revise"] = outcome
            if outcome == "resubmit":
                clear_from = self.rt.manifest["step_order"][0] if m["resume"] == "restart" else step
        else:  # pragma: no cover - manifest do compiler sinh
            raise EngineError(f"loại task {kind!r} không hỗ trợ")
        remove: list[str] = []
        if clear_from:
            order: list[str] = self.rt.manifest["step_order"]
            remove = [
                f"{s}{suf}"
                for s in order[order.index(clear_from) :]
                for suf in STEP_SUFFIXES
                if f"{s}{suf}" not in data
            ]
        snapshot = self.wf.to_state()
        try:
            self.wf.complete(t.id, data, remove)
        except Exception as e:
            self.wf = self.rt._load_wf(snapshot, self.id)
            retry = {
                "operation": "act",
                "task_id": t.id,
                "actor": actor,
                "outcome": outcome,
                "comment": comment,
                "kwargs": {
                    k: copy.deepcopy(v)
                    for k, v in {
                        "delegate_to": delegate_to,
                        "ask": ask,
                        "updates": dict(updates) if updates is not None else None,
                    }.items()
                    if v is not None
                },
            }
            raise self._incident(
                t.name, actor, e, "engine lỗi, đã khôi phục state", retry=retry
            ) from e
        self.meta["overrides"].update(overrides)
        returned = (kind == "revise" and outcome == "resubmit") or (
            kind in DECISION_KINDS
            and outcome == "return_for_revision"
            and bool(m["on_return"].get("to_step"))
        )
        if returned:
            self.meta["returns"][step] = self.meta["returns"].get(step, 0) + 1
        if kind in DECISION_KINDS:
            self.meta["actors"].setdefault(step, []).append(actor)
        if outcome in ("delegate", "request_info"):
            loops[step] = loops.get(step, 0) + 1
        if kind == "revise" and updates and actor not in self.meta["editors"]:
            self.meta["editors"].append(actor)
        entry: dict[str, Any] = {
            "task": t.name, "step": step, "actor": actor, "outcome": outcome, "comment": comment
        }  # fmt: skip
        if delegate_to:
            entry["delegate_to"] = delegate_to
        if ask:
            entry["ask"] = ask
        if updates:
            entry["updates"] = dict(updates)
        self.meta["history"].append(entry)
        for incident in self.meta.get("incidents", []):
            replay = incident.get("retry") or {}
            if (
                incident.get("status") == "open"
                and isinstance(replay, Mapping)
                and replay.get("task_id") == t.id
            ):
                incident.update(status="resolved", resolved_by=actor)
        self.refresh_assignments()
        self._after_change()

    def _incident(
        self,
        task: str | None,
        actor: str | None,
        err: BaseException,
        what: str,
        *,
        retry: dict[str, Any] | None = None,
    ) -> EngineError:
        """Ghi sự cố vào meta (quản trị thấy qua `incidents` + sự kiện `engine.error`); wf đã
        được khôi phục. Người gọi lưu meta rồi ném lại (ProcessService làm việc này)."""
        msg = f"{type(err).__name__}: {err}"[:500]
        inc = {
            "id": str(uuid.uuid4()),
            "status": "open",
            "task": task,
            "actor": actor,
            "error": msg,
            "attempts": 0,
            **_failure_details(err),
        }
        if retry is not None:
            inc["retry"] = retry
        self.meta.setdefault("incidents", []).append(inc)
        self._emit({"type": "engine.error", "instance": self.id, **inc})
        self._dispatch()
        return EngineError(f"{what}: {msg}", reason="engine_incident", incident=copy.deepcopy(inc))

    def tick(self) -> None:
        """Đánh giá timer (SLA/timer start). Lỗi -> khôi phục state, ném EngineError."""
        snapshot = self.wf.to_state()
        try:
            self.wf.refresh_timers()
        except Exception as e:
            self.wf = self.rt._load_wf(snapshot, self.id)
            raise self._incident(
                None, None, e, "engine lỗi khi chạy timer", retry={"operation": "tick"}
            ) from e
        self.refresh_assignments()
        self._after_change()
