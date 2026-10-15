# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Resolver người nhận (G-P4). Không có biểu thức: mỗi `kind` của Assignee ánh xạ tới một hàm đã đăng ký,
đọc dữ liệu tổ chức qua `OrgDirectory`. Không giải được -> thử fallback -> fallback của quy trình -> `unresolved`
(task vẫn READY, không ai làm được cho tới khi quản trị gán lại). Không bao giờ bỏ qua bước."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .spec import Assignee


class OrgDirectory(Protocol):
    def get_user(self, uid: str) -> dict | None: ...          # {id, active, department, roles, manager}
    def department_head(self, dept: str) -> str | None: ...
    def users_with_role(self, role: str, dept: str | None = None) -> list[str]: ...
    def department_exists(self, dept: str) -> bool: ...


class InMemoryOrg:
    """Adapter test/demo nạp từ seed của pack. Production: bảng org_unit/position (architecture-v2 §6.2)."""

    def __init__(self, users: dict, departments: dict):
        self.users, self.departments = users, departments

    @classmethod
    def from_seed(cls, users_doc: dict, employees_doc: dict | None = None):
        """users_doc: seed 00_users.json (departments[key, head], users[key, department, roles]);
        employees_doc: 02_nhan_vien.json (records[_key, manager='@employee:NV-x', state])."""
        users = {u["key"]: {"id": u["key"], "name": u.get("name"), "active": True, "department": u.get("department"),
                            "roles": list(u.get("roles", [])), "manager": None} for u in users_doc["users"]}
        for r in (employees_doc or {}).get("records", []):
            if r["_key"] not in users:
                continue
            if r.get("manager"):
                users[r["_key"]]["manager"] = r["manager"].split(":", 1)[1]
            if r.get("state") in ("resigned", "terminated", "left"):
                users[r["_key"]]["active"] = False
        depts = {d["key"]: {"key": d["key"], "name": d.get("name"), "head": d.get("head")} for d in users_doc["departments"]}
        return cls(users, depts)

    def get_user(self, uid):
        return self.users.get(uid) if isinstance(uid, str) else None

    def department_head(self, dept):
        d = self.departments.get(dept)
        return d and d.get("head")

    def users_with_role(self, role, dept=None):
        return sorted(u["id"] for u in self.users.values() if role in u["roles"] and (dept is None or u["department"] == dept))

    def department_exists(self, dept):
        return isinstance(dept, str) and dept in self.departments


@dataclass
class Assignment:
    candidates: list[str]
    reason: str
    via: str                      # primary | fallback | process_fallback | unresolved | override | admin
    trail: list[str] = field(default_factory=list)

    def to_dict(self):
        return {"candidates": self.candidates, "reason": self.reason, "via": self.via, "trail": self.trail}


class AssigneeResolver:
    def __init__(self, org: OrgDirectory):
        self.org = org
        self.kinds = {"role": self._role, "manager": self._manager, "department_head": self._head, "user": self._user}

    def _dept(self, a: Assignee, data):
        if a.department:
            return a.department, f"phòng {a.department}"
        if a.department_of:
            u = self.org.get_user(data.get(a.department_of))
            if not u or not u.get("department"):
                return None, f"không xác định được phòng của {a.department_of}={data.get(a.department_of)!r}"
            return u["department"], f"phòng {u['department']} của {a.department_of} {u['id']}"
        if a.department_var:
            d = data.get(a.department_var)
            if not self.org.department_exists(d):
                return None, f"phòng {d!r} trong biến {a.department_var} không tồn tại"
            return d, f"phòng {d}"
        return None, ""

    def _role(self, a, data):
        dept, why = (None, "toàn công ty") if a.is_static() else self._dept(a, data)
        if not a.is_static() and dept is None:
            return [], why
        users = self.org.users_with_role(a.role, dept)
        return users, f"vai trò {a.role} ({why})" if users else f"không có ai giữ vai trò {a.role} ({why})"

    def _manager(self, a, data):
        uid = data.get(a.user_var)
        cur = self.org.get_user(uid)
        if not cur:
            return [], f"{a.user_var}={uid!r} không có trong danh bạ"
        for lvl in range(a.levels):
            m = cur.get("manager")
            nxt = self.org.get_user(m) if m else None
            if not nxt:
                return [], f"{cur['id']} không có quản lý (cấp {lvl + 1})"
            cur = nxt
        return [cur["id"]], f"quản lý cấp {a.levels} của {uid}"

    def _head(self, a, data):
        dept, why = self._dept(a, data)
        if dept is None:
            return [], why
        h = self.org.department_head(dept)
        return ([h], f"trưởng {why}") if h else ([], f"{why} chưa có trưởng phòng")

    def _user(self, a, data):
        uid = data.get(a.user_var)
        return ([uid], f"người trong biến {a.user_var}") if self.org.get_user(uid) else ([], f"{a.user_var}={uid!r} không có trong danh bạ")

    def _filter(self, users, exclude):
        ok, dropped = [], []
        for u in users:
            info = self.org.get_user(u)
            if not info or not info.get("active", False):
                dropped.append(f"{u} không còn hoạt động")
            elif u in exclude:
                dropped.append(f"{u} bị loại do SoD ({exclude[u]})")
            else:
                ok.append(u)
        return ok, dropped

    def resolve(self, primary: Assignee, data: dict, exclude: dict, process_fallback: Assignee | None) -> Assignment:
        trail = []
        chain = [("primary", primary)] + [("fallback", x) for x in list(primary.all())[1:]]
        if process_fallback is not None:
            chain += [("process_fallback", x) for x in process_fallback.all()]
        for via, a in chain:
            fn = self.kinds.get(a.kind)
            if fn is None:                       # chỉ kind đã đăng ký; không có đường eval
                trail.append(f"kind {a.kind!r} chưa đăng ký resolver")
                continue
            users, why = fn(a, data)
            users, dropped = self._filter(users, exclude)
            if users:
                return Assignment(users, f"{a.describe()}: {why}", via, trail)
            trail.append(f"{a.describe()}: {why}" + (f" [{'; '.join(dropped)}]" if dropped else ""))
        return Assignment([], "không giải được người nhận; chờ quản trị gán lại", "unresolved", trail)
