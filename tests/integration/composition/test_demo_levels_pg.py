# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-DEMO-LEVELS-1 trên Postgres 17 thật (xt_app, RLS, audit trigger, sổ quyết định):

Tenant riêng mô phỏng công ty demo chỉ còn MỘT tài khoản mỗi cấp. Người vận hành dùng
`xt person set` (kế hoạch + `--confirm <digest>`, audit `xt_meta.person_change`) để:
Oanh (trưởng KT, chief_accountant) giữ thêm payment_accountant; tắt Quyên (payment_accountant)
và Vinh (it_admin thứ hai). Sau đó:
- tiếp nhận v8: Khang (IT duy nhất) tạo access_grant, Khang không tự duyệt được bước IT; giám đốc
  (người duyệt thay thế) hoàn thành bước IT -> vai trò được áp, approved_by = giám đốc;
- đề nghị thanh toán 60tr: Oanh làm kttt, ktt (distinct_from kttt) rơi về giám đốc;
- người đã tắt không bao giờ là ứng viên.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import PACK, ROOT, StackBase, run_admin
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT
from tests.integration.composition.shared_tenants import Ops
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.application.tools.context import Actor
from xt.domain.identity import PrincipalKind
from xt.domain.process.errors import DecisionError, EngineError
from xt.entrypoints.composition.runtime import Caller
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration

TRI, OANH, QUYEN, BAO, DUNG, NHI, KHANG, VINH, NEW = (
    "NV-0001", "NV-0004", "NV-0005", "NV-0010", "NV-0013", "NV-0026", "NV-0031", "NV-0032",
    "NV-0090",
)  # fmt: skip
DEPTS = [("BGD", "Ban Giám đốc"), ("KT", "Kế toán"), ("KD", "Kinh doanh"), ("NS", "Nhân sự"),
         ("IT", "Công nghệ")]  # fmt: skip
PEOPLE = [
    (TRI, "Nguyễn Hữu Trí", "BGD", None, ["director"]),
    (OANH, "Phạm Kim Oanh", "KT", TRI, ["dept_head", "chief_accountant"]),
    (QUYEN, "Đỗ Thục Quyên", "KT", OANH, ["payment_accountant"]),
    (BAO, "Hoàng Quốc Bảo", "KD", TRI, ["dept_head"]),
    (DUNG, "Phan Tiến Dũng", "KD", BAO, []),
    (NHI, "Nguyễn Yến Nhi", "NS", TRI, ["hr"]),
    (KHANG, "Đặng Minh Khang", "IT", TRI, ["it_admin"]),
    (VINH, "Bùi Quang Vinh", "IT", KHANG, ["it_admin"]),
    (NEW, "Nhân Viên Mới", "KD", BAO, []),
]
DIGEST = re.compile(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})")
WHO = ["--proposed-by", "person:NV-0031", "--approved-by", "person:NV-0001"]


class Lv(Ops):
    def caller(self, key: str) -> tuple[Caller, Actor]:
        actor = Actor(self.alias, self.sub(key), PrincipalKind.HUMAN, DESK_CLIENT, None)
        return self.rt.resolve(actor, f"lv-{uuid.uuid4().hex[:8]}"), actor

    def candidates(self, iid: str, step: str) -> list[str]:
        info = self.run(TRI, lambda _, pc: pc.service().get(iid))
        task = next(t for t in info["tasks"] if t["step"] == step)
        return sorted(task["candidates"])


def person_set(env: dict[str, str], alias: str, key: str, *flags: str) -> tuple[int, str]:
    return run_admin(["person", "set", "--tenant", alias, "--key", key, *flags, *WHO], env)


def person_set_confirmed(env: dict[str, str], alias: str, key: str, *flags: str) -> str:
    rc, plan = person_set(env, alias, key, *flags)
    assert rc == 0, plan
    m = DIGEST.search(plan)
    assert m, plan
    rc, done = person_set(env, alias, key, *flags, "--confirm", m.group(1))
    assert rc == 0, done
    return m.group(1)


