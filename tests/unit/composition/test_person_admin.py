# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`xt person set` (D-DEMO-LEVELS-1): kế hoạch thuần + digest + nối CLI (không cần Postgres)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from xt.adapters.db.integration import AdminError
from xt.domain.model.context import TenantRef
from xt.entrypoints import admin, cli
from xt.entrypoints.composition.person_admin import parse_roles, plan_person

T = TenantRef(7, uuid.UUID("00000000-0000-0000-0000-000000000007"))
PID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def row(**kw: Any) -> dict[str, Any]:
    base = {"id": PID, "key": "NV-0004", "name": "Phạm Kim Oanh", "has_login": True,
            "roles": ["dept_head", "chief_accountant", "employee"], "active": True,
            "granted": ["facility_manager"]}  # fmt: skip
    return {**base, **kw}


def test_add_role_plan_and_stable_digest() -> None:
    p = plan_person(row(), tenant=T, alias="binhminh", key="NV-0004",
                    add_roles=["payment_accountant"])  # fmt: skip
    assert p.problems == []
    assert p.before == {"roles": ["dept_head", "chief_accountant", "employee"], "active": True}
    assert p.after == {
        "roles": ["dept_head", "chief_accountant", "employee", "payment_accountant"],
        "active": True,
    }
    again = plan_person(row(), tenant=T, alias="binhminh", key="NV-0004",
                        add_roles=["payment_accountant"])  # fmt: skip
    assert p.digest == again.digest and p.digest.startswith("sha256:")
    out = p.render_vi()
    assert "facility_manager" in out and p.digest in out


def test_digest_binds_current_state() -> None:
    a = plan_person(row(), tenant=T, alias="b", key="NV-0004", active=False)
    b = plan_person(row(roles=["employee"]), tenant=T, alias="b", key="NV-0004", active=False)
    assert a.digest != b.digest


def test_inactive_plan() -> None:
    p = plan_person(row(key="NV-0005", roles=["payment_accountant", "employee"]), tenant=T,
                    alias="b", key="NV-0005", active=False)  # fmt: skip
    assert p.after["active"] is False and p.after["roles"] == p.before["roles"]
    assert "Người nghỉ không được giao bước mới" in p.render_vi()


@pytest.mark.parametrize(
    ("r", "kw", "frag"),
    [
        (None, {"active": False}, "không có người key"),
        (row(), {"add_roles": ["employee"]}, "kế hoạch rỗng"),
        (row(), {"active": True}, "kế hoạch rỗng"),
        (row(), {"remove_roles": ["facility_manager"]}, "access_grant"),
        (row(), {"add_roles": ["x"], "remove_roles": ["x"]}, "vừa thêm vừa bỏ"),
    ],
)
def test_refused_plans(r: dict[str, Any] | None, kw: dict[str, Any], frag: str) -> None:
    p = plan_person(r, tenant=T, alias="b", key="NV-9999", **kw)
    assert p.problems and any(frag in x for x in p.problems), p.problems
    assert "KHÔNG ÁP ĐƯỢC" in p.render_vi()


def test_parse_roles_rejects_bad_names() -> None:
    assert parse_roles(" payment_accountant, employee,employee ") == [
        "payment_accountant",
        "employee",
    ]
    for bad in ("Admin", "a-b", "x" * 41, "1abc"):
        with pytest.raises(AdminError):
            parse_roles(bad)


def test_cli_routes_person_group(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "person" in cli.ADMIN_GROUPS
    seen: list[list[str]] = []
    monkeypatch.setattr(admin, "main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main(["person", "set", "--tenant", "t", "--key", "NV-1", "--inactive",
                     "--proposed-by", "a", "--approved-by", "b"]) == 0  # fmt: skip
    assert seen and seen[0][:2] == ["person", "set"]


def test_cli_requires_a_change_and_exclusive_active_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    env = {"XT_DB_MIGRATOR_URL": "postgresql://x@127.0.0.1:1/x"}
    base = ["person", "set", "--tenant", "t", "--key", "NV-1", "--proposed-by", "a",
            "--approved-by", "b"]  # fmt: skip
    with pytest.raises(AdminError, match="cần ít nhất một thay đổi"):
        admin.run(base, env)
    with pytest.raises(SystemExit):
        admin.run([*base, "--active", "--inactive"], env)
    with pytest.raises(AdminError, match="tên vai trò"):
        admin.run([*base, "--add-roles", "Bad-Role"], env)
