# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit đối kháng: hoàn thành bước người qua `transition_record` / REST decision (D-STEP-1..5).

Chạy thật trên Postgres 17 (fixture phiên, container riêng, `docker rm -f -v`) + dispatcher MCP
thật + REST `/workflow/v1` trên uvicorn. Tenant riêng `psa` (chính), `psb` (tenant khác),
`psc` (biến thể gói nhân sự: bước có `when` đứng ngay trước bước đóng hồ sơ).

Quy ước: mỗi test khẳng định HÀNH VI AN TOÀN. Test của lỗi còn mở đánh `xfail(strict=True)`: hôm
nay khai thác thành công -> assert sai -> xfail; khi đã vá -> XPASS -> strict báo FAIL để gỡ dấu.
Test không đánh dấu = vector đã kiểm và chặn đúng (hồi quy).

Chạy: uv run pytest -m integration --no-cov -rA tests/integration/process_steps_audit
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import statistics
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import PACK, ROOT, StackBase, run_admin
from tests.integration.composition.idp import DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import RunningApp, free_port, serve
from tests.integration.composition.shared_tenants import DEPTS, PEOPLE, Ops
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.application.tools import Actor
from xt.domain.identity import PrincipalKind
from xt.entrypoints.composition.runtime import Caller
from xt.entrypoints.serve import prepare
from xt.entrypoints.workflow_http import with_workflow_api

pytestmark = pytest.mark.integration

A, B, C = "psa", "psb", "psc"
WF = "/workflow/v1"
TASK_PACK = ROOT / "packages" / "giao-viec-sla"
HR_PACK = ROOT / "packages" / "nhan-su-vong-doi"
FAR = "2099-12-31T17:00:00+07:00"


class Aud(Ops):
    """`Ops` dùng chung nhưng mọi đường (kể cả `caller`) theo đúng alias tenant."""

    def caller(self, key: str) -> tuple[Caller, Actor]:
        actor = Actor(self.alias, self.sub(key), PrincipalKind.HUMAN, DESK_CLIENT, None)
        return self.rt.resolve(actor, f"it-{uuid.uuid4().hex[:8]}"), actor


@dataclass
class World:
    a: Aud
    b: Aud
    idp: LocalIdp
    app: RunningApp
    pg: pgfixture.Pg
    env: dict[str, str]
    tmp: Path

    def http(self, key: str, alias: str = A) -> httpx.Client:
        tok = self.idp.access_token(
            f"kc-{alias}-{key}", org=alias, aud=["xt-workflow"], scope="openid xt-workflow"
        )
        return httpx.Client(
            base_url=self.app.base, headers={"Authorization": f"Bearer {tok}"}, timeout=30
        )


