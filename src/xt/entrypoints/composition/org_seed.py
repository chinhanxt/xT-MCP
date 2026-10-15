# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chuyển tệp tổ chức dạng HR (`seed/binh-minh/org.json`: departments/positions/employees) sang
định dạng nạp của `import_org` (units/people/agent_grants).

- Đơn vị: `departments[].id` -> key; trưởng đơn vị = nhân sự đang làm, thuộc đơn vị, có vai trò
  `dept_head` (nhiều hơn một -> lỗi, không đoán).
- Người: `employees[].id` -> key; chức danh lấy từ `positions`; vai trò = `roles` của tệp cộng
  `base_roles` do người vận hành khai tường minh (`--base-roles`), không có vai trò ngầm.
- Liên kết đăng nhập (`subject` = `sub` của Keycloak) và quyền agent nằm ở tệp `links`
  (`{"subjects": {key: sub}, "agent_grants": [...]}`), vì tệp HR không biết IdP.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xt.adapters.db.integration import AdminError


def is_hr_format(data: Mapping[str, Any]) -> bool:
    return "employees" in data and "departments" in data


def org_from_hr(
    data: Mapping[str, Any],
    *,
    links: Mapping[str, Any] | None = None,
    base_roles: Sequence[str] = (),
) -> dict[str, Any]:
    links = links or {}
    departments = list(data.get("departments", ()))
    positions = {str(p["id"]): str(p["name"]) for p in data.get("positions", ())}
    employees = list(data.get("employees", ()))
    subjects: Mapping[str, str] = links.get("subjects", {})
    keys = {str(e["id"]) for e in employees}
    problems = [
        f"links.subjects: nhân sự {k!r} không có trong tệp" for k in subjects if k not in keys
    ]
    heads: dict[str, list[str]] = {}
    for e in employees:
        if e.get("active", True) and "dept_head" in e.get("roles", ()):
            heads.setdefault(str(e["department_id"]), []).append(str(e["id"]))
    for dept, hs in heads.items():
        if len(hs) > 1:
            problems.append(f"đơn vị {dept}: nhiều trưởng đơn vị {hs}")
    if problems:
        raise AdminError("; ".join(problems))
    units = [
        {"key": str(d["id"]), "name": str(d["name"]), "head": heads.get(str(d["id"]), [None])[0]}
        for d in departments
    ]
    people = []
    for e in employees:
        roles = list(dict.fromkeys([*e.get("roles", ()), *base_roles]))
        people.append(
            {
                "key": str(e["id"]),
                "name": str(e["full_name"]),
                "email": e.get("email"),
                "unit": e.get("department_id"),
                "manager": e.get("manager_id"),
                "title": positions.get(str(e.get("position_id"))),
                "active": bool(e.get("active", True)),
                "roles": roles,
                "subject": subjects.get(str(e["id"])),
            }
        )
    return {"units": units, "people": people, "agent_grants": list(links.get("agent_grants", ()))}
