# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-UNI-H-1: `access_grant` (system keycloak) đã duyệt -> vai trò hiệu lực của person; thu hồi
khi có `revoked_on`; idempotent; SoD; allow-list của tenant; tenant chỉ từ ngữ cảnh đã xác thực."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from uuid import UUID

import pytest

from xt.application.iam.role_grants import AccessGrant, RoleGrantRejected, RoleGrantService
from xt.domain.model.context import ModelActor, TenantRef

T_A, T_B = TenantRef(1, uuid.uuid4()), TenantRef(2, uuid.uuid4())
HR, IT, NEW, OTHER = (uuid.uuid4() for _ in range(4))
ALLOWED = ("employee", "hr", "it_admin")


@dataclass
class FakeStore:
    """Bảng person + person_role_grant theo tenant (RLS: chỉ thấy dòng của tenant actor)."""

    people: dict[UUID, set[UUID]] = field(default_factory=dict)
    rows: dict[tuple[UUID, str], dict[str, object]] = field(default_factory=dict)

    def person_active(self, actor: ModelActor, pid: UUID) -> bool:
        return pid in self.people.get(actor.tenant.id, set())

    def apply_role_grant(
        self, actor: ModelActor, ref: str, person: UUID, role: str, requested_by: UUID,
        approved_by: UUID,
    ) -> bool:  # fmt: skip
        key = (actor.tenant.id, ref)
        cur = self.rows.get(key)
        if cur and cur["revoked"] is False and (cur["person"], cur["role"]) == (person, role):
            return False
        self.rows[key] = {"person": person, "role": role, "by": approved_by, "revoked": False}
        return True

    def revoke_role_grant(self, actor: ModelActor, ref: str, revoked_by: UUID) -> bool:
        cur = self.rows.get((actor.tenant.id, ref))
        if cur is None or cur["revoked"]:
            return False
        cur["revoked"] = True
        return True

    def effective(self, tenant: TenantRef, person: UUID) -> set[str]:
        return {
            str(r["role"]) for (t, _), r in self.rows.items()
            if t == tenant.id and r["person"] == person and not r["revoked"]
        }  # fmt: skip


def _actor(tenant: TenantRef = T_A, who: UUID = IT) -> ModelActor:
    return ModelActor(tenant, who, ("xt_workflow",))


def _grant(role: str = "employee", **kw: object) -> AccessGrant:
    base: dict[str, object] = {
        "ref": "access_grant:1", "system": "keycloak", "role": role, "grantee": NEW,
        "requester": HR, "revoked": False,
    }  # fmt: skip
    base.update(kw)
    return AccessGrant(**base)  # type: ignore[arg-type]


@pytest.fixture
def store() -> FakeStore:
    return FakeStore(people={T_A.id: {HR, IT, NEW}, T_B.id: {OTHER}})


def test_apply_binds_role(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    assert svc.apply(_actor(), _grant(), allowed=ALLOWED) == "applied"
    assert store.effective(T_A, NEW) == {"employee"}
    assert store.rows[(T_A.id, "access_grant:1")]["by"] == IT


def test_reapply_is_idempotent(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    svc.apply(_actor(), _grant(), allowed=ALLOWED)
    assert svc.apply(_actor(), _grant(), allowed=ALLOWED) == "already"
    assert store.effective(T_A, NEW) == {"employee"}


def test_revoked_on_removes_binding(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    svc.apply(_actor(), _grant(), allowed=ALLOWED)
    assert svc.apply(_actor(), _grant(revoked=True), allowed=ALLOWED) == "revoked"
    assert store.effective(T_A, NEW) == set()
    # thu hồi lặp lại / thu hồi trực tiếp từ hook cập nhật bản ghi: không lỗi, không đổi gì
    assert svc.revoke(_actor(), "access_grant:1", by=IT) is False
    assert svc.apply(_actor(), _grant(revoked=True), allowed=ALLOWED) == "already"


def test_revoke_hook_direct(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    svc.apply(_actor(), _grant(), allowed=ALLOWED)
    assert svc.revoke(_actor(), "access_grant:1", by=IT) is True
    assert store.effective(T_A, NEW) == set()


@pytest.mark.parametrize(
    ("approver", "requester", "grantee"),
    [(HR, HR, NEW), (NEW, HR, NEW), (IT, None, NEW)],
    ids=["approver-is-requester", "approver-is-grantee", "requester-unknown"],
)
def test_self_approval_rejected(
    store: FakeStore, approver: UUID, requester: UUID | None, grantee: UUID
) -> None:
    svc = RoleGrantService(store)
    g = _grant(requester=requester, grantee=grantee)
    with pytest.raises(RoleGrantRejected) as e:
        svc.apply(_actor(who=approver), g, allowed=ALLOWED)
    assert e.value.reason == "self_approval"
    assert store.rows == {}


@pytest.mark.parametrize("role", ["xt_admin", "director", "Bad Role", ""])
def test_role_outside_allow_list_rejected(store: FakeStore, role: str) -> None:
    with pytest.raises(RoleGrantRejected) as e:
        RoleGrantService(store).apply(_actor(), _grant(role), allowed=ALLOWED)
    assert e.value.reason == "role_not_allowed"
    assert store.rows == {}


def test_allow_list_is_per_tenant_config(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    out = svc.apply(_actor(), _grant("xt_admin"), allowed=(*ALLOWED, "xt_admin"))
    assert out == "applied"


def test_cross_tenant_rejected(store: FakeStore) -> None:
    svc = RoleGrantService(store)
    # người được cấp thuộc tenant B; actor (ngữ cảnh đã xác thực) ở tenant A -> không thấy, từ chối
    with pytest.raises(RoleGrantRejected) as e:
        svc.apply(_actor(), _grant(grantee=OTHER), allowed=ALLOWED)
    assert e.value.reason == "person_not_found"
    # người duyệt của tenant B không duyệt được cho tenant A (và ngược lại)
    with pytest.raises(RoleGrantRejected):
        svc.apply(_actor(T_B, OTHER), _grant(), allowed=ALLOWED)
    assert store.rows == {}
    # thu hồi chỉ chạm dòng của tenant actor
    svc.apply(_actor(), _grant(), allowed=ALLOWED)
    assert svc.revoke(_actor(T_B, OTHER), "access_grant:1", by=OTHER) is False
    assert store.effective(T_A, NEW) == {"employee"}


def test_other_systems_ignored(store: FakeStore) -> None:
    out = RoleGrantService(store).apply(_actor(), _grant("xt_admin", system="vpn"), allowed=ALLOWED)
    assert out == "skipped" and store.rows == {}