def _tenant(env: dict[str, str], d: Path, alias: str, packs: list[Path]) -> Any:
    rc, out = run_admin(
        ["tenant", "create", "--alias", alias, "--name", f"Audit {alias}",
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
    (d / f"org-{alias}.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    links = {
        "subjects": {k: f"kc-{alias}-{k}" for k, *_ in PEOPLE},
        "agent_grants": [
            {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
            for k, *_ in PEOPLE
            for c in env["XT_MCP_ALLOWED_CLIENTS"].split(",")
        ],
    }
    (d / f"links-{alias}.json").write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", alias, str(d / f"org-{alias}.json"), "--links",
         str(d / f"links-{alias}.json"), "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    for pack in packs:
        common = ["pack", "install", "--tenant", alias, str(pack)]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, done = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, done
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        return tenant_of_alias(c, alias)


def _variant_hr_pack(d: Path) -> Path:
    """nhan-su-vong-doi với offboarding bỏ `hr_close`: `chief_acct_overdue` (có `when`) đứng
    ngay trước `close_case` — đúng trường hợp tác giả ghi ở D-STEP 'Giới hạn đã biết'."""
    dst = d / "nhan-su-vong-doi"
    shutil.copytree(HR_PACK, dst)
    p = dst / "processes" / "employee_offboarding.json"
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["steps"] = [s for s in spec["steps"] if s.get("id") != "hr_close"]
    p.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


@pytest.fixture(scope="module")
def world(
    stack_base: StackBase,
    pg: pgfixture.Pg,
    idp: LocalIdp,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[World]:
    # Cổng riêng (bind :0): cổng MCP của phiên đã có app khác (stack của test_e2e) giữ khi chạy
    # chung `make test-integration`.
    port = free_port()
    env = {**stack_base.env, "XT_WORKFLOW_PUBLIC_URL": f"http://127.0.0.1:{port}"}
    d = tmp_path_factory.mktemp("xt-psa")
    packs = [PACK, TASK_PACK, HR_PACK]
    ta = _tenant(env, d, A, packs)
    tb = _tenant(env, d, B, packs)
    tc = _tenant(env, d, C, [PACK, TASK_PACK, _variant_hr_pack(d)])
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=port, profile="full")
    prepared = prepare(env, ns)
    comp = prepared.services
    app = serve(with_workflow_api(prepared, env, prepared.http_app()), port)
    a = Aud(comp.runtime, comp.engine, ta, {}, prepared.dispatcher, alias=A)  # type: ignore[union-attr]
    b = Aud(comp.runtime, comp.engine, tb, {}, prepared.dispatcher, alias=B)  # type: ignore[union-attr]
    c = Aud(comp.runtime, comp.engine, tc, {}, prepared.dispatcher, alias=C)  # type: ignore[union-attr]
    try:
        w = World(a, b, idp, app, pg, env, d)
        w.c = c  # type: ignore[attr-defined]
        yield w
    finally:
        app.stop()
        prepared.close()


# ------------------------------------------------------------------------------- tiện ích ---
_SEQ = iter(range(1000, 9999))


def _kind(o: Aud) -> dict[str, Any]:
    kinds = o.search("task_type", {"field": "code", "op": "=", "value": "bao_cao"})
    if kinds:
        return kinds[0]
    return o.create("NV-0001", "task_type", {"code": "bao_cao", "name": "BC"})


def mk_task(o: Aud, creator: str, assigner: str, assignee: str, title: str) -> dict[str, Any]:
    return o.create(
        creator,
        "task",
        {"task_no": f"CV-2026-{next(_SEQ):04d}", "title": title, "kind": _kind(o)["id"],
         "assigner": o.pid(assigner), "assignee": o.pid(assignee), "deadline": FAR,
         "done_criteria": "Có số liệu đầy đủ"},
    )  # fmt: skip


def start_task(
    o: Aud, task: dict[str, Any] | None, starter: str, assignee: str, assigner: str, **params: Any
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "process_id": "task_normal",
        "parameters": {"assignee_user": f"person:{o.pid(assignee)}",
                       "assigner_user": f"person:{o.pid(assigner)}", **params},
        "idempotency_key": f"st-{uuid.uuid4().hex[:12]}",
    }  # fmt: skip
    if task is not None:
        args["subject_ref"] = f"task:{task['id']}"
    return o.tool(starter, "start_process", args)


def ik(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:8]


def iid_of(out: dict[str, Any]) -> str:
    assert out["ok"], out
    return str(out["data"]["process_instance"]).split(":", 1)[1]


def tr(o: Aud, key: str, ref: str, to_state: str, rev: int, **extra: Any) -> dict[str, Any]:
    args = {"ref": ref, "to_state": to_state, "expected_revision": rev,
            "idempotency_key": f"tr-{uuid.uuid4().hex[:12]}", **extra}  # fmt: skip
    return o.tool(key, "transition_record", args)


def rev(o: Aud, model: str, rid: Any) -> int:
    return int(o.get(model, rid)["revision"])


def info(o: Aud, iid: str) -> dict[str, Any]:
    out: dict[str, Any] = o.run("NV-0001", lambda _, pc: pc.service().get(iid))
    return out


def err(out: dict[str, Any]) -> tuple[str, str | None]:
    assert not out.get("ok"), out
    e = out["error"]
    return e["code"], (e.get("details") or {}).get("reason")


def confirm_and_replay(o: Aud, key: str, args_out: dict[str, Any], args: dict[str, Any]) -> Any:
    o.confirm(args_out, key, key)
    return o.tool(key, "transition_record", args)


def task_flow(w: World, title: str) -> tuple[dict[str, Any], str, str]:
    """task KD: NV-0009 giao cho NV-0010, chạy task_normal gắn subject."""
    a = w.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0010", title)
    iid = iid_of(start_task(a, t, "NV-0009", "NV-0010", "NV-0009"))
    return t, f"task:{t['id']}", iid


# ===================================================================== 1. human_only mọi đường ==
def test_human_only_step_never_completes_without_assignee_confirmation(w_hr: Any) -> None:
    """Bước HR `human_only`: agent của chính người được giao chỉ nhận confirmation_required (MCP),
    428 (REST); người khác xác nhận hộ không được; instance và bản ghi đứng nguyên."""
    w, hr = w_hr
    a = w.a
    ref, r = hr["hr_ref"], rev(a, "lifecycle_task", hr["hr_id"])
    out = tr(a, "NV-0003", ref, "done", r)
    assert err(out)[0] == "confirmation_required", out
    with w.http("NV-0003") as c:
        rr = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": r},
                    headers={"Idempotency-Key": ik("hr-rest-1-")})  # fmt: skip
    assert rr.status_code == 428, rr.text
    by_other = a.confirm(out, "NV-0002", "NV-0003")  # trưởng NS bấm xác nhận hộ
    assert by_other.error, by_other
    assert a.get("lifecycle_task", hr["hr_id"])["state"] == "waiting"
    assert {t["step"] for t in info(a, hr["iid"])["tasks"]} == {"hr"}


_CASES = iter(range(101, 999))


@pytest.fixture
def w_hr(world: World) -> tuple[World, dict[str, Any]]:
    """Hồ sơ tiếp nhận MỚI cho mỗi test (tenant psa) khởi động bằng start_process của HR."""
    a = world.a
    if "emp" not in world.__dict__:
        dept = a.create("NV-0003", "department", {"code": "KD", "name": "Kinh doanh"})
        world.__dict__["emp"] = a.create("NV-0003", "employee", {
            "emp_no": "NV-0011", "user_id": a.pid("NV-0011"), "full_name": "NV-0011",
            "job_title": "NV", "department": dept["id"]})  # fmt: skip
    emp = world.__dict__["emp"]

    def new_case() -> dict[str, Any]:
        return a.create("NV-0003", "lifecycle_case", {
            "code": f"ON-2026-{next(_CASES):04d}", "employee": emp["id"], "kind": "onboarding",
            "effective_on": "2026-11-02", "initiated_by": a.pid("NV-0003")})  # fmt: skip

    case, case2 = new_case(), new_case()
    out = a.tool("NV-0003", "start_process", {
        "process_id": "employee_onboarding", "subject_ref": f"lifecycle_case:{case['id']}",
        "parameters": {"case_id": case["id"], "employee_id": emp["id"],
                       "employee_user": f"person:{a.pid('NV-0011')}"},
        "idempotency_key": f"psa-onb-{case['code']}"})  # fmt: skip
    iid = iid_of(out)
    tasks = {t["step"]: t for t in a.search("lifecycle_task", {
        "field": "lifecycle_case", "op": "=", "value": case["id"]})}  # fmt: skip
    return world, {
        "iid": iid,
        "case": case,
        "case2": case2,
        "emp": emp,
        "tasks": tasks,
        "hr_id": tasks["hr"]["id"],
        "hr_ref": f"lifecycle_task:{tasks['hr']['id']}",
    }


def _hr_done(w_hr: Any) -> dict[str, Any]:
    """Đưa hồ sơ qua bước hr (xác nhận thật) để mở it/accounting/manager."""
    w, hr = w_hr
    a = w.a
    if any(t["step"] == "hr" for t in info(a, hr["iid"])["tasks"]):
        args = {"ref": hr["hr_ref"], "to_state": "done",
                "expected_revision": rev(a, "lifecycle_task", hr["hr_id"]),
                "idempotency_key": f"psa-hr-done-{hr['iid'][:8]}"}  # fmt: skip
        out = a.tool("NV-0003", "transition_record", args)
        done = confirm_and_replay(a, "NV-0003", out, args)
        assert done["ok"], done
    out_hr: dict[str, Any] = hr
    return out_hr


# ============================================================ 2. values injection (lỗi mở) ====
def test_values_cannot_rewrite_fields_employee_cannot_edit(world: World) -> None:
    """Người làm (employee, model `task` chỉ cho employee quyền read) hoàn thành bước accept (L1,
    agent tự làm, không ai xác nhận) kèm values sửa hạn chót/ưu tiên/tiêu chí/người giao."""
    a = world.a
    t, ref, _ = task_flow(world, "Báo cáo tồn kho")
    evil = {"deadline": "2100-01-01T00:00:00+07:00", "priority": "low",
            "done_criteria": "Không cần gì cả", "assigner": f"person:{a.pid('NV-0010')}",
            "escalated": True, "process_instance_id": "giả"}  # fmt: skip
    out = tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]), values=evil)
    row = a.get("task", t["id"])
    world.__dict__["psa01_task"] = {"level": out.get("meta", {}).get("effective_level"), "row": row}
    assert not out.get("ok"), (out, row)
    assert row["done_criteria"] == "Có số liệu đầy đủ" and row["priority"] == "medium"