@pytest.fixture(scope="module")
def lv(
    stack_base: StackBase, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Lv, dict[str, str]]]:
    env = stack_base.env
    alias = f"lvl{uuid.uuid4().hex[:6]}"
    d = tmp_path_factory.mktemp("lvl")
    rc, out = run_admin(
        ["tenant", "create", "--alias", alias, "--name", "Một người mỗi cấp",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    org = {
        "departments": [{"id": k, "name": n, "parent_id": None} for k, n in DEPTS],
        "positions": [],
        "employees": [
            {"id": k, "full_name": n, "email": f"{k.lower()}@{alias}.example", "department_id": u,
             "position_id": None, "manager_id": m, "active": True, "roles": r}
            for k, n, u, m, r in PEOPLE
        ],
    }  # fmt: skip
    (d / "org.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    links = {
        "subjects": {k: f"kc-{alias}-{k}" for k, *_ in PEOPLE},
        "agent_grants": [
            {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
            for k, *_ in PEOPLE
        ],
    }
    (d / "links.json").write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", alias, str(d / "org.json"), "--links",
         str(d / "links.json"), "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    for pack in (PACK, ROOT / "packages" / "nhan-su-vong-doi"):
        common = ["pack", "install", "--tenant", alias, str(pack)]
        rc, plan = run_admin([*common, *WHO], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        m = DIGEST.search(plan)
        assert m, plan
        rc, done = run_admin([*common, *WHO, "--confirm", m.group(1)], env)
        assert rc == 0, done
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        tenant = tenant_of_alias(c, alias)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    comp: Any = prepared.services
    try:
        yield Lv(comp.runtime, comp.engine, tenant, {}, prepared.dispatcher, alias), env
    finally:
        prepared.close()


@pytest.fixture(scope="module")
def roster(lv: tuple[Lv, dict[str, str]]) -> Lv:
    """Áp roster demo bằng đúng lệnh của runbook (docs/demo-accounts.md)."""
    ops, env = lv
    person_set_confirmed(env, ops.alias, OANH, "--add-roles", "payment_accountant")
    person_set_confirmed(env, ops.alias, QUYEN, "--inactive")
    person_set_confirmed(env, ops.alias, VINH, "--inactive")
    return ops


# ---------------------------------------------------------------------------- xt person set
def test_person_set_two_step_audited_and_refusals(
    roster: Lv, lv: tuple[Lv, dict[str, str]], pg: pgfixture.Pg
) -> None:
    ops, env = lv
    p = roster.run(OANH, lambda c, _: c.person)
    assert {"dept_head", "chief_accountant", "payment_accountant", "employee"} <= set(p.roles)
    with pg.su() as su:
        rows = su.execute(
            "SELECT key, before, after, proposed_by, approved_by, digest"
            " FROM xt_meta.person_change WHERE tenant_id = %s ORDER BY at",
            (ops.tenant.id,),
        ).fetchall()
        audit = su.execute(
            "SELECT op FROM xt_audit.event WHERE tenant_id = %s AND model LIKE %s",
            (ops.tenant.id, "%person_change"),
        ).fetchall()
        active = dict(su.execute(
            "SELECT key, active FROM xt_meta.person WHERE tenant_id = %s AND key = ANY(%s)",
            (ops.tenant.id, [QUYEN, VINH, OANH]),
        ).fetchall())  # fmt: skip
    assert [r[0] for r in rows] == [OANH, QUYEN, VINH]
    assert rows[0][2]["roles"][-1] == "payment_accountant" and rows[1][2]["active"] is False
    assert {r[3] for r in rows} == {"person:NV-0031"} and {r[4] for r in rows} == {"person:NV-0001"}
    assert len(audit) >= 3  # mỗi lần đổi vào hash-chain của tenant
    assert active == {QUYEN: False, VINH: False, OANH: True}
    look = roster.rt.lookup_actor(ops.tenant)
    assert roster.rt.audit.verify(look).intact

    # key lạ / kế hoạch rỗng / mã sai / tự duyệt: từ chối, không ghi gì
    rc, out = person_set(env, ops.alias, "NV-9999", "--inactive")
    assert rc == 1 and "không có người key" in out
    rc, out = person_set(env, ops.alias, QUYEN, "--inactive")
    assert rc == 1 and "kế hoạch rỗng" in out
    rc, out = person_set(env, ops.alias, QUYEN, "--active", "--confirm", "sha256:" + "0" * 64)
    assert rc == 2 and "không khớp" in out
    rc, out = run_admin(["person", "set", "--tenant", ops.alias, "--key", QUYEN, "--active",
                         "--proposed-by", "a", "--approved-by", "a", "--confirm",
                         "sha256:" + "0" * 64], env)  # fmt: skip
    assert rc == 2 and "tách nhiệm vụ" in out
    with pg.su() as su:
        n = su.execute(
            "SELECT count(*) FROM xt_meta.person_change WHERE tenant_id = %s", (ops.tenant.id,)
        ).fetchone()
    assert n is not None and n[0] == 3
    # người đã tắt không đăng nhập/hành động được
    with pytest.raises(Exception, match="not linked to an active person"):
        roster.caller(QUYEN)


# ------------------------------------------------------------- tiếp nhận: một IT + giám đốc
def _employee(ops: Lv, user: str, dept: dict[str, Any]) -> dict[str, Any]:
    return ops.create(
        NHI,
        "employee",
        {"emp_no": user, "user_id": ops.pid(user), "full_name": user,
         "job_title": "Nhân viên", "department": dept["id"]},
    )  # fmt: skip


def test_onboarding_one_it_director_approves_it_step(roster: Lv) -> None:
    ops = roster
    dept = ops.create(NHI, "department", {"code": "KDLV", "name": "Kinh doanh"})
    emp = _employee(ops, NEW, dept)
    case = ops.create(
        NHI,
        "lifecycle_case",
        {"code": "ON-2026-8001", "employee": emp["id"], "kind": "onboarding",
         "effective_on": "2026-11-02", "initiated_by": ops.pid(NHI)},
    )  # fmt: skip
    grant = ops.create(
        KHANG,
        "access_grant",
        {"employee": emp["id"], "system": "keycloak", "role": "sales", "issued_on": "2026-11-02"},
    )
    data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": ops.pid(NEW)}
    iid, _ = ops.start("employee_onboarding", data, NHI)
    assert ops.steps(ops.act(iid, "hr", NHI)) == {"it", "accounting", "manager"}
    assert ops.candidates(iid, "it") == sorted([ops.pid(KHANG), ops.pid(TRI)])  # Vinh đã tắt
    assert ops.candidates(iid, "accounting") == [ops.pid(OANH)]  # Quyên đã tắt
    with pytest.raises(EngineError, match="người duyệt phải khác"):
        ops.act(iid, "it", KHANG)  # người tạo access_grant không tự duyệt
    ops.act(iid, "it", TRI)

    def body(c: Caller, _: Any) -> dict[str, Any] | None:
        with ops.rt.uow.tenant_tx(c.actor) as cur:
            row = cur.execute(
                "SELECT person, role, requested_by, approved_by FROM xt_meta.person_role_grant"
                " WHERE grant_ref = %s",
                (f"access_grant:{grant['id']}",),
            ).fetchone()
        return dict(row) if row else None

    row = ops.run(TRI, body)
    assert row is not None
    assert (str(row["person"]), row["role"]) == (ops.pid(NEW), "sales")
    assert str(row["requested_by"]) == ops.pid(NHI) and str(row["approved_by"]) == ops.pid(TRI)
    ops.act(iid, "accounting", OANH)
    done = ops.act(iid, "manager", BAO)
    assert done["status"] == "completed", done


# ------------------------------------------------------ đề xuất: một kế toán giữ hai vai trò
def test_payment_same_accountant_both_roles_director_takes_ktt(roster: Lv) -> None:
    ops = roster
    iid, s = ops.start("proposal_approval", {"kind": "payment_request", "amount": 60_000_000},
                       DUNG)  # fmt: skip
    assert ops.steps(s) == {"dept_head"}
    s = ops.act(iid, "dept_head", BAO, "approve")
    assert ops.candidates(iid, "kttt") == [ops.pid(OANH)]  # Quyên đã tắt: không là ứng viên
    s = ops.act(iid, "kttt", OANH, "approve")
    assert ops.candidates(iid, "ktt") == [ops.pid(TRI)]  # Oanh bị distinct_from kttt
    with pytest.raises((DecisionError, AssertionError)):
        ops.act(iid, "ktt", OANH, "approve")
    ops.act(iid, "ktt", TRI, "approve")
    done = ops.act(iid, "director", TRI, "approve")
    assert done["status"] == "completed", done


def test_requester_still_never_approves(roster: Lv) -> None:
    ops = roster
    iid, _ = ops.start("proposal_approval", {"kind": "advance_request", "amount": 60_000_000},
                       OANH)  # fmt: skip
    assert ops.pid(OANH) not in ops.candidates(iid, "dept_head")
    ops.act(iid, "dept_head", TRI, "approve")
    assert ops.candidates(iid, "ktt") == [ops.pid(TRI)]


def test_reactivated_person_is_candidate_again(roster: Lv, lv: tuple[Lv, dict[str, str]]) -> None:
    ops, env = lv
    person_set_confirmed(env, ops.alias, VINH, "--active")
    try:
        dept = ops.create(NHI, "department", {"code": "KDLVB", "name": "Kinh doanh 2"})
        emp = _employee(ops, DUNG, dept)
        case = ops.create(
            NHI,
            "lifecycle_case",
            {"code": "ON-2026-8002", "employee": emp["id"], "kind": "onboarding",
             "effective_on": "2026-11-02", "initiated_by": ops.pid(NHI)},
        )  # fmt: skip
        data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": ops.pid(DUNG)}
        iid, _ = ops.start("employee_onboarding", data, NHI)
        ops.act(iid, "hr", NHI)
        assert ops.pid(VINH) in ops.candidates(iid, "it")
    finally:
        person_set_confirmed(env, ops.alias, VINH, "--inactive")
