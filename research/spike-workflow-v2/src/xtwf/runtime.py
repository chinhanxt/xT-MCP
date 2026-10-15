# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Runtime instance: giao việc động, kiểm outcome (fail closed), SoD theo bước, vòng trả lại/ủy quyền/hỏi thông tin,
lưu/khôi phục JSON (có _reorder). Mỗi act là nguyên tử: lỗi engine -> khôi phục snapshot trước đó."""
from __future__ import annotations

import copy
import re
import unicodedata
import uuid
from datetime import date

from SpiffWorkflow.util.task import TaskState

from .compiler import Compiled
from .engine import from_state, new_workflow, ready_user_tasks, safe_engine, to_state
from .org import AssigneeResolver, Assignment, OrgDirectory
from .spec import Assignee

MAX_COMMENT = 2000
MAX_LOOPS_PER_STEP = 5      # delegate + request_info mỗi bước; chặn "chuyền bóng" vô hạn làm SLA khởi động lại mãi
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DecisionError(ValueError):
    """Hành động bị từ chối trước khi chạm engine; state không đổi."""


class EngineError(RuntimeError):
    """Engine lỗi khi chạy; state đã được khôi phục về trước hành động."""


def _text(v, field_name, required=False):
    if v is None or v == "":
        if required:
            raise DecisionError(f"cần {field_name}")
        return None
    if not isinstance(v, str) or len(v) > MAX_COMMENT:
        raise DecisionError(f"{field_name} phải là chuỗi ≤ {MAX_COMMENT} ký tự")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in v):
        raise DecisionError(f"{field_name} chứa ký tự điều khiển")
    return v


class Runtime:
    def __init__(self, compiled: Compiled, services: dict, org: OrgDirectory, admin_role: str | None = None, on_event=None):
        self.c, self.services, self.org = compiled, services, org
        self.resolver = AssigneeResolver(org)
        self.admin_role = admin_role
        self.on_event = on_event or (lambda ev: None)
        m = compiled.manifest
        self.fallback = Assignee.model_validate(m["fallback_assignee"]) if m["fallback_assignee"] else None
        self.timer = m["trigger"]["kind"] == "timer"

    # ---------- input validation (G-P8: đầu vào được khai báo và kiểm kiểu)
    def check_value(self, name, spec, v):
        t = spec["type"]
        ok = {"str": lambda: isinstance(v, str) and len(v) <= MAX_COMMENT,
              "int": lambda: isinstance(v, int) and not isinstance(v, bool) and abs(v) < 10 ** 15,
              "number": lambda: isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 10 ** 15,
              "bool": lambda: isinstance(v, bool),
              "user": lambda: self.org.get_user(v) is not None,
              "department": lambda: self.org.department_exists(v),
              "date": lambda: isinstance(v, str) and bool(DATE_RE.match(v)) and bool(date.fromisoformat(v)),
              "object": lambda: isinstance(v, (dict, list)) and len(repr(v)) <= 16384}[t]
        try:
            good = ok()
        except ValueError:
            good = False
        if not good:
            raise DecisionError(f"input {name}={v!r} không hợp lệ cho kiểu {t}")

    def validate_inputs(self, data: dict, partial=False, only_editable=False):
        inputs = self.c.manifest["inputs"]
        if not isinstance(data, dict):
            raise DecisionError("dữ liệu phải là object")
        for k, v in data.items():
            if k not in inputs:
                raise DecisionError(f"input lạ {k!r}; chỉ chấp nhận {sorted(inputs)}")
            if only_editable and not inputs[k]["editable_on_revision"]:
                raise DecisionError(f"input {k!r} không được sửa khi trả lại bổ sung")
            self.check_value(k, inputs[k], v)
        if not partial:
            miss = [k for k, s in inputs.items() if s["required"] and k not in data]
            if miss:
                raise DecisionError(f"thiếu input bắt buộc {miss}")

    def start(self, data: dict, requester: str | None = None, instance_id: str | None = None, fire: dict | None = None) -> "Instance":
        data = dict(data or {})
        self.validate_inputs(data)
        if self.timer:
            if fire is None or requester is not None:
                raise DecisionError("quy trình định kỳ chỉ được khởi tạo bởi poller (có fire, không có requester)")
            from .timers import validate_fire   # tránh vòng import
            validate_fire(self.c.spec.trigger, fire)
            data.update({"xt_fire_at": fire["fire_at"], "xt_period_key": fire["period_key"]})
        else:
            u = self.org.get_user(requester)
            if not u or not u.get("active"):
                raise DecisionError(f"người đề nghị {requester!r} không hợp lệ hoặc đã nghỉ")
            data["requester"] = requester
        iid = instance_id or str(uuid.uuid4())
        try:
            wf = new_workflow(self.c.bpmn, self.c.process_id, safe_engine(self.services, iid), data)
        except Exception as e:
            raise EngineError(f"engine lỗi khi khởi tạo: {type(e).__name__}: {e}") from e
        m = self.c.manifest
        inst = Instance(self, wf, {"id": iid, "process_id": m["process_id"], "version": m["version"], "spec_sha256": m["spec_sha256"],
                                   "requester": requester, "assign": {}, "history": [], "actors": {}, "returns": {},
                                   "overrides": {}, "events": [], "editors": [], "loops": {}})
        inst._sync()
        inst._dispatch()
        return inst

    def load(self, state: dict) -> "Instance":
        meta = copy.deepcopy(state["meta"])
        m = self.c.manifest
        if (meta.get("process_id"), meta.get("version"), meta.get("spec_sha256")) != (m["process_id"], m["version"], m["spec_sha256"]):
            raise DecisionError("state không thuộc định nghĩa quy trình/phiên bản này")
        return Instance(self, from_state(state["wf"], safe_engine(self.services, meta["id"])), meta)


class Instance:
    def __init__(self, rt: Runtime, wf, meta):
        self.rt, self.wf, self.meta = rt, wf, meta

    # ---------- state
    def to_state(self) -> dict:
        return {"wf": to_state(self.wf), "meta": copy.deepcopy(self.meta)}

    def status(self) -> str:
        if not self.wf.is_completed():
            return "running"
        done = {t.task_spec.name for t in self.wf.get_tasks(state=TaskState.COMPLETED)}
        if "end_rejected" in done:
            last = self.meta["history"][-1]["outcome"] if self.meta["history"] else None
            return "withdrawn" if last == "withdraw" else "rejected"
        return "completed"

    def data(self) -> dict:
        if self.wf.is_completed():
            return dict(self.wf.data)
        tasks = self.wf.get_tasks(state=TaskState.READY)
        return dict(tasks[-1].data) if tasks else {}

    # ---------- assignment
    def _exclude(self, ut_meta, data):
        ex = {}
        sod = ut_meta.get("sod") or {}
        if sod.get("not_requester") and self.meta["requester"]:
            ex[self.meta["requester"]] = "người đề nghị không tự xử lý"
        others = set(sod.get("distinct_from", [])) | set(self.rt.c.manifest.get("distinct", {}).get(ut_meta.get("step"), []))
        for s in sorted(others):
            for a in self.meta["actors"].get(s, []):
                ex[a] = f"đã làm bước {s}"
        if sod.get("not_requester"):
            for e in self.meta.get("editors", []):
                ex.setdefault(e, "đã sửa dữ liệu đề nghị")
        return ex

    def _dispatch(self):
        """Sự kiện đã nằm trong meta (outbox); gửi best-effort sau khi state đã nhất quán, lỗi gửi không làm hỏng act."""
        evs, self._pending_events = self._pending_events, []
        for ev in evs:
            try:
                self.rt.on_event(ev)
            except Exception as e:  # noqa: BLE001
                self.meta["events"].append({"type": "event.delivery_failed", "of": ev["type"], "error": f"{type(e).__name__}: {e}"[:200]})

    def _sync(self):
        if not hasattr(self, "_pending_events"):
            self._pending_events = []
        ready = {str(t.id): t for t in ready_user_tasks(self.wf)}
        for k in list(self.meta["assign"]):
            if k not in ready:
                del self.meta["assign"][k]
        for tid, t in ready.items():
            if tid in self.meta["assign"]:
                continue
            name = t.task_spec.name
            m = self.rt.c.manifest["user_tasks"][name]
            ov = self.meta["overrides"].pop(name, None)
            if ov:
                ok = [u for u in ov["users"] if (self.rt.org.get_user(u) or {}).get("active") and u not in self._exclude(m, t.data)]
                ov = ov if ok == ov["users"] else None
            if ov:
                a = Assignment(ov["users"], ov["reason"], "override")
            elif m["kind"] == "revise":
                # chỉ người đề nghị được sửa dữ liệu của mình; nghỉ việc -> unresolved (quản trị quyết), không giao người khác sửa
                req = self.meta["requester"]
                u = self.rt.org.get_user(req)
                a = (Assignment([req], "người đề nghị sửa và nộp lại", "primary") if u and u.get("active") else
                     Assignment([], "người đề nghị không còn hoạt động; chờ quản trị (chỉ được rút)", "unresolved"))
            else:
                ex = self._exclude(m, t.data)
                a = self.rt.resolver.resolve(Assignee.model_validate(m["assignee"]), t.data, ex, self.rt.fallback)
                prev = self.meta.setdefault("last_candidates", {}).get(f"ut_{m['step']}")
                if m.get("escalated") and prev and set(a.candidates) == set(prev):
                    # leo thang về đúng người đang giữ việc quá hạn là vô nghĩa -> đi tiếp chuỗi fallback
                    ex = {**ex, **{u: "đang giữ việc bị quá hạn" for u in prev}}
                    a = self.rt.resolver.resolve(Assignee.model_validate(m["assignee"]), t.data, ex, self.rt.fallback)
            if m["kind"] in ("decision", "task") and not m.get("escalated"):
                self.meta.setdefault("last_candidates", {})[name] = a.candidates
            self.meta["assign"][tid] = {"spec": name, "step": m["step"], "kind": m["kind"], **a.to_dict()}
            if a.via in ("fallback", "process_fallback", "unresolved"):
                ev = {"type": "assignment.unresolved" if a.via == "unresolved" else "assignment.escalated", "task": name,
                      "candidates": a.candidates, "trail": a.trail}
                self.meta["events"].append(ev)
                self._pending_events.append(ev)

    def inbox(self, user: str | None = None) -> list[dict]:
        out = []
        for t in ready_user_tasks(self.wf):
            a = self.meta["assign"][str(t.id)]
            if user is None or user in a["candidates"]:
                m = self.rt.c.manifest["user_tasks"][t.task_spec.name]
                out.append({"task_id": str(t.id), "spec": t.task_spec.name, "step": m["step"], "kind": m["kind"], "name": m["name"],
                            "lane": t.task_spec.lane, "outcomes": m["outcomes"], **{k: a[k] for k in ("candidates", "reason", "via", "trail")}})
        return out

    def _task(self, task_id):
        for t in ready_user_tasks(self.wf):
            if str(t.id) == str(task_id):
                return t
        raise DecisionError(f"task {task_id} không ở trạng thái chờ xử lý")

    def _active_user(self, uid, what):
        u = self.rt.org.get_user(uid)
        if not u or not u.get("active"):
            raise DecisionError(f"{what} {uid!r} không tồn tại hoặc không còn hoạt động")
        return u

    def reassign(self, task_id, admin: str, to_user: str, reason: str):
        if not self.rt.admin_role:
            raise DecisionError("runtime chưa cấu hình vai trò quản trị")
        a = self._active_user(admin, "quản trị")
        if self.rt.admin_role not in a["roles"]:
            raise DecisionError(f"{admin} không có vai trò {self.rt.admin_role}")
        t = self._task(task_id)
        m = self.rt.c.manifest["user_tasks"][t.task_spec.name]
        if m["kind"] == "revise":
            raise DecisionError("không gán lại bước sửa đề nghị cho người khác (người khác không được sửa dữ liệu của người đề nghị)")
        self._active_user(to_user, "người nhận")
        if to_user in self._exclude(m, t.data):
            raise DecisionError(f"{to_user} bị loại do SoD")
        _text(reason, "lý do", required=True)
        self.meta["assign"][str(t.id)].update({"candidates": [to_user], "reason": f"quản trị {admin} gán lại: {reason}", "via": "admin"})
        self.meta["history"].append({"task": t.task_spec.name, "actor": admin, "outcome": "reassign", "to": to_user})

    # ---------- act
    def act(self, task_id, actor: str, outcome: str, comment: str | None = None, delegate_to: str | None = None,
            ask: str | None = None, updates: dict | None = None):
        t = self._task(task_id)
        name = t.task_spec.name
        m = self.rt.c.manifest["user_tasks"][name]
        a = self.meta["assign"][str(t.id)]
        step = m["step"]
        if not (self.rt.org.get_user(actor) or {}).get("active"):
            raise DecisionError(f"{actor!r} không tồn tại hoặc không còn hoạt động")
        if actor not in a["candidates"]:
            raise DecisionError(f"{actor!r} không phải người được giao bước '{m['name']}' (người được giao: {a['candidates'] or 'chưa có'})")
        if not isinstance(outcome, str) or outcome not in m["outcomes"]:
            raise DecisionError(f"kết quả {outcome!r} không hợp lệ cho bước '{m['name']}'; chỉ chấp nhận {m['outcomes']}")
        if delegate_to is not None and outcome != "delegate":
            raise DecisionError("delegate_to chỉ dùng với outcome delegate")
        if ask is not None and outcome != "request_info":
            raise DecisionError("ask chỉ dùng với outcome request_info")
        if updates is not None and outcome != "resubmit":
            raise DecisionError("updates chỉ dùng khi nộp lại (resubmit)")
        if outcome in ("delegate", "request_info") and self.meta.setdefault("loops", {}).get(step, 0) >= MAX_LOOPS_PER_STEP:
            raise DecisionError(f"bước '{m['name']}' đã ủy quyền/hỏi thêm {MAX_LOOPS_PER_STEP} lần; phải quyết định")
        comment = _text(comment, "ghi chú")
        data, clear_from = {}, None
        kind = m["kind"]
        if kind in ("decision", "task"):
            if actor in self._exclude(m, t.data):   # kiểm lại lúc hành động (dữ liệu có thể đã đổi)
                raise DecisionError(f"{actor} bị loại do SoD ở bước '{m['name']}'")
            if outcome == "return_for_revision":
                _text(comment, "lý do trả lại", required=True)
                n = self.meta["returns"].get(step, 0)
                if n >= m["on_return"]["max_rounds"]:
                    raise DecisionError(f"đã trả lại {n} lần (tối đa {m['on_return']['max_rounds']}); phải duyệt hoặc từ chối")
            elif outcome == "delegate":
                if not delegate_to or delegate_to == actor:
                    raise DecisionError("ủy quyền cần delegate_to là người khác")
                self._active_user(delegate_to, "người được ủy quyền")
                if delegate_to in self._exclude(m, t.data):
                    raise DecisionError(f"không ủy quyền được cho {delegate_to}: {self._exclude(m, t.data)[delegate_to]}")
                self.meta["overrides"][name] = {"users": [delegate_to], "reason": f"được {actor} ủy quyền"}
            elif outcome == "request_info":
                _text(comment, "câu hỏi", required=True)
                ask = ask or self.meta["requester"]
                if not ask or ask == actor:
                    raise DecisionError("cần chỉ rõ người được hỏi (khác người hỏi)")
                self._active_user(ask, "người được hỏi")
                self.meta["overrides"][f"ri_{name}"] = {"users": [ask], "reason": f"{actor} hỏi thêm thông tin"}
                self.meta["overrides"][name] = {"users": [actor], "reason": f"trả về {actor} sau khi bổ sung thông tin"}
            data = {f"{step}_decision": outcome, f"{step}_comment": comment, f"{step}_actor": actor}
            if outcome == "return_for_revision" and m["on_return"].get("to_step"):
                clear_from = m["on_return"]["to_step"]
        elif kind == "info":
            _text(comment, "thông tin bổ sung", required=True)
            data = {f"{step}_info": comment}
        elif kind == "revise":
            if updates:
                if m["resume"] == "same_step":
                    raise DecisionError("resume same_step không cho sửa input")
                self.rt.validate_inputs(updates, partial=True, only_editable=True)
                data.update(updates)
            data[f"{step}_revise"] = outcome
            if outcome == "resubmit":
                order = self.rt.c.manifest["step_order"]
                clear_from = order[0] if m["resume"] == "restart" else step
        snapshot = (to_state(self.wf), copy.deepcopy(self.meta))
        try:
            if clear_from:
                # Vòng lặp: xóa (không gán None) mọi biến do các bước từ điểm quay lại trở đi sinh ra. Join song song của
                # SpiffWorkflow gộp data kiểu "nhánh về sau ghi đè", nên giá trị cũ còn sót trước fork có thể đè giá trị mới.
                order = self.rt.c.manifest["step_order"]
                for s in order[order.index(clear_from):]:
                    for suf in ("_decision", "_comment", "_actor", "_info", "_result", "_revise"):
                        if f"{s}{suf}" not in data:
                            t.data.pop(f"{s}{suf}", None)
            t.data.update(data)
            t.run()
            self.wf.do_engine_steps()
        except Exception as e:
            self.wf = from_state(snapshot[0], safe_engine(self.rt.services, self.meta["id"]))
            self.meta = snapshot[1]
            raise EngineError(f"engine lỗi, đã khôi phục state: {type(e).__name__}: {e}") from e
        if kind == "revise" and outcome == "resubmit":
            self.meta["returns"][step] = self.meta["returns"].get(step, 0) + 1
        if kind in ("decision", "task") and outcome == "return_for_revision" and m["on_return"].get("to_step"):
            self.meta["returns"][step] = self.meta["returns"].get(step, 0) + 1
        if kind in ("decision", "task"):
            self.meta["actors"].setdefault(step, []).append(actor)
        if outcome in ("delegate", "request_info"):
            self.meta["loops"][step] = self.meta["loops"].get(step, 0) + 1
        if kind == "revise" and updates and actor not in self.meta.setdefault("editors", []):
            self.meta["editors"].append(actor)
        self.meta["history"].append({"task": name, "step": step, "actor": actor, "outcome": outcome, "comment": comment,
                                     **({"delegate_to": delegate_to} if delegate_to else {}), **({"ask": ask} if ask else {}),
                                     **({"updates": updates} if updates else {})})
        self._sync()
        self._dispatch()

    def tick(self):
        snapshot = to_state(self.wf)
        try:
            self.wf.refresh_timers()
            self.wf.do_engine_steps()
        except Exception as e:
            self.wf = from_state(snapshot, safe_engine(self.rt.services, self.meta["id"]))
            raise EngineError(f"engine lỗi khi chạy timer: {type(e).__name__}: {e}") from e
        self._sync()
        self._dispatch()