def test_values_cannot_rebind_lifecycle_task_to_other_case(w_hr: Any) -> None:
    """Quản trị IT (lifecycle_task chỉ read) hoàn thành bước IT kèm values chuyển bản ghi sang hồ
    sơ khác + đổi `step`/`owner_unit` (khóa `match` của bước)."""
    w, _ = w_hr
    hr = _hr_done(w_hr)
    a = w.a
    it = hr["tasks"]["it"]
    args = {"ref": f"lifecycle_task:{it['id']}", "to_state": "done",
            "expected_revision": rev(a, "lifecycle_task", it["id"]),
            "values": {"lifecycle_case": f"lifecycle_case:{hr['case2']['id']}", "step": "hr",
                       "owner_unit": "hr", "name": "Đã sửa bởi IT"},
            "idempotency_key": f"psa-it-rebind-{hr['iid'][:8]}"}  # fmt: skip
    out = a.tool("NV-0005", "transition_record", args)
    if not out.get("ok") and out["error"]["code"] == "confirmation_required":
        out = confirm_and_replay(a, "NV-0005", out, args)
    row = a.get("lifecycle_task", it["id"])
    inbox = a.tool("NV-0005", "get_inbox", {"kinds": ["task"], "limit": 100})
    w.__dict__["psa01_hr"] = {
        "row": row,
        "steps": [x["step"] for x in info(a, hr["iid"])["tasks"]],
        "inbox_it": [i for i in inbox["data"]["items"] if i["step"] == "it"],
    }
    assert row["lifecycle_case"] == hr["case"]["id"] and row["step"] == "it", (out, row)


# ======================================= 3. ghi thẳng trường trạng thái (update_record) =======
def test_update_record_cannot_set_step_state_directly(world: World) -> None:
    """Agent của người giao (dept_head, L1) đặt thẳng task.state=done trong khi quy trình còn ở
    bước accept: bỏ qua nghiệm thu human_only ở mức bản ghi."""
    a = world.a
    t, ref, iid = task_flow(world, "Báo cáo nhanh")
    out = a.tool("NV-0009", "update_record", {
        "ref": ref, "expected_revision": rev(a, "task", t["id"]),
        "set": {"state": "done", "result": "xong", "completed_at": "2026-10-16T10:00:00+07:00",
                "progress": 100},
        "idempotency_key": f"up-{uuid.uuid4().hex[:8]}"})  # fmt: skip
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"accept"}
    assert not out.get("ok"), out
    assert a.get("task", t["id"])["state"] == "new"


def test_hr_cannot_mark_other_units_step_done_via_update_record(w_hr: Any) -> None:
    w, _ = w_hr
    hr = _hr_done(w_hr)
    a = w.a
    acc = hr["tasks"]["accounting"]
    out = a.tool("NV-0003", "update_record", {
        "ref": f"lifecycle_task:{acc['id']}",
        "expected_revision": rev(a, "lifecycle_task", acc["id"]),
        "set": {"state": "done"}, "idempotency_key": f"psa-hr-acc-{hr['iid'][:8]}"})  # fmt: skip
    assert "accounting" in {x["step"] for x in info(a, hr["iid"])["tasks"]}
    assert not out.get("ok"), out


def test_data_api_has_no_write_path(world: World) -> None:
    """/data (PostgREST): view api_t<seq> do xt_api_owner sở hữu, xt_api_owner chỉ SELECT bảng
    model -> không có đường PATCH trạng thái bước qua /data cho task/lifecycle_task."""
    seq = world.a.tenant.seq
    with world.pg.su() as su:
        for model in ("task", "lifecycle_task"):
            row = su.execute(
                "SELECT has_table_privilege('xt_api_owner', %s, 'UPDATE'),"
                " has_table_privilege('xt_api_owner', %s, 'INSERT')",
                (f"xt_t{seq}.{model}", f"xt_t{seq}.{model}"),
            ).fetchone()
            assert row is not None and not row[0] and not row[1], (model, row)
            others = su.execute(
                "SELECT grantee, privilege_type FROM information_schema.role_table_grants"
                " WHERE table_schema=%s AND table_name=%s AND grantee <> 'xt_api_owner'"
                " AND privilege_type IN ('INSERT','UPDATE','DELETE')",
                (f"api_t{seq}", model),
            ).fetchall()
            assert not others, (model, others)
        # Ghi chú: xt_app UPDATE được cột state (không managed_by) — chặn ở precheck update_record.
        st = su.execute(
            "SELECT has_column_privilege('xt_app', %s, 'state', 'UPDATE')", (f"xt_t{seq}.task",)
        ).fetchone()
        assert st is not None and st[0] is True


