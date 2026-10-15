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


def apply_mapping(data: Mapping[str, Any], mapping: Mapping[str, Any]) -> dict[str, Any]:
    """Dịch từ vựng của tệp HR sang từ vựng của gói (`mapping.json`):
    `departments` {id tệp -> mã phòng}, `roles` {vai trò tệp -> [vai trò gói]}; khóa `role@id`
    cộng thêm vai trò cho người giữ vai trò đó trong đúng phòng đó. Vai trò/phòng không khai -> lỗi."""
    dmap: Mapping[str, str] = mapping.get("departments", {})
    rmap: Mapping[str, Sequence[str]] = mapping.get("roles", {})
    problems = []
    for key, val in rmap.items():
        if not isinstance(val, list) or not all(isinstance(r, str) and r for r in val):
            problems.append(f"mapping.roles[{key!r}]: phải là danh sách tên vai trò")
    for key, val in dmap.items():
        if not isinstance(val, str) or not val:
            problems.append(f"mapping.departments[{key!r}]: phải là mã phòng (chuỗi)")
    for d in data.get("departments", ()):
        if str(d["id"]) not in dmap:
            problems.append(f"mapping thiếu phòng {d['id']!r} ({d.get('name')})")
    if len(set(dmap.values())) != len(dmap):
        problems.append("mapping.departments: hai phòng trùng một mã")
    seen: set[str] = set()
    for e in data.get("employees", ()):
        for r in e.get("roles", ()):
            if r not in rmap and r not in seen:
                seen.add(r)
                problems.append(f"mapping thiếu vai trò {r!r} (nhân sự {e['id']})")
    for key in rmap:
        base, _, dept = key.partition("@")
        if dept and not any(str(d["id"]) == dept for d in data.get("departments", ())):
            problems.append(f"mapping.roles[{key!r}]: phòng {dept!r} không có trong tệp")
    if problems:
        raise AdminError("; ".join(problems))
    out = dict(data)
    out["departments"] = [
        {**d, "id": dmap[str(d["id"])], **({"parent_id": dmap[str(d["parent_id"])]}
                                          if d.get("parent_id") is not None else {})}
        for d in data.get("departments", ())
    ]
    employees = []
    for e in data.get("employees", ()):
        dept = str(e.get("department_id"))
        roles: list[str] = []
        for r in e.get("roles", ()):
            roles += [*rmap[r], *rmap.get(f"{r}@{dept}", ())]
        employees.append(
            {**e, "department_id": dmap.get(dept, e.get("department_id")),
             "roles": list(dict.fromkeys(roles))}
        )
    out["employees"] = employees
    return out


def org_from_hr(
    data: Mapping[str, Any],
    *,
    links: Mapping[str, Any] | None = None,
    base_roles: Sequence[str] = (),
    mapping: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    links = links or {}
    if mapping is not None:
        data = apply_mapping(data, mapping)
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
