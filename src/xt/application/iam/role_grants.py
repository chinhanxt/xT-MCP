# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-UNI-H-1: áp `access_grant` đã duyệt (system `keycloak`) thành vai trò hiệu lực của person.

- Tenant, người duyệt: chỉ từ `ModelActor` của ngữ cảnh đã xác thực (RLS theo tenant đó); người
  được cấp/người duyệt không thuộc tenant -> từ chối.
- Allow-list vai trò: cấu hình của tenant (tham số op trong định nghĩa quy trình đã duyệt), không
  cài cứng; vai trò ngoài danh sách (vd `xt_admin`) -> từ chối.
- SoD: người duyệt khác người đề nghị, khác người được cấp và khác người tạo bản ghi `access_grant`
  (người chọn vai trò); không biết người đề nghị hoặc người tạo -> từ chối.
- Idempotent theo khóa `ref` (bản ghi nguồn): áp lại không đổi gì; `revoked` -> gỡ ràng buộc.
- Audit: bảng `xt_meta.person_role_grant` nằm trong hash-chain audit của tenant (migration 0017).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from xt.domain.model.context import ModelActor

SYSTEM = "keycloak"
_ROLE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
Outcome = Literal["applied", "already", "revoked", "skipped"]


class RoleGrantRejected(PermissionError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AccessGrant:
    ref: str  # "<model>:<uuid>" của bản ghi nguồn (khóa idempotent)
    system: str
    role: str
    grantee: UUID  # person id
    requester: UUID | None
    revoked: bool
    creator: UUID | None  # người tạo bản ghi nguồn (created_by)


class RoleGrantStore(Protocol):
    def person_active(self, actor: ModelActor, pid: UUID) -> bool: ...

    def apply_role_grant(
        self,
        actor: ModelActor,
        ref: str,
        person: UUID,
        role: str,
        requested_by: UUID,
        approved_by: UUID,
    ) -> bool: ...

    def revoke_role_grant(self, actor: ModelActor, ref: str, revoked_by: UUID) -> bool: ...


def parse_roles(value: object) -> tuple[str, ...]:
    """Allow-list dạng "a,b,c" (tham số op); rỗng hoặc tên sai -> lỗi."""
    roles = tuple(r.strip() for r in str(value or "").split(",") if r.strip())
    if not roles or any(not _ROLE.match(r) for r in roles):
        raise ValueError(f"danh sách vai trò không hợp lệ: {value!r}")
    return roles


class RoleGrantService:
    def __init__(self, store: RoleGrantStore) -> None:
        self.store = store

    def apply(self, actor: ModelActor, grant: AccessGrant, *, allowed: Collection[str]) -> Outcome:
        if grant.system != SYSTEM:
            return "skipped"
        approver = actor.actor_id
        if grant.revoked:
            return "revoked" if self.revoke(actor, grant.ref, by=approver) else "already"
        if not _ROLE.match(grant.role) or grant.role not in allowed:
            raise RoleGrantRejected(
                "role_not_allowed", f"vai trò {grant.role!r} không nằm trong danh sách được cấp"
            )
        if (
            grant.requester is None
            or grant.creator is None
            or approver in (grant.requester, grant.grantee, grant.creator)
        ):
            raise RoleGrantRejected(
                "self_approval",
                "người duyệt phải khác người đề nghị, người được cấp quyền và người tạo bản ghi cấp"
                " quyền",
            )
        for pid in (approver, grant.grantee, grant.requester):
            if not self.store.person_active(actor, pid):
                raise RoleGrantRejected(
                    "person_not_found", f"không có người {pid} đang hoạt động trong tổ chức"
                )
        changed = self.store.apply_role_grant(
            actor, grant.ref, grant.grantee, grant.role, grant.requester, approver
        )
        return "applied" if changed else "already"

    def revoke(self, actor: ModelActor, ref: str, *, by: UUID) -> bool:
        return self.store.revoke_role_grant(actor, ref, by)