# ============================== 4. instance tự dựng trỏ vào bản ghi không đọc được (lỗi mở) ===
def test_crafted_instance_cannot_drive_foreign_task(world: World) -> None:
    """Trưởng IT (NV-0004) không đọc được task của phòng Nhân sự. Khởi động task_normal KHÔNG
    subject, `parameters.task_id` = task nạn nhân, tự giao cho mình -> inbox lộ tiêu đề, rồi
    transition_record (L1, không xác nhận) ghi trạng thái + values lên task nạn nhân."""
    a = world.a
    victim = mk_task(a, "NV-0002", "NV-0002", "NV-0003", "Bảng lương bí mật tháng 10")
    vref = f"task:{victim['id']}"
    legit = iid_of(start_task(a, victim, "NV-0002", "NV-0003", "NV-0002"))
    seen = a.tool("NV-0004", "get_record", {"ref": vref})
    assert (seen["ok"] and seen["data"] is None) or not seen["ok"], seen  # không đọc được
    started = start_task(a, None, "NV-0004", "NV-0004", "NV-0004", task_id=vref)
    leaked = []
    hijack: dict[str, Any] = {"ok": False}
    if started.get("ok"):
        inbox = a.tool("NV-0004", "get_inbox", {"kinds": ["task"], "limit": 100})
        leaked = [i for i in inbox["data"]["items"] if i["ref"] == vref]
        if leaked:
            hijack = tr(a, "NV-0004", vref, "accepted", leaked[0]["revision"],
                        values={"deadline": "2026-10-17T08:00:00+07:00",
                                "description": "đã bị sửa"})  # fmt: skip
    row = a.get("task", victim["id"])
    world.__dict__["psa03"] = {
        "started": started.get("ok"),
        "leaked": leaked,
        "hijack_ok": hijack.get("ok"),
        "row": row,
        "legit_steps": [x["step"] for x in info(a, legit)["tasks"]],
    }
    assert not leaked, leaked  # tiêu đề/trạng thái/revision task phòng khác lộ qua inbox
    assert not hijack.get("ok") and row["state"] == "new", (hijack, row)


def test_employee_agent_cannot_craft_instance(world: World) -> None:
    """Biến thể PSA-03 cho nhân viên: statement `process.start` có role:agent nhưng quyền của agent
    giao với quyền chủ (employee không có process.start) -> bị chặn."""
    a = world.a
    victim = mk_task(a, "NV-0002", "NV-0002", "NV-0003", "Việc NS khác")
    out = start_task(a, None, "NV-0010", "NV-0010", "NV-0013", task_id=f"task:{victim['id']}")
    assert err(out)[0] == "access_denied", out


def test_rest_decision_unheld_step_same_answer_as_ghost(world: World) -> None:
    """Người ngoài cuộc (NV-0013) POST decision lên task có thật (không đọc được) và lên ref ma:
    phải cùng một câu trả lời như MCP (not_found/404), không lộ tồn tại qua details.reason."""
    a = world.a
    t, ref, _ = task_flow(world, "Việc oracle")
    r = rev(a, "task", t["id"])
    mcp_real = err(tr(a, "NV-0013", ref, "accepted", r))
    mcp_ghost = err(tr(a, "NV-0013", f"task:{uuid.uuid4()}", "accepted", r))
    assert mcp_real == mcp_ghost  # MCP đúng: cùng not_found

    def post(target: str) -> tuple[int, str, Any]:
        with world.http("NV-0013") as c:
            rr = c.post(f"{WF}/tasks/{target}/decision",
                        json={"decision": "approve", "expected_revision": r},
                        headers={"Idempotency-Key": ik("psa-orc-")})  # fmt: skip
        e = rr.json()["error"]
        return rr.status_code, e["code"], (e.get("details") or {}).get("reason")

    real, ghost = post(ref), post(f"task:{uuid.uuid4()}")
    world.__dict__["psa06"] = {"mcp": mcp_real, "rest_real": real, "rest_ghost": ghost}
    assert real == ghost == (404, "not_found", None), (real, ghost)


# =============================================== 5. người/instance/tenant không đúng (PASS) =====
def test_not_assignee_other_instance_other_tenant(world: World) -> None:
    a, b = world.a, world.b
    t1, ref1, _ = task_flow(world, "Việc một")
    r1 = rev(a, "task", t1["id"])
    # Người khác cùng phòng (NV-0013, employee) không giữ bước: như không tồn tại (không đọc được).
    assert err(tr(a, "NV-0013", ref1, "accepted", r1))[0] in ("not_found", "access_denied")
    # Người giao (đọc được) không giữ bước accept: not_assigned.
    assert err(tr(a, "NV-0009", ref1, "accepted", r1)) == ("access_denied", "not_assigned")
    # NV-0010 giữ bước accept của t2 nhưng gửi ref t1 của instance khác do NV-0013 làm.
    t3 = mk_task(a, "NV-0009", "NV-0009", "NV-0013", "Việc ba")
    iid_of(start_task(a, t3, "NV-0009", "NV-0013", "NV-0009"))
    out = tr(a, "NV-0010", f"task:{t3['id']}", "accepted", rev(a, "task", t3["id"]))
    assert err(out)[0] in ("not_found", "access_denied"), out
    # Tenant khác: cùng khóa người, ref tenant psa -> không tồn tại; REST cũng 404.
    out = tr(b, "NV-0010", ref1, "accepted", r1)
    assert err(out)[0] == "not_found", out
    with world.http("NV-0010", B) as c:
        rr = c.post(f"{WF}/tasks/{ref1}/decision", json={"decision": "approve",
                    "expected_revision": r1},
                    headers={"Idempotency-Key": ik("psb-x-1-")})  # fmt: skip
    assert rr.status_code in (403, 404), rr.text  # REST đi nhánh decide_request (PSA-06)
    assert a.get("task", t1["id"])["state"] == "new"


def test_stale_revision_wrong_state_and_skip(world: World) -> None:
    a = world.a
    t, ref, iid = task_flow(world, "Việc revision")
    r = rev(a, "task", t["id"])
    if r > 1:
        assert err(tr(a, "NV-0010", ref, "accepted", r - 1))[0] == "revision_conflict"
    assert err(tr(a, "NV-0010", ref, "accepted", r + 5))[0] == "revision_conflict"
    # Nhảy bước: đặt thẳng trạng thái của bước sau / trạng thái không thuộc to_states.
    for bad in ("awaiting_acceptance", "done", "cancelled", "blocked", "khong_co"):
        code, reason = err(tr(a, "NV-0010", ref, bad, r))
        assert (code, reason) == ("precondition_failed", "transition_not_allowed"), bad
    # Đặt state qua values.
    out = tr(a, "NV-0010", ref, "accepted", r, values={"state": "done"})
    assert err(out) == ("invalid_request", "state_in_values"), out
    # Trường không có trong model / cột hệ thống.
    out = tr(a, "NV-0010", ref, "accepted", r, values={"owner_id": a.pid("NV-0010")})
    assert err(out)[0] == "invalid_request", out
    assert a.get("task", t["id"])["state"] == "new"
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"accept"}
    # REST: decision không ánh xạ được sang to_state của bước (reject ở bước accept).
    with world.http("NV-0010") as c:
        rr = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "reject",
                    "expected_revision": r},
                    headers={"Idempotency-Key": ik("psa-rej-1-")})  # fmt: skip
    assert rr.status_code in (400, 403, 409, 412), rr.text
    assert a.get("task", t["id"])["state"] == "new"


