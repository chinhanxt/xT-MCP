# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Giải người nhận bước (G-P4). Không có biểu thức: mỗi `kind` của Assignee ánh xạ tới một hàm
đã đăng ký, đọc dữ liệu tổ chức qua `OrgDirectory` (port; bản thật đọc org_unit/position).

Thứ tự: assignee -> fallback của nó -> fallback_assignee của quy trình -> `unresolved` (task vẫn
READY, không ai làm được cho tới khi quản trị gán lại). Không bao giờ bỏ qua bước. Ứng viên bị lọc
nếu không còn hoạt động hoặc bị SoD loại.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from xt.domain.process.spec import Assignee

Via = Literal["primary", "fallback", "process_fallback", "unresolved", "override", "admin"]


@dataclass(frozen=True, slots=True)
class OrgUser:
    id: str
    active: bool
    department: str | None
    roles: tuple[str, ...]
    manager: str | None


class OrgDirectory(Protocol):
    """Dữ liệu tổ chức tối thiểu mà resolver cần (không hard-code dữ liệu nào trong engine)."""

    def get_user(self, uid: object) -> OrgUser | None: ...

    def department_head(self, dept: str) -> str | None: ...

    def users_with_role(self, role: str, dept: str | None = None) -> list[str]: ...

    def department_exists(self, dept: object) -> bool: ...


@dataclass(slots=True)
class Assignment:
    candidates: list[str]
    reason: str
    via: Via
    trail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": list(self.candidates),
            "reason": self.reason,
            "via": self.via,
            "trail": list(self.trail),
        }


KindResolver = Callable[[Assignee, Mapping[str, Any]], tuple[list[str], str]]


class AssigneeResolver:
    def __init__(self, org: OrgDirectory):
        self.org = org
        self.kinds: dict[str, KindResolver] = {
            "role": self._role,
            "manager": self._manager,
            "department_head": self._head,
            "user": self._user,
        }

    def _dept(self, a: Assignee, data: Mapping[str, Any]) -> tuple[str | None, str]:
        if a.department:
            return a.department, f"phòng {a.department}"
        if a.department_of:
            u = self.org.get_user(data.get(a.department_of))
            if not u or not u.department:
                who = data.get(a.department_of)
                return None, f"không xác định được phòng của {a.department_of}={who!r}"
            return u.department, f"phòng {u.department} của {a.department_of} {u.id}"
        if a.department_var:
            d = data.get(a.department_var)
            if not isinstance(d, str) or not self.org.department_exists(d):
                return None, f"phòng {d!r} trong biến {a.department_var} không tồn tại"
            return d, f"phòng {d}"
        return None, ""

    def _role(self, a: Assignee, data: Mapping[str, Any]) -> tuple[list[str], str]:
        assert a.role is not None
        if a.is_static():
            dept, why = None, "toàn công ty"
        else:
            dept, why = self._dept(a, data)
            if dept is None:
                return [], why
        users = self.org.users_with_role(a.role, dept)
        if users:
            return users, f"vai trò {a.role} ({why})"
        return [], f"không có ai giữ vai trò {a.role} ({why})"

    def _manager(self, a: Assignee, data: Mapping[str, Any]) -> tuple[list[str], str]:
        uid = data.get(a.user_var or "")
        cur = self.org.get_user(uid)
        if not cur:
            return [], f"{a.user_var}={uid!r} không có trong danh bạ"
        for lvl in range(a.levels):
            nxt = self.org.get_user(cur.manager) if cur.manager else None
            if not nxt:
                return [], f"{cur.id} không có quản lý (cấp {lvl + 1})"
            cur = nxt
        return [cur.id], f"quản lý cấp {a.levels} của {uid}"

    def _head(self, a: Assignee, data: Mapping[str, Any]) -> tuple[list[str], str]:
        dept, why = self._dept(a, data)
        if dept is None:
            return [], why
        h = self.org.department_head(dept)
        return ([h], f"trưởng {why}") if h else ([], f"{why} chưa có trưởng phòng")

    def _user(self, a: Assignee, data: Mapping[str, Any]) -> tuple[list[str], str]:
        uid = data.get(a.user_var or "")
        u = self.org.get_user(uid)
        if u is None:
            return [], f"{a.user_var}={uid!r} không có trong danh bạ"
        return [u.id], f"người trong biến {a.user_var}"

    def active(self, uid: object) -> bool:
        u = self.org.get_user(uid)
        return bool(u and u.active)

    def _filter(self, users: list[str], exclude: Mapping[str, str]) -> tuple[list[str], list[str]]:
        ok: list[str] = []
        dropped: list[str] = []
        for u in users:
            if not self.active(u):
                dropped.append(f"{u} không còn hoạt động")
            elif u in exclude:
                dropped.append(f"{u} bị loại do SoD ({exclude[u]})")
            else:
                ok.append(u)
        return ok, dropped

    def resolve(
        self,
        primary: Assignee,
        data: Mapping[str, Any],
        exclude: Mapping[str, str],
        process_fallback: Assignee | None,
    ) -> Assignment:
        trail: list[str] = []
        chain: list[tuple[Via, Assignee]] = [("primary", primary)]
        chain += [("fallback", x) for x in list(primary.chain())[1:]]
        if process_fallback is not None:
            chain += [("process_fallback", x) for x in process_fallback.chain()]
        for via, a in chain:
            fn = self.kinds.get(a.kind)
            if fn is None:  # chỉ kind đã đăng ký; không có đường eval
                trail.append(f"kind {a.kind!r} chưa đăng ký resolver")
                continue
            users, why = fn(a, data)
            users, dropped = self._filter(users, exclude)
            if users:
                return Assignment(users, f"{a.describe()}: {why}", via, trail)
            trail.append(f"{a.describe()}: {why}" + (f" [{'; '.join(dropped)}]" if dropped else ""))
        return Assignment(
            [], "không giải được người nhận; chờ quản trị gán lại", "unresolved", trail
        )
