# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`xt person set`: đổi chính xác vai trò gốc / cờ active của MỘT người (D-DEMO-LEVELS-1).

Cùng luồng hai bước với `xt pack install`:

1. Không `--confirm`: đọc người theo `key`, lập kế hoạch (trước -> sau) và in mã xác nhận
   `digest` (gắn tenant, id người, trạng thái trước và sau; đổi dữ liệu giữa lúc xem và lúc duyệt
   làm mã không khớp). Không ghi gì.
2. `--confirm <digest>`: người duyệt khác người đề xuất; trong một transaction khóa dòng người,
   lập lại kế hoạch, so digest, UPDATE `person` + ghi `xt_meta.person_change` (hash-chain audit).

Chỉ đổi `person.roles` (vai trò gốc) và `person.active`; vai trò cấp qua access_grant (D-UNI-H-1)
giữ nguyên và chỉ hiển thị. Không tạo, không xóa người: key lạ -> từ chối. Kế hoạch rỗng -> từ chối.
Lưu ý vận hành: `xt org import` (không `--if-empty`) nạp lại roles/active từ tệp, ghi đè thay đổi.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from xt.adapters.db.integration import (
    AdminError,
    apply_person_change,
    connect,
    person_admin_row,
)
from xt.domain.model.context import TenantRef

_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def parse_roles(value: str | None) -> list[str]:
    roles = [r.strip() for r in (value or "").split(",") if r.strip()]
    bad = [r for r in roles if not _ROLE.match(r)]
    if bad:
        raise AdminError(f"tên vai trò không hợp lệ: {bad} (chữ thường, số, '_', tối đa 40)")
    return list(dict.fromkeys(roles))


@dataclass
class PersonPlan:
    tenant: TenantRef
    alias: str
    key: str
    person: str | None
    name: str | None
    before: dict[str, Any]
    after: dict[str, Any]
    granted: list[str] = field(default_factory=list)
    has_login: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        body = {
            "op": "person.set",
            "tenant": str(self.tenant.id),
            "person": self.person,
            "key": self.key,
            "before": self.before,
            "after": self.after,
        }
        canon = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()

    def render_vi(self) -> str:
        def state(s: Mapping[str, Any]) -> str:
            roles = ", ".join(s.get("roles") or []) or "(không)"
            return f"vai trò gốc [{roles}], {'đang hoạt động' if s.get('active') else 'NGHỈ'}"

        lines = [
            f"Đổi người {self.key} ({self.name or '?'}) ở tenant {self.alias}"
            f" (seq {self.tenant.seq})",
            f"Trước: {state(self.before)}" if self.before else "Trước: (không có)",
            f"Sau:   {state(self.after)}" if self.after else "Sau:   (không có)",
            "Vai trò cấp qua access_grant (giữ nguyên): " + (", ".join(self.granted) or "(không)"),
            "Đăng nhập (subject Keycloak): " + ("có" if self.has_login else "chưa liên kết"),
        ]
        if self.before.get("active") and self.after.get("active") is False:
            lines.append(
                "Người nghỉ không được giao bước mới; bước đang giữ được giải lại người nhận."
                " Tài khoản Keycloak tắt riêng (enabled=false)."
            )
        if self.problems:
            lines += ["", "KHÔNG ÁP ĐƯỢC:", *[f"  - {p}" for p in self.problems]]
        else:
            lines += ["", f"Mã xác nhận (digest): {self.digest}"]
        return "\n".join(lines)


def plan_person(
    row: Mapping[str, Any] | None,
    *,
    tenant: TenantRef,
    alias: str,
    key: str,
    add_roles: Sequence[str] = (),
    remove_roles: Sequence[str] = (),
    active: bool | None = None,
) -> PersonPlan:
    """Hàm thuần: dòng người hiện tại + yêu cầu -> kế hoạch (có `problems` nếu không áp được)."""
    if row is None:
        return PersonPlan(tenant, alias, key, None, None, {}, {},
                          problems=[f"không có người key {key!r} ở tenant {alias}"])  # fmt: skip
    roles = list(row["roles"] or [])
    before = {"roles": roles, "active": bool(row["active"])}
    problems: list[str] = []
    both = sorted(set(add_roles) & set(remove_roles))
    if both:
        problems.append(f"vai trò vừa thêm vừa bỏ: {both}")
    missing = [r for r in remove_roles if r not in roles]
    if missing:
        granted = [r for r in missing if r in (row.get("granted") or [])]
        hint = (
            f" ({granted} cấp qua access_grant: thu hồi bằng quy trình nghỉ việc/IT)"
            if granted
            else ""
        )
        problems.append(f"không có vai trò gốc {missing} để bỏ{hint}")
    new_roles = [r for r in roles if r not in remove_roles] + [
        r for r in add_roles if r not in roles
    ]
    after = {"roles": new_roles, "active": before["active"] if active is None else bool(active)}
    if after == before and not problems:
        problems.append("kế hoạch rỗng: trạng thái đã đúng như yêu cầu, không có gì để đổi")
    return PersonPlan(
        tenant, alias, key, str(row["id"]), str(row.get("name") or ""), before, after,
        list(row.get("granted") or []), bool(row.get("has_login")), problems,
    )  # fmt: skip


def check_key(key: str) -> str:
    if not _KEY.match(key or ""):
        raise AdminError(f"key người không hợp lệ: {key!r}")
    return key


def read_plan(conn: Any, *, tenant: TenantRef, alias: str, key: str, lock: bool = False,
              **want: Any) -> PersonPlan:  # fmt: skip
    row = person_admin_row(conn, tenant, check_key(key), lock=lock)
    return plan_person(row, tenant=tenant, alias=alias, key=key, **want)


def apply_person(
    migrator_dsn: str,
    *,
    tenant: TenantRef,
    alias: str,
    key: str,
    confirm: str,
    proposed_by: str,
    approved_by: str,
    **want: Any,
) -> PersonPlan:
    if not proposed_by or not approved_by or approved_by == proposed_by:
        raise AdminError("người duyệt phải khác người đề xuất (tách nhiệm vụ)")
    with connect(migrator_dsn) as conn, conn.transaction():
        plan = read_plan(conn, tenant=tenant, alias=alias, key=key, lock=True, **want)
        if plan.problems:
            raise AdminError("; ".join(plan.problems))
        if plan.digest != confirm:
            raise AdminError(
                f"mã xác nhận không khớp kế hoạch hiện tại ({plan.digest});"
                " xem lại kế hoạch rồi duyệt"
            )
        assert plan.person is not None
        apply_person_change(
            conn,
            tenant,
            person=UUID(plan.person),
            key=key,
            before=plan.before,
            after=plan.after,
            digest=plan.digest,
            proposed_by=proposed_by,
            approved_by=approved_by,
        )
    return plan