def test_concurrent_double_completion_advances_once(world: World) -> None:
    """Hai agent của cùng người được giao gửi accept đồng thời (khóa idempotency khác nhau)."""
    a = world.a
    t, ref, iid = task_flow(world, "Việc đồng thời")
    r = rev(a, "task", t["id"])
    outs: list[dict[str, Any]] = []
    barrier = threading.Barrier(4)

    def go() -> None:
        barrier.wait()
        outs.append(tr(a, "NV-0010", ref, "accepted", r))

    th = [threading.Thread(target=go) for _ in range(4)]
    for x in th:
        x.start()
    for x in th:
        x.join(60)
    ok = [o for o in outs if o.get("ok")]
    assert len(outs) == 4 and len(ok) == 1, outs
    hist = [(h["step"], h["outcome"]) for h in info(a, iid)["history"]]
    assert hist == [("accept", "done")], hist
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"work"}
    assert int(a.get("task", t["id"])["revision"]) == r + 1


def test_parallel_steps_of_two_instances_no_deadlock(world: World) -> None:
    """open_steps khóa FOR UPDATE instance gắn bản ghi rồi MỌI instance đang có việc của người gọi:
    hai lời gọi song song trên hai task của cùng người làm khóa ngược thứ tự -> phải không
    deadlock/500 (khóa chuỗi audit của tenant đi trước tuần tự hóa)."""
    a = world.a
    flows = [task_flow(world, f"Song song {i}") for i in range(3)]
    outs: list[dict[str, Any]] = []
    barrier = threading.Barrier(len(flows))

    def go(t: dict[str, Any], ref: str) -> None:
        r = rev(a, "task", t["id"])
        barrier.wait()
        outs.append(tr(a, "NV-0010", ref, "accepted", r))

    th = [threading.Thread(target=go, args=(t, ref)) for t, ref, _ in flows]
    for x in th:
        x.start()
    for x in th:
        x.join(90)
    assert len(outs) == len(flows) and all(o.get("ok") for o in outs), outs


def test_concurrent_rest_and_mcp_completion(world: World) -> None:
    """REST decision và MCP transition_record cùng lúc trên cùng bước work."""
    a = world.a
    t, ref, iid = task_flow(world, "Việc REST+MCP")
    assert tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]))["ok"]
    r = rev(a, "task", t["id"])
    res: dict[str, Any] = {}
    barrier = threading.Barrier(2)

    def mcp() -> None:
        barrier.wait()
        res["mcp"] = tr(a, "NV-0010", ref, "awaiting_acceptance", r, values={"result": "MCP"})

    def rest() -> None:
        with world.http("NV-0010") as c:
            barrier.wait()
            res["rest"] = c.post(
                f"{WF}/tasks/{ref}/decision",
                json={"decision": "approve", "expected_revision": r, "values": {"result": "REST"}},
                headers={"Idempotency-Key": f"psa-rm-{uuid.uuid4().hex[:6]}"},
            )

    th = [threading.Thread(target=mcp), threading.Thread(target=rest)]
    for x in th:
        x.start()
    for x in th:
        x.join(60)
    oks = int(bool(res["mcp"].get("ok"))) + int(res["rest"].status_code == 200)
    assert oks == 1, (res["mcp"], res["rest"].status_code, res["rest"].text)
    hist = [h["step"] for h in info(a, iid)["history"]]
    assert hist == ["accept", "work"], hist


def test_escalated_step_old_assignee_rejected(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đổi người giữ bước giữa chừng (leo thang SLA PT4H của accept -> người giao): người cũ hết
    quyền ở cả MCP và REST, người nhận leo thang làm được."""
    import SpiffWorkflow.bpmn.specs.event_definitions.timer as spiff_timer

    from tests.unit.process.support import FakeDT

    a = world.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0010", "Việc leo thang")
    FakeDT.offset = timedelta(0)
    monkeypatch.setattr(spiff_timer, "datetime", FakeDT)
    try:
        iid = iid_of(start_task(a, t, "NV-0009", "NV-0010", "NV-0009"))
        FakeDT.offset = timedelta(hours=5)
        a.run("NV-0001", lambda _, pc: pc.service().tick(iid))
    finally:
        FakeDT.offset = timedelta(0)
    (step,) = info(a, iid)["tasks"]
    assert step["candidates"] == [a.pid("NV-0009")], step
    ref, r = f"task:{t['id']}", rev(a, "task", t["id"])
    out = tr(a, "NV-0010", ref, "accepted", r)
    assert err(out)[0] in ("access_denied", "not_found"), out
    with world.http("NV-0010") as c:
        rr = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": r},
                    headers={"Idempotency-Key": ik("psa-old-")})  # fmt: skip
    world.__dict__["rest_old_assignee"] = (rr.status_code, rr.json()["error"])
    assert rr.status_code in (400, 403, 404), rr.text
    assert a.get("task", t["id"])["state"] == "new"
    ok = tr(a, "NV-0009", ref, "accepted", r)
    assert ok["ok"], ok


def test_record_assignee_edit_does_not_move_step(world: World) -> None:
    """Ghi nhận: sửa task.assignee bằng update_record không đổi người giữ bước -> người cũ vẫn
    hoàn thành được, bản ghi và engine lệch nhau (PSA-05, hành vi theo thiết kế, ghi doc)."""
    a = world.a
    t, ref, _ = task_flow(world, "Việc sửa assignee")
    up = a.tool("NV-0009", "update_record", {
        "ref": ref, "expected_revision": rev(a, "task", t["id"]),
        "set": {"assignee": f"person:{a.pid('NV-0013')}"},
        "idempotency_key": f"up-{uuid.uuid4().hex[:8]}"})  # fmt: skip
    assert up["ok"], up
    old = tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]))
    new = a.tool("NV-0013", "get_inbox", {"kinds": ["task"], "limit": 100})
    assert old["ok"], old  # người cũ (theo engine) vẫn làm được
    assert not [i for i in new["data"]["items"] if i["ref"] == ref]


def test_deactivated_assignee_cannot_complete(world: World) -> None:
    a = world.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0012", "Việc người nghỉ")
    iid_of(start_task(a, t, "NV-0009", "NV-0012", "NV-0009"))
    ref, r = f"task:{t['id']}", rev(a, "task", t["id"])
    with world.pg.su() as su:
        su.execute("UPDATE xt_meta.person SET active=false WHERE tenant_id=%s AND id=%s",
                   (a.tenant.id, a.pid("NV-0012")))  # fmt: skip
    try:
        out = tr(a, "NV-0012", ref, "accepted", r)
        assert not out.get("ok"), out
        with world.http("NV-0012") as c:
            rr = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                        "expected_revision": r},
                        headers={"Idempotency-Key": ik("psa-dead-")})  # fmt: skip
        assert rr.status_code in (401, 403, 404), rr.text
        assert a.get("task", t["id"])["state"] == "new"
    finally:
        with world.pg.su() as su:
            su.execute("UPDATE xt_meta.person SET active=true WHERE tenant_id=%s AND id=%s",
                       (a.tenant.id, a.pid("NV-0012")))  # fmt: skip


def test_sod_worker_cannot_accept_own_work(world: World) -> None:
    """Tự giao cho mình (assigner = assignee = NV-0009): accept/work L1 được, nghiệm thu bị SoD
    distinct_from(work) chặn ngay ở plan — MCP và REST, không tạo PendingOperation."""
    a = world.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0009", "Tự giao")
    iid = iid_of(start_task(a, t, "NV-0009", "NV-0009", "NV-0009"))
    ref = f"task:{t['id']}"
    assert tr(a, "NV-0009", ref, "accepted", rev(a, "task", t["id"]))["ok"]
    assert tr(a, "NV-0009", ref, "awaiting_acceptance", rev(a, "task", t["id"]),
              values={"result": "xong"})["ok"]  # fmt: skip
    out = tr(a, "NV-0009", ref, "done", rev(a, "task", t["id"]), reason="ok")
    assert err(out)[0] == "access_denied", out
    with world.http("NV-0009") as c:
        rr = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": rev(a, "task", t["id"])},
                    headers={"Idempotency-Key": ik("psa-sod-1-")})  # fmt: skip
    assert rr.status_code == 403, rr.text
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"acceptance"}


def test_rest_values_and_human_only_same_service(world: World) -> None:
    """REST decision = cùng dispatcher: accept/work L1 chạy ngay, values rule model áp dụng;
    nghiệm thu human_only -> 428 + confirmation; values cho đề nghị -> 400."""
    a = world.a
    t, ref, iid = task_flow(world, "Việc REST")
    with world.http("NV-0010") as c:
        r1 = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": rev(a, "task", t["id"])},
                    headers={"Idempotency-Key": ik("psa-rest-a-")})  # fmt: skip
        assert r1.status_code == 200, r1.text
        r2 = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": rev(a, "task", t["id"])},
                    headers={"Idempotency-Key": ik("psa-rest-w0-")})  # fmt: skip
        assert r2.status_code in (400, 409, 422), r2.text  # rule r1: thiếu result
        r3 = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": rev(a, "task", t["id"]), "values": {"result": "ok"}},
                    headers={"Idempotency-Key": ik("psa-rest-w1-")})  # fmt: skip
        assert r3.status_code == 200, r3.text
    with world.http("NV-0009") as c:
        r4 = c.post(f"{WF}/tasks/{ref}/decision", json={"decision": "approve",
                    "expected_revision": rev(a, "task", t["id"])},
                    headers={"Idempotency-Key": ik("psa-rest-acc-")})  # fmt: skip
    assert r4.status_code == 428, r4.text
    assert a.get("task", t["id"])["state"] == "awaiting_acceptance"
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"acceptance"}


# ======================================================= 6. GET instance + inbox (lộ thông tin) =
def test_get_instance_unbound_participants_only_same_404(world: World) -> None:
    a = world.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0010", "Việc không subject")
    out = start_task(a, None, "NV-0009", "NV-0010", "NV-0009", task_id=f"task:{t['id']}")
    iid = iid_of(out)
    with world.http("NV-0010") as c:
        mine = c.get(f"{WF}/instances/{iid}")
    assert mine.status_code == 200, mine.text
    assert mine.json()["data"]["request"] is None
    with world.http("NV-0013") as c:
        other = c.get(f"{WF}/instances/{iid}")
        ghost = c.get(f"{WF}/instances/{uuid.uuid4()}")
    with world.http("NV-0010", B) as c:
        cross = c.get(f"{WF}/instances/{iid}")
    assert other.status_code == ghost.status_code == cross.status_code == 404

    def shape(r: httpx.Response) -> Any:
        body = r.json()
        body.pop("meta", None)
        body.get("error", {}).pop("trace_id", None)
        return body, sorted(k for k in r.headers if k not in ("date", "content-length"))

    assert shape(other) == shape(ghost) == shape(cross)


def test_get_instance_timing_reported(world: World) -> None:
    """Đo (không khẳng định chặt) chênh lệch thời gian 404 giữa instance thật của người khác và id
    ma; ghi vào docs/archive/audit-process-steps.md."""
    a = world.a
    t = mk_task(a, "NV-0009", "NV-0009", "NV-0010", "Việc đo thời gian")
    iid = iid_of(start_task(a, None, "NV-0009", "NV-0010", "NV-0009", task_id=f"task:{t['id']}"))
    real, ghost = [], []
    with world.http("NV-0013") as c:
        for _ in range(3):
            c.get(f"{WF}/instances/{iid}")
        for _ in range(25):
            s = time.perf_counter()
            c.get(f"{WF}/instances/{iid}")
            real.append(time.perf_counter() - s)
            s = time.perf_counter()
            c.get(f"{WF}/instances/{uuid.uuid4()}")
            ghost.append(time.perf_counter() - s)
    mr, mg = statistics.median(real) * 1000, statistics.median(ghost) * 1000
    print(f"\nTIMING GET /instances 404: instance thật {mr:.1f} ms, id ma {mg:.1f} ms")
    (world.tmp / "timing.txt").write_text(f"{mr:.2f} {mg:.2f}\n")
    assert mr < mg * 10  # chỉ chặn trường hợp vô lý; số đo nằm trong tài liệu


def test_inbox_only_my_steps(w_hr: Any) -> None:
    w, _ = w_hr
    hr = _hr_done(w_hr)
    a = w.a
    for key, mine in (("NV-0005", {"it"}), ("NV-0007", {"accounting"}), ("NV-0009", {"manager"}),
                      ("NV-0011", set()), ("NV-0013", set())):  # fmt: skip
        out = a.tool(key, "get_inbox", {"kinds": ["task", "approval"], "limit": 100})
        assert out["ok"], out
        steps = {
            i["step"]
            for i in out["data"]["items"]
            if i["ref"].startswith("lifecycle_task:")
            and i["ref"].split(":", 1)[1] in {t["id"] for t in hr["tasks"].values()}
        }
        assert steps == mine, (key, steps)
    # Instance gắn subject (hồ sơ): người chỉ giữ bước nhưng không đọc được hồ sơ -> 404 instance?
    with w.http("NV-0011") as c:  # nhân viên mới không tham gia bước nào
        assert c.get(f"{WF}/instances/{hr['iid']}").status_code == 404


# ===================================== 7. bước có điều kiện ngay trước đóng hồ sơ (lỗi tác giả) =
def test_conditional_step_right_before_close_keeps_done(world: World) -> None:
    c: Aud = world.c  # type: ignore[attr-defined]
    dept = c.create("NV-0003", "department", {"code": "KD", "name": "Kinh doanh"})
    emp = c.create("NV-0003", "employee", {"emp_no": "NV-0012", "user_id": c.pid("NV-0012"),
                   "full_name": "NV-0012", "job_title": "NV", "department": dept["id"],
                   "state": "permanent", "hired_on": "2020-01-01"})  # fmt: skip
    asset = c.create("NV-0005", "asset_issue", {"employee": emp["id"], "kind": "laptop",
                     "asset_code": "LT-0012", "issued_on": "2020-01-01"})  # fmt: skip
    case = c.create("NV-0003", "lifecycle_case", {"code": "OF-2026-0201", "employee": emp["id"],
                    "kind": "offboarding", "effective_on": "2026-11-30",
                    "initiated_by": c.pid("NV-0003")})  # fmt: skip
    data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": c.pid("NV-0012")}
    iid, _ = c.start("employee_offboarding", data, "NV-0003")
    c.act(iid, "hr_confirm", "NV-0003")
    c.act(iid, "manager", "NV-0009")
    c.act(iid, "it", "NV-0005")
    s = c.act(iid, "accounting", "NV-0007")
    assert c.steps(s) == {"chief_acct_overdue"}
    c.run("NV-0005", lambda cl, _: c.rt.records.update(
        cl.actor, "asset_issue", uuid.UUID(asset["id"]), int(asset["revision"]),
        {"revoked_on": "2026-11-30"}))  # fmt: skip
    chief = next(t for t in c.search("lifecycle_task", {"field": "lifecycle_case", "op": "=",
                 "value": case["id"]}) if t["step"] == "chief_acct_overdue")  # fmt: skip
    args = {
        "ref": f"lifecycle_task:{chief['id']}",
        "to_state": "done",
        "expected_revision": int(chief["revision"]),
        "idempotency_key": "psc-chief",
    }
    out = c.tool("NV-0008", "transition_record", args)
    done = confirm_and_replay(c, "NV-0008", out, args)
    assert done["ok"], done
    assert info(c, iid)["status"] == "completed"
    row = c.get("lifecycle_task", chief["id"])
    world.__dict__["psa04_row"] = row
    assert row["state"] == "done" and row["note"] is None, row


# ============================================================ VÒNG 2: thử vượt bản vá ========
def test_r2_writable_fields_alias_case_unicode(world: World) -> None:
    """writable_fields bước work = ["result"]. Gửi biến thể tên (hoa/thường, thừa khoảng trắng,
    chữ Unicode giống): phải bị chặn, không ghi; chỉ đúng "result" mới ghi."""
    a = world.a
    t, ref, _ = task_flow(world, "Việc alias trường")
    assert tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]))["ok"]
    r = rev(a, "task", t["id"])
    unicode_e = "r\u0435sult"  # chu Cyrillic, khong phai e Latin
    for bad in ("Result", "RESULT", "result ", " result", unicode_e, "result\u200b", "r\u00e9sult"):
        out = tr(a, "NV-0010", ref, "awaiting_acceptance", r, values={bad: "x", "result": "ok"})
        assert err(out)[0] == "invalid_request", (bad, out)
    assert a.get("task", t["id"])["state"] == "accepted"  # chưa ghi gì
    ok = tr(a, "NV-0010", ref, "awaiting_acceptance", r, values={"result": "Đúng tên trường"})
    assert ok["ok"], ok
    assert a.get("task", t["id"])["result"] == "Đúng tên trường"


def test_r2_writable_fields_empty_step_rejects_any_value(world: World) -> None:
    """Bước accept có writable_fields rỗng: mọi values (kể cả trường hợp lệ của model) bị chặn."""
    a = world.a
    t, ref, _ = task_flow(world, "Việc accept không values")
    for v in ({"result": "x"}, {"priority": "low"}, {"progress": 50}):
        out = tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]), values=v)
        assert err(out) == ("invalid_request", None) or out["error"]["code"] == "invalid_request"
        assert (
            out["error"]["details"].get("reason") in (None, "field_not_writable")
            or out["error"]["code"] == "invalid_request"
        ), out
    assert a.get("task", t["id"])["state"] == "new"


def test_r2_values_on_skip_step_not_written(world: World) -> None:
    """values gửi kèm to_state không hợp lệ (nhảy bước): precondition_failed TRƯỚC khi chạm values,
    không ghi gì lên bản ghi."""
    a = world.a
    t, ref, iid = task_flow(world, "Việc nhảy bước có values")
    r = rev(a, "task", t["id"])
    out = tr(a, "NV-0010", ref, "done", r, values={"result": "lén", "progress": 100})
    assert err(out) == ("precondition_failed", "transition_not_allowed"), out
    out = tr(a, "NV-0010", ref, "awaiting_acceptance", r, values={"result": "lén"})
    assert err(out)[0] == "precondition_failed", out  # bước accept chưa xong
    row = a.get("task", t["id"])
    assert row["state"] == "new" and row["result"] is None
    assert {x["step"] for x in info(a, iid)["tasks"]} == {"accept"}


def test_r2_start_ref_other_model_same_id(world: World) -> None:
    """parameters.task_id trỏ model khác (prefix sai) hoặc bare-uuid của bản ghi không đọc được:
    not_found đồng nhất, không tạo instance."""
    a = world.a
    victim = mk_task(a, "NV-0002", "NV-0002", "NV-0003", "Task NS kín")
    before = len(a.search("task", {"field": "state", "op": "=", "value": "new"}))
    for val in (
        f"lifecycle_task:{victim['id']}",
        f"employee:{victim['id']}",
        f"xrn:psb:task:{victim['id']}",
        victim["id"],
        f"task:{uuid.uuid4()}",
    ):
        out = start_task(a, None, "NV-0004", "NV-0004", "NV-0004", task_id=val)
        assert err(out)[0] == "not_found", (val, out)
    # Bare-uuid task đọc được của chính NV-0004: khởi động được (đối chứng dương).
    mine = mk_task(a, "NV-0004", "NV-0004", "NV-0004", "Task IT của mình")
    out = start_task(a, None, "NV-0004", "NV-0004", "NV-0004", task_id=mine["id"])
    assert out["ok"], out
    after = len(a.search("task", {"field": "state", "op": "=", "value": "new"}))
    assert after == before + 1  # chỉ task mình tạo thêm, không có instance lên task nạn nhân


def test_r2_start_ref_nested_or_subject_unreadable(world: World) -> None:
    """subject_ref không đọc được -> not_found; ref model khác giấu trong tham số thường (không
    phải khóa match) vẫn bị quét (nhánh chung của _check_start_refs)."""
    a = world.a
    victim = mk_task(a, "NV-0002", "NV-0002", "NV-0003", "Task NS kín 2")
    out = a.tool("NV-0004", "start_process", {
        "process_id": "task_normal", "subject_ref": f"task:{victim['id']}",
        "parameters": {"assignee_user": f"person:{a.pid('NV-0004')}",
                       "assigner_user": f"person:{a.pid('NV-0004')}"},
        "idempotency_key": ik("r2-subj-")})  # fmt: skip
    assert err(out)[0] == "not_found", out


def test_r2_toctou_role_revoked_between_start_and_step(world: World) -> None:
    """Quyền bị thu hồi SAU start_process, TRƯỚC transition_record: lời gọi transition tự
    authorize lại -> access_denied, không ghi. Khôi phục grant sau đó."""
    a = world.a
    t, ref, _ = task_flow(world, "Việc TOCTOU")
    r = rev(a, "task", t["id"])
    with world.pg.su() as su:
        row0 = su.execute(
            "SELECT roles FROM xt_meta.agent_grant WHERE person=%s AND client_id=%s",
            (a.pid("NV-0010"), a.agent("NV-0010").client_id),
        ).fetchone()
        assert row0 is not None
        old = row0[0]
        su.execute(
            "UPDATE xt_meta.agent_grant SET roles='{}' WHERE person=%s AND client_id=%s",
            (a.pid("NV-0010"), a.agent("NV-0010").client_id),
        )
    try:
        out = tr(a, "NV-0010", ref, "accepted", r)
        assert err(out)[0] == "access_denied", out
        assert a.get("task", t["id"])["state"] == "new"
    finally:
        with world.pg.su() as su:
            su.execute(
                "UPDATE xt_meta.agent_grant SET roles=%s WHERE person=%s AND client_id=%s",
                (old, a.pid("NV-0010"), a.agent("NV-0010").client_id),
            )
    assert tr(a, "NV-0010", ref, "accepted", rev(a, "task", t["id"]))["ok"]


def _clone_pack_with_writable(d: Path, name: str, step: str, fields: list[str]) -> Path:
    dst = d / f"evil-{uuid.uuid4().hex[:6]}"
    shutil.copytree(TASK_PACK, dst)
    proc = dst / "processes" / f"{name}.json"
    spec = json.loads(proc.read_text(encoding="utf-8"))
    for st in spec["steps"]:
        if st["id"] == step:
            st["complete_via"]["writable_fields"] = fields
    proc.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return dst


def test_r2_install_rejects_malicious_writable_fields(world: World) -> None:
    """Cài gói khai writable_fields là trường ref/user hoặc trường không có -> KHÔNG CÀI ĐƯỢC
    (guard _complete_via_problems ở plan_install). Tenant mới, chưa cài gì."""
    env, d = world.env, world.tmp
    _tenant(env, d, "psm", [PACK])  # tenant trống, chỉ có gói đề nghị
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    for fields in (["kind"], ["assignee"], ["khong_co_truong"], ["result", "kind"]):
        pack = _clone_pack_with_writable(d, "task_normal", "work", fields)
        rc, plan = run_admin(["pack", "install", "--tenant", "psm", str(pack), *who], env)
        assert "KHÔNG CÀI ĐƯỢC" in plan and "writable_fields" in plan, (fields, plan)
        assert rc == 1, (fields, rc)
    # Đối chứng: gói thật (writable_fields hợp lệ) cài được.
    rc, plan = run_admin(["pack", "install", "--tenant", "psm", str(TASK_PACK), *who], env)
    assert "KHÔNG CÀI ĐƯỢC" not in plan, plan


def test_r2_spec_rejects_state_field_in_writable(world: World) -> None:
    """Validator spec chặn writable_fields trùng khóa match / state_field ngay khi parse."""
    from xt.domain.process.spec import parse_spec

    base = {"id": "p", "name": "P", "version": 1, "trigger": {"kind": "manual"},
            "inputs": {"task_id": {"type": "str"}},
            "steps": [{"id": "s", "type": "task", "name": "S",
                       "assignee": "role(director)", "outcomes": ["done"],
                       "complete_via": {"model": "task", "match": {"id": "task_id"},
                                        "states": {"accepted": "done"},
                                        "autonomy": "L1"}}]}  # fmt: skip
    for bad in (["state"], ["id"], ["result", "result"]):
        spec = json.loads(json.dumps(base))
        spec["steps"][0]["complete_via"]["writable_fields"] = bad
        with pytest.raises(ValueError):
            parse_spec(spec)


def test_zz_dump_for_report(world: World) -> None:
    """Không khẳng định: in dữ liệu khai thác để dẫn chứng trong tài liệu audit."""
    for k in ("psa06", "psa03", "psa04_row", "rest_old_assignee", "psa01_task", "psa01_hr"):
        if k in world.__dict__:
            print(k, json.dumps(world.__dict__[k], ensure_ascii=False, default=str))
