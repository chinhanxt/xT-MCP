# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Gói `giao-viec-sla` + `nhan-su-vong-doi` trên lớp ghép thật (Postgres 17 dùng chung của phiên):
`xt pack install` hai bước cài được (E0), op dịch vụ của deployment chạy happy path của mọi quy
trình (task_normal, weekly_digest, employee_onboarding, employee_offboarding) qua ProcessService
thật (RLS, vai trò xt_workflow, rule model, audit trigger).

W9 (E10): sổ `xt_meta.decision` + trigger `decision_sod_guard` chặn cả INSERT SQL trực tiếp bằng
xt_app: người duyệt = người đề nghị, hoặc quyết định không do chính người duyệt xác nhận.

Tenant riêng `opsx`, tổ chức sinh trong test với đúng vai trò/khóa phòng mà gói khai (hr, it_admin,
payment_accountant, chief_accountant; NS, IT) để kiểm việc gán người thật, không rơi về fallback.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import ROOT, StackBase, run_admin
from tests.integration.composition.shared_tenants import ALIAS, Ops, subject
from xt.adapters.db.connection import Database
from xt.adapters.db.integration import SodViolation
from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.application.process.service import _record, summary
from xt.domain.process.errors import EngineError
from xt.domain.process.schedule import Schedule, timer_inputs
from xt.domain.process.spec import TimerTrigger
from xt.entrypoints.composition.process import ProcessContext
from xt.entrypoints.composition.runtime import Caller

pytestmark = pytest.mark.integration


def test_packs_install_with_registered_ops(ops: Ops) -> None:
    assert "task_normal v4" in ops.plans["giao-viec-sla"]
    assert "weekly_digest v3" in ops.plans["giao-viec-sla"]
    assert "employee_onboarding v6" in ops.plans["nhan-su-vong-doi"]
    assert "employee_offboarding v4" in ops.plans["nhan-su-vong-doi"]


def test_task_normal_happy_path_closes_task(ops: Ops) -> None:
    kind = ops.create("NV-0001", "task_type", {"code": "bao_cao", "name": "Báo cáo"})
    task = ops.create(
        "NV-0009",
        "task",
        {
            "task_no": "CV-2026-0001",
            "title": "Báo cáo doanh số tuần",
            "kind": kind["id"],
            "assigner": ops.pid("NV-0009"),
            "assignee": ops.pid("NV-0010"),
            "deadline": "2099-12-31T17:00:00+07:00",
            "done_criteria": "Có số liệu theo khách hàng",
        },
    )
    data = {
        "task_id": f"task:{task['id']}",
        "assignee_user": ops.pid("NV-0010"),
        "assigner_user": ops.pid("NV-0009"),
    }
    iid, s = ops.start("task_normal", data, "NV-0009")
    assert ops.steps(s) == {"accept"}
    assert ops.steps(ops.act(iid, "accept", "NV-0010")) == {"work"}
    assert ops.steps(ops.act(iid, "work", "NV-0010")) == {"acceptance"}
    done = ops.act(iid, "acceptance", "NV-0009", "approve")
    assert done["status"] == "completed", done
    row = ops.get("task", task["id"])
    assert row["state"] == "done" and int(row["progress"]) == 100
    assert row["completed_at"] and row["result"] == "work xong"  # ghi chú khi nộp bước work
    assert row["process_instance_id"] == iid
    logs = ops.search("sla_log", {"field": "task", "op": "=", "value": task["id"]})
    assert [x["event"] for x in logs] == ["completed_on_time"]


def test_weekly_digest_rollup_and_send(ops: Ops) -> None:
    """Như poller: lần kích hoạt gần nhất của lịch (thứ Hai 8:00) -> bản tin tuần trước."""

    def fire(_: Caller, pc: ProcessContext) -> tuple[dict[str, Any], dict[str, Any]]:
        rt = pc.active("weekly_digest")
        assert rt is not None
        trig = rt.compiled.spec.trigger
        assert isinstance(trig, TimerTrigger)
        sch = Schedule(trig)
        occ = sch.latest_at_or_before(datetime.now(ZoneInfo(trig.tz)))
        assert occ is not None
        data = timer_inputs(trig, sch.fire_context(occ))
        inst = rt.start(data, fire={"fire_at": occ.isoformat(), "period_key": occ.isoformat()})
        PgWorkflowStore(ops.rt.uow).insert(inst.id, _record(inst))
        return data, summary(inst)

    data, s = ops.run("NV-0001", fire)
    assert s["status"] == "completed", s
    assert data["department"] == ""
    y, w, _ = date.fromisoformat(data["from_date"]).isocalendar()
    code = f"BT-{y:04d}-W{w:02d}"
    digests = ops.search("weekly_digest", {"field": "code", "op": "=", "value": code})
    assert len(digests) == 1
    dg = digests[0]
    assert dg["sent"] is True and dg["from_date"] == data["from_date"]
    assert dg["to_date"] == data["to_date"] and int(dg["total_completed"]) >= 0
    assert "hoàn thành" in dg["content"]
    # director + mọi dept_head nhận đúng một thông báo (idempotent theo mã bản tin)
    for key in ("NV-0001", "NV-0002", "NV-0004", "NV-0006", "NV-0009"):
        notes = ops.run(
            key,
            lambda c, _: ops.rt.store.notifications(c.actor, c.person.id, open_only=False),
        )
        assert sum(1 for n in notes if n["ref"] == f"weekly_digest:{dg['id']}") == 1, key


def _employee(ops: Ops, emp_no: str, key: str, dept: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return ops.create(
        "NV-0003",
        "employee",
        {"emp_no": emp_no, "user_id": ops.pid(key), "full_name": key, "job_title": "Nhân viên",
         "department": dept["id"], **kw},
    )  # fmt: skip


def _of_case(case: dict[str, Any]) -> dict[str, Any]:
    return {"field": "lifecycle_case", "op": "=", "value": case["id"]}


def test_onboarding_happy_path(ops: Ops) -> None:
    dept = ops.create("NV-0003", "department", {"code": "KD", "name": "Kinh doanh"})
    emp = _employee(ops, "NV-0011", "NV-0011", dept)
    assert emp["state"] == "waiting_to_start"
    case = ops.create(
        "NV-0003",
        "lifecycle_case",
        {"code": "ON-2026-0001", "employee": emp["id"], "kind": "onboarding",
         "effective_on": "2026-11-02", "initiated_by": ops.pid("NV-0003")},
    )  # fmt: skip
    data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": ops.pid("NV-0011")}
    iid, s = ops.start("employee_onboarding", data, "NV-0003")
    assert ops.steps(s) == {"hr"}
    tasks = ops.search(
        "lifecycle_task", {"field": "lifecycle_case", "op": "=", "value": case["id"]}
    )
    assert sorted(t["owner_unit"] for t in tasks) == ["accounting", "hr", "it", "manager"]
    assert all(t["state"] == "waiting" and t["due_on"] == "2026-11-02" for t in tasks)
    assert ops.steps(ops.act(iid, "hr", "NV-0003")) == {"it", "accounting", "manager"}
    ops.act(iid, "it", "NV-0005")
    ops.act(iid, "accounting", "NV-0007")
    done = ops.act(iid, "manager", "NV-0009")  # quản lý trực tiếp của người mới
    assert done["status"] == "completed", done
    e = ops.get("employee", emp["id"])
    assert e["state"] == "probation" and e["hired_on"] == "2026-11-02"
    c = ops.get("lifecycle_case", case["id"])
    assert c["state"] == "completed" and c["closed_at"] and c["process_instance_id"] == iid
    tasks = ops.search(
        "lifecycle_task", {"field": "lifecycle_case", "op": "=", "value": case["id"]}
    )
    assert {t["state"] for t in tasks} == {"done"}


def test_offboarding_happy_path_with_unreturned_asset(ops: Ops) -> None:
    dept = ops.create("NV-0003", "department", {"code": "KDOFF", "name": "Kinh doanh"})
    emp = _employee(ops, "NV-0012", "NV-0012", dept, state="permanent", hired_on="2020-01-01")
    asset = ops.create(
        "NV-0005",
        "asset_issue",
        {
            "employee": emp["id"],
            "kind": "laptop",
            "asset_code": "LT-0012",
            "issued_on": "2020-01-01",
        },
    )  # fmt: skip
    case = ops.create(
        "NV-0003",
        "lifecycle_case",
        {"code": "OF-2026-0001", "employee": emp["id"], "kind": "offboarding",
         "effective_on": "2026-11-30", "initiated_by": ops.pid("NV-0003")},
    )  # fmt: skip
    data = {"case_id": case["id"], "employee_id": emp["id"], "employee_user": ops.pid("NV-0012")}
    iid, s = ops.start("employee_offboarding", data, "NV-0003")
    assert ops.steps(s) == {"hr_confirm"}
    e = ops.get("employee", emp["id"])
    assert e["state"] == "offboarding_in_progress" and e["left_on"] == "2026-11-30"
    tasks = ops.search(
        "lifecycle_task", {"field": "lifecycle_case", "op": "=", "value": case["id"]}
    )
    assert sorted(t["owner_unit"] for t in tasks) == [
        "accounting", "chief_accountant", "hr", "hr", "it", "manager"
    ]  # fmt: skip
    assert ops.steps(ops.act(iid, "hr_confirm", "NV-0003")) == {"manager", "it", "accounting"}
    ops.act(iid, "manager", "NV-0009")
    ops.act(iid, "it", "NV-0005")
    s = ops.act(iid, "accounting", "NV-0007")
    # laptop chưa thu hồi -> chk_assets_result['unreturned'] > 0 -> kế toán trưởng xử lý
    assert ops.steps(s) == {"chief_acct_overdue"}
    assert ops.steps(ops.act(iid, "chief_acct_overdue", "NV-0008", "approve")) == {"hr_close"}
    # Chốt: còn laptop chưa thu hồi -> bước đóng hồ sơ lỗi, instance đứng ở hr_close
    with pytest.raises(EngineError, match="chưa thu hồi"):
        ops.act(iid, "hr_close", "NV-0003")
    assert ops.get("lifecycle_case", case["id"])["state"] == "processing"
    ops.run(
        "NV-0005",
        lambda c, _: ops.rt.records.update(
            c.actor, "asset_issue", uuid.UUID(asset["id"]), int(asset["revision"]),
            {"revoked_on": "2026-11-30"},
        ),
    )  # fmt: skip
    done = ops.act(iid, "hr_close", "NV-0003")
    assert done["status"] == "completed", done
    assert ops.get("employee", emp["id"])["state"] == "resigned"
    c = ops.get("lifecycle_case", case["id"])
    assert c["state"] == "completed" and c["closed_at"]
    tasks = ops.search("lifecycle_task", _of_case(case))
    assert {t["state"] for t in tasks} == {"done"}  # kế toán trưởng đã xử lý -> không "skipped"


# ------------------------------------------------------------------------------- W9 SoD ----
def _insert(cur: psycopg.Cursor[Any], approver: str, requester: str, confirmed: list[str]) -> None:
    cur.execute(
        "INSERT INTO xt_meta.decision (tenant_id, kind, ref, step, decision, approver, requester,"
        " actor_kind, actor_client, confirmed_by) VALUES (xt.tenant(), 'request', 'x:1', 'dh',"
        " 'approve', %s, %s, 'agent', 'evil-agent', %s)",
        (approver, requester, confirmed),
    )


def test_db_sod_trigger_rejects_direct_sql_as_xt_app(ops: Ops, app_db: Database) -> None:
    caller, _ = ops.caller("NV-0010")
    actor = caller.actor
    req, dh = ops.pid("NV-0010"), ops.pid("NV-0009")
    with app_db.tenant_tx(actor) as cur:
        assert cur.execute("SELECT current_user").fetchone()["current_user"] == "xt_app"
        trig = cur.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'xt_meta.decision'::regclass"
            " AND NOT tgisinternal AND tgname = 'decision_sod_guard'"
        ).fetchone()
        assert trig is not None
    cases = {
        "approver = requester": (req, req, [subject("NV-0010")]),
        "agent, người duyệt không tự xác nhận": (dh, req, ["evil-agent"]),
        "người đề nghị xác nhận hộ": (dh, req, [subject("NV-0009"), subject("NV-0010")]),
    }
    for name, (approver, requester, confirmed) in cases.items():
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="SoD"),
            app_db.tenant_tx(actor) as cur,
        ):
            _insert(cur, approver, requester, confirmed)
            pytest.fail(f"DB nhận quyết định vi phạm SoD: {name}")
    with app_db.tenant_tx(actor) as cur:  # hợp lệ: người khác, tự xác nhận
        _insert(cur, dh, req, [subject("NV-0009")])
        n = cur.execute("SELECT count(*) AS n FROM xt_meta.decision WHERE ref = 'x:1'").fetchone()
        assert n["n"] == 1
    for stmt in (
        "UPDATE xt_meta.decision SET approver = requester",
        "DELETE FROM xt_meta.decision",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege), app_db.tenant_tx(actor) as cur:
            cur.execute(stmt)
    # Đường ứng dụng: lỗi DB được dịch thành SodViolation (decide_request -> access_denied/sod)
    with pytest.raises(SodViolation):
        ops.run(
            "NV-0010",
            lambda c, _: ops.rt.store.record_decision(
                c.actor, kind="request", ref="x:2", step="dh", decision="approve",
                approver=uuid.UUID(req), requester=uuid.UUID(req), actor_kind="agent",
                actor_client="evil-agent", confirmed_by=[subject("NV-0010")],
            ),
        )  # fmt: skip


def test_decision_migration_is_idempotent(pg: pgfixture.Pg) -> None:
    body = files("xt.adapters.db").joinpath("sql/migrations/0010_decision_sod.sql").read_text()
    with pg.migrator() as m:
        assert m.execute(
            "SELECT 1 FROM xt_meta.schema_migration WHERE name = '0010_decision_sod.sql'"
        ).fetchone()
        with m.transaction():
            m.execute(body.encode())  # chạy lại trên CSDL đã có: không lỗi
        n = m.execute(
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'xt_meta.decision'::regclass"
            " AND tgname = 'decision_sod_guard'"
        ).fetchone()
        assert n == (1,)


# ------------------------------------------------- start_process + timer start (xt-worker) ----
def test_start_process_task_normal_via_tool(ops: Ops) -> None:
    """Trưởng phòng (qua agent L1) khởi động quy trình giao việc có nghiệm thu trên một task."""
    kind = ops.create("NV-0001", "task_type", {"code": "bao_cao_tool", "name": "Báo cáo"})
    task = ops.create(
        "NV-0009",
        "task",
        {"task_no": "CV-2026-0101", "title": "Chuẩn bị hội chợ", "kind": kind["id"],
         "assigner": ops.pid("NV-0009"), "assignee": ops.pid("NV-0010"),
         "deadline": "2099-12-31T17:00:00+07:00", "done_criteria": "Có kế hoạch"},
    )  # fmt: skip
    ref = f"task:{task['id']}"
    args = {
        "process_id": "task_normal",
        "subject_ref": ref,
        "parameters": {"assignee_user": f"person:{ops.pid('NV-0010')}", "assigner_user": "me"},
        "idempotency_key": "it-start-task-0101",
    }
    out = ops.tool("NV-0009", "start_process", args)
    assert out["ok"], out
    assert out["meta"]["effective_level"] == "L1"
    data = out["data"]
    assert data["state"] == "running" and data["subject_ref"] == ref
    (step,) = data["first_steps"]
    assert step["assignee"]["ref"] == f"person:{ops.pid('NV-0010')}"
    iid = data["process_instance"].split(":", 1)[1]
    again = ops.tool("NV-0009", "start_process", args)
    assert again["ok"] and again["meta"]["receipt"]["replayed"] is True
    assert again["data"]["process_instance"] == data["process_instance"]

    def linked(_: Caller, pc: ProcessContext) -> tuple[str | None, dict[str, Any]]:
        return PgWorkflowStore(ops.rt.uow).ref_for(iid), pc.service().get(iid)

    got_ref, info = ops.run("NV-0009", linked)
    assert got_ref == ref  # task_id = subject_ref (quy ước `<model>_id`), inbox/timeline thấy task
    assert info["process_id"] == "task_normal" and {t["step"] for t in info["tasks"]} == {"accept"}
    # nhân viên không có `process.start`; quy trình định kỳ / gắn đề nghị không khởi động tay
    denied = ops.tool("NV-0010", "start_process", {**args, "idempotency_key": "it-start-emp-01"})
    assert denied["error"]["code"] == "access_denied", denied
    timer = ops.tool(
        "NV-0001",
        "start_process",
        {"process_id": "weekly_digest", "idempotency_key": "it-start-digest-01"},
    )
    assert timer["error"]["code"] in ("invalid_request", "access_denied"), timer
    ghost = ops.tool(
        "NV-0009",
        "start_process",
        {**args, "subject_ref": f"task:{uuid.uuid4()}", "idempotency_key": "it-start-ghost-1"},
    )
    assert ghost["error"]["code"] == "not_found", ghost


def test_start_process_onboarding_via_tool(ops: Ops) -> None:
    dept = ops.create("NV-0003", "department", {"code": "KDTOOL", "name": "Kinh doanh"})
    emp = _employee(ops, "NV-0013", "NV-0013", dept)
    case = ops.create(
        "NV-0003",
        "lifecycle_case",
        {"code": "ON-2026-0002", "employee": emp["id"], "kind": "onboarding",
         "effective_on": "2026-12-01", "initiated_by": ops.pid("NV-0003")},
    )  # fmt: skip
    out = ops.tool(
        "NV-0003",
        "start_process",
        {
            "process_id": "employee_onboarding",
            "subject_ref": f"lifecycle_case:{case['id']}",
            "parameters": {
                "case_id": case["id"],
                "employee_id": emp["id"],
                "employee_user": f"person:{ops.pid('NV-0013')}",
            },
            "idempotency_key": "it-start-onb-0002",
        },
    )
    assert out["ok"], out
    assert [s["assignee"]["ref"] for s in out["data"]["first_steps"]] == [
        f"person:{ops.pid('NV-0003')}"
    ]
    tasks = ops.search("lifecycle_task", _of_case(case))
    assert sorted(t["owner_unit"] for t in tasks) == ["accounting", "hr", "it", "manager"]


def _period_after(ops: Ops, pg: pgfixture.Pg) -> tuple[datetime, str, str]:
    """Mốc 'now' giả lập: lần kích hoạt đầu tiên của weekly_digest sau lúc định nghĩa được tạo
    (poller bỏ kỳ trước lúc kích hoạt); trả (now, period_key, from_date của kỳ)."""
    with pg.su() as c:
        created = c.execute(
            "SELECT created_at FROM xt_meta.process_definition WHERE tenant_id=%s"
            " AND process_id='weekly_digest' AND active",
            (ops.tenant.id,),
        ).fetchone()[0]

    def trig(_: Caller, pc: ProcessContext) -> TimerTrigger:
        rt = pc.active("weekly_digest")
        assert rt is not None and isinstance(rt.compiled.spec.trigger, TimerTrigger)
        return rt.compiled.spec.trigger

    t = ops.run("NV-0001", trig)
    sch = Schedule(t)
    occ = sch.next_after(created)
    assert occ is not None
    from_date = timer_inputs(t, sch.fire_context(occ))["from_date"]
    return occ + timedelta(minutes=3), occ.isoformat(), str(from_date)


def test_worker_timer_start_once_per_period_with_two_workers(
    ops: Ops, worker: Any, pg: pgfixture.Pg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`workflow.timer_starts`: hai worker quét cùng kỳ -> đúng MỘT instance weekly_digest; quét
    lại -> `already`. Instance chạy hết (rollup + gửi), bản tin của kỳ được tạo đúng một lần."""
    from tests.unit.process import support

    now, period, from_date = _period_after(ops, pg)
    # Kỳ nằm sau lúc tạo định nghĩa (tương lai so với đồng hồ thật): đồng hồ timer của
    # SpiffWorkflow đặt tới `now` để timer start event của kỳ chín ngay (như lúc worker chạy thật).
    monkeypatch.setattr(support.spiff_timer, "datetime", support.FakeDT)
    support.Clock().set(now)
    try:
        _two_workers(ops, worker, pg, monkeypatch, now, period, from_date)
    finally:
        support.FakeDT.offset = timedelta(0)


def _two_workers(
    ops: Ops,
    worker: Any,
    pg: pgfixture.Pg,
    monkeypatch: pytest.MonkeyPatch,
    now: datetime,
    period: str,
    from_date: str,
) -> None:
    import threading

    from xt.entrypoints.composition import worker as wk

    def count() -> int:
        with pg.su() as su:
            row = su.execute(
                "SELECT count(*) FROM xt_meta.workflow_instance WHERE tenant_id=%s"
                " AND process_id='weekly_digest'",
                (ops.tenant.id,),
            ).fetchone()
        return int(row[0]) if row else 0

    before = count()
    jobs = [wk.build_jobs(worker.env), wk.build_jobs(worker.env)]
    try:
        barrier = threading.Barrier(2)
        results: list[dict[str, Any]] = []
        errors: list[BaseException] = []

        def run(j: Any) -> None:
            try:
                barrier.wait()
                results.extend(j.timer_starts_sync(ops.tenant.id, now))
            except BaseException as e:  # pragma: no cover - báo ở assert
                errors.append(e)

        threads = [threading.Thread(target=run, args=(j,)) for j in jobs]
        for t in threads:
            t.start()
        for t in threads:
            t.join(120)
        assert not errors, errors
        mine = [r for r in results if r["process_key"] == "weekly_digest"]
        assert sorted(r["result"] for r in mine) == ["already", "fired"], results
        fired = next(r for r in mine if r["result"] == "fired")
        assert fired["period_key"] == period
        again = jobs[0].timer_starts_sync(ops.tenant.id, now + timedelta(minutes=5))
        assert [r["result"] for r in again if r["process_key"] == "weekly_digest"] == ["already"]
        with pg.su() as su:
            rows = su.execute(
                "SELECT f.status, f.instance_id, i.status FROM xt_meta.workflow_timer_fire f"
                " JOIN xt_meta.workflow_instance i"
                " ON i.tenant_id=f.tenant_id AND i.id=f.instance_id"
                " WHERE f.tenant_id=%s AND f.process_id='weekly_digest'",
                (ops.tenant.id,),
            ).fetchall()
        assert len(rows) == 1 and rows[0][0] == "fired" and rows[0][1] == fired["instance_id"]
        assert rows[0][2] == "completed" and count() == before + 1
        y, w, _ = date.fromisoformat(from_date).isocalendar()
        code = f"BT-{y:04d}-W{w:02d}"
        digests = ops.search("weekly_digest", {"field": "code", "op": "=", "value": code})
        assert len(digests) == 1 and digests[0]["sent"] is True
        assert digests[0]["from_date"] == from_date
        # Job được đăng ký trong `xt worker` và relay hẹn nó mỗi chu kỳ
        for k, v in worker.env.items():
            monkeypatch.setenv(k, v)
        assert wk.TIMER_STARTS in wk.handlers()
        rc, out = run_admin(["tenant", "bind-queue", "--tenant", ALIAS], worker.env)
        assert rc == 0, out

        async def relay() -> None:
            from xt.adapters.db.workflow_bridge import tenant_async_tx
            from xt.application.jobs import JobRequest

            async with tenant_async_tx(worker.env["XT_WORKER_DATABASE_URL"], ops.tenant.id) as conn:
                await jobs[0].relay(conn, JobRequest(ops.tenant.id, wk.RELAY, "it.relay.ops", {}))

        import anyio

        anyio.run(relay)
        with pg.su() as su:
            queued = su.execute(
                "SELECT count(*) FROM xt_queue.requests WHERE tenant_id=%s"
                " AND idempotency_key LIKE 'workflow.timer_starts:%%'",
                (ops.tenant.id,),
            ).fetchone()[0]
        assert queued >= 1

    finally:
        for j in jobs:
            j.rt.close()
            j.rt.app_db.close()


def test_timer_fire_migration_is_idempotent(pg: pgfixture.Pg) -> None:
    body = (
        files("xt.adapters.db").joinpath("sql/migrations/0012_workflow_timer_fire.sql").read_text()
    )
    with pg.migrator() as m:
        assert m.execute(
            "SELECT 1 FROM xt_meta.schema_migration WHERE name = '0012_workflow_timer_fire.sql'"
        ).fetchone()
        with m.transaction():
            m.execute(body.encode())


# ------------------------------- bước người qua transition_record (complete_via, D-STEP) ----
def _task(ops: Ops, no: str, title: str) -> dict[str, Any]:
    kind = ops.create(
        "NV-0001", "task_type", {"code": no.lower().replace("-", "_"), "name": "Báo cáo"}
    )
    return ops.create(
        "NV-0009",
        "task",
        {"task_no": no, "title": title, "kind": kind["id"], "assigner": ops.pid("NV-0009"),
         "assignee": ops.pid("NV-0010"), "deadline": "2099-12-31T17:00:00+07:00",
         "done_criteria": "Có số liệu đầy đủ"},
    )  # fmt: skip


def _start_task(ops: Ops, task: dict[str, Any], key: str, **extra: Any) -> str:
    args: dict[str, Any] = {
        "process_id": "task_normal",
        "subject_ref": f"task:{task['id']}",
        "parameters": {"assignee_user": f"person:{ops.pid('NV-0010')}", "assigner_user": "me"},
        "idempotency_key": key,
        **extra,
    }
    out = ops.tool("NV-0009", "start_process", args)
    assert out["ok"], out
    return str(out["data"]["process_instance"]).split(":", 1)[1]


def _instance(ops: Ops, iid: str) -> tuple[Any, dict[str, Any]]:
    def body(_: Caller, pc: ProcessContext) -> tuple[Any, dict[str, Any]]:
        return PgWorkflowStore(ops.rt.uow).get_for_update(iid), pc.service().get(iid)

    out: tuple[Any, dict[str, Any]] = ops.run("NV-0001", body)
    return out


def _sla_left(ops: Ops, iid: str) -> timedelta:
    rec, _ = _instance(ops, iid)
    assert rec.next_due is not None
    return rec.next_due - datetime.now(UTC)


def test_task_normal_steps_complete_via_transition_record(ops: Ops) -> None:
    """assign -> accept -> work -> nghiệm thu -> done bằng transition_record trên chính task:
    người được giao + IAM + SoD + mức tự chủ của bước (accept/work L1, nghiệm thu human_only),
    SLA từng bước vẫn hẹn đúng."""
    from xt.application.tools.ports import PendingStatus

    task = _task(ops, "CV-2026-0301", "Báo cáo công nợ quý")
    ref = f"task:{task['id']}"
    iid = _start_task(ops, task, "it-step-start-0301")
    assert timedelta(hours=3, minutes=50) < _sla_left(ops, iid) <= timedelta(hours=4)

    item = ops.inbox_item("NV-0010", ref)  # inbox người làm: ref = task, tool = transition_record
    assert (item["kind"], item["step"], item["state"]) == ("task", "accept", "new"), item
    assert item["suggested_tool"] == "transition_record" and item["to_states"] == ["accepted"]
    assert item["sla_status"] == "on_track" and item["due_at"]
    # Người làm không đọc được task (own theo người tạo): inbox không lộ tiêu đề (PSA-03).
    assert item["title"] == f"Xác nhận nhận việc: task:{task['id'][:8]}"

    # Người giao không giữ bước accept: không hoàn thành thay được.
    other = ops.tool(
        "NV-0009",
        "transition_record",
        {"ref": ref, "to_state": "accepted", "expected_revision": item["revision"],
         "idempotency_key": "it-step-0301-x"},
    )  # fmt: skip
    assert other["error"]["code"] == "access_denied", other
    assert other["error"]["details"]["reason"] == "not_assigned"

    acc = ops.complete("NV-0010", ref, "accepted", "it-step-0301-acc")
    assert acc["ok"], acc
    assert acc["meta"]["effective_level"] == "L1"  # autonomy L1 của bước: agent làm ngay
    rec = acc["data"]["record"]
    assert (acc["data"]["from_state"], rec["state"]) == ("new", "accepted")
    assert rec["available_transitions"] == [
        {"to_state": "awaiting_acceptance", "label": "Hoàn thành và nộp kết quả (done)",
         "level": "L1"}
    ]  # fmt: skip
    assert timedelta(hours=71, minutes=50) < _sla_left(ops, iid) <= timedelta(hours=72)

    wrong = ops.complete("NV-0010", ref, "done", "it-step-0301-bad")
    assert wrong["error"]["code"] == "precondition_failed", wrong
    assert wrong["error"]["details"]["available_transitions"] == ["awaiting_acceptance"]
    no_result = ops.complete("NV-0010", ref, "awaiting_acceptance", "it-step-0301-w0")
    assert no_result["error"]["code"] == "rule_violation", no_result  # rule r1 của model task
    work = ops.complete(
        "NV-0010", ref, "awaiting_acceptance", "it-step-0301-w1",
        values={"result": "Đã gửi báo cáo công nợ"},
    )  # fmt: skip
    assert work["ok"], work
    assert work["data"]["record"]["state"] == "awaiting_acceptance"
    assert timedelta(hours=47, minutes=50) < _sla_left(ops, iid) <= timedelta(hours=48)

    # Người làm không tự nghiệm thu (không phải người được giao bước + SoD distinct_from work).
    item = ops.inbox_item("NV-0009", ref)
    assert item["kind"] == "approval" and item["suggested_tool"] == "transition_record"
    assert item["to_states"] == ["done", "in_progress", "not_accepted"]
    self_ok = ops.tool(
        "NV-0010",
        "transition_record",
        {"ref": ref, "to_state": "done", "expected_revision": item["revision"],
         "idempotency_key": "it-step-0301-self"},
    )  # fmt: skip
    assert self_ok["error"]["code"] in ("access_denied", "not_found"), self_ok
    got = ops.tool("NV-0009", "get_record", {"ref": ref})
    assert got["ok"], got
    assert {t["to_state"]: t["level"] for t in got["data"]["record"]["available_transitions"]} == {
        "done": "L3", "in_progress": "L3", "not_accepted": "L3"
    }  # fmt: skip

    # Nghiệm thu là human_only: agent của người giao chỉ tạo yêu cầu xác nhận.
    args = {"ref": ref, "to_state": "done", "expected_revision": item["revision"],
            "reason": "Đạt", "idempotency_key": "it-step-0301-ok"}  # fmt: skip
    pending = ops.tool("NV-0009", "transition_record", args)
    assert pending["error"]["code"] == "confirmation_required", pending
    assert ops.get("task", task["id"])["state"] == "awaiting_acceptance"
    by_worker = ops.confirm(pending, "NV-0010", "NV-0009")
    assert by_worker.status is not PendingStatus.CONFIRMED and by_worker.error, by_worker
    confirmed = ops.confirm(pending, "NV-0009", "NV-0009")
    assert confirmed.status is PendingStatus.CONFIRMED, confirmed
    done = ops.tool("NV-0009", "transition_record", args)
    assert done["ok"] and done["meta"]["receipt"]["replayed"] is True, done
    assert done["data"]["record"]["state"] == "done"

    row = ops.get("task", task["id"])
    assert row["state"] == "done" and int(row["progress"]) == 100 and row["completed_at"]
    assert row["result"] == "Đã gửi báo cáo công nợ" and row["process_instance_id"] == iid
    logs = ops.search("sla_log", {"field": "task", "op": "=", "value": task["id"]})
    assert [x["event"] for x in logs] == ["completed_on_time"]
    _, info = _instance(ops, iid)
    assert info["status"] == "completed" and not info["tasks"]
    assert [(h["step"], h["outcome"]) for h in info["history"]] == [
        ("accept", "done"), ("work", "done"), ("acceptance", "approve")
    ]  # fmt: skip
    late = ops.tool(
        "NV-0010",
        "transition_record",
        {"ref": ref, "to_state": "accepted", "expected_revision": int(row["revision"]),
         "idempotency_key": "it-step-0301-late"},
    )  # fmt: skip
    assert late["error"]["code"] in ("precondition_failed", "not_found"), late


def test_task_normal_return_and_sla_escalation(ops: Ops, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nghiệm thu không đạt -> task về in_progress, bước work mở lại cho người làm; quá SLA PT4H
    của accept -> leo thang người giao (đúng spec), người nhận leo thang hoàn thành được."""
    import SpiffWorkflow.bpmn.specs.event_definitions.timer as spiff_timer

    from tests.unit.process.support import FakeDT

    # Leo thang: instance mới, đồng hồ của timer SpiffWorkflow tiến 5 giờ.
    task = _task(ops, "CV-2026-0302", "Kiểm kê kho")
    ref = f"task:{task['id']}"
    FakeDT.offset = timedelta(0)
    monkeypatch.setattr(spiff_timer, "datetime", FakeDT)
    try:
        iid = _start_task(ops, task, "it-step-start-0302")
        FakeDT.offset = timedelta(hours=5)
        ops.run("NV-0001", lambda _, pc: pc.service().tick(iid))
    finally:
        FakeDT.offset = timedelta(0)
    _, info = _instance(ops, iid)
    (t,) = info["tasks"]
    assert (t["step"], t["candidates"]) == ("accept", [ops.pid("NV-0009")]), t
    item = ops.inbox_item("NV-0009", ref)
    assert item["suggested_tool"] == "transition_record" and item["to_states"] == ["accepted"]
    acc = ops.complete("NV-0009", ref, "accepted", "it-step-0302-acc")
    assert acc["ok"], acc
    work = ops.complete("NV-0010", ref, "awaiting_acceptance", "it-step-0302-w",
                        values={"result": "Bảng kiểm kê"})  # fmt: skip
    assert work["ok"], work
    item = ops.inbox_item("NV-0009", ref)
    noreason = ops.tool(
        "NV-0009", "transition_record",
        {"ref": ref, "to_state": "in_progress", "expected_revision": item["revision"],
         "idempotency_key": "it-step-0302-r0"},
    )  # fmt: skip
    assert noreason["error"]["code"] == "invalid_request", noreason
    args = {"ref": ref, "to_state": "in_progress", "expected_revision": item["revision"],
            "reason": "Thiếu kho Bình Dương", "idempotency_key": "it-step-0302-ret"}  # fmt: skip
    pending = ops.tool("NV-0009", "transition_record", args)
    ops.confirm(pending, "NV-0009", "NV-0009")
    back = ops.tool("NV-0009", "transition_record", args)
    assert back["ok"], back
    assert ops.get("task", task["id"])["state"] == "in_progress"
    item = ops.inbox_item("NV-0010", ref)
    assert item["step"] == "work" and item["to_states"] == ["awaiting_acceptance"]


def test_start_process_scans_refs_nested_in_object_inputs(
    ops: Ops, stack_base: StackBase, tmp_path: Any
) -> None:
    """Input `object` của quy trình: ref bản ghi trong mảng/object cũng phải đọc được bởi
    người khởi tạo (như ref cấp cao nhất); không thì not_found như id ma (PSA-03 vòng 2)."""
    import shutil

    dst = tmp_path / "giao-viec-sla"
    shutil.copytree(ROOT / "packages" / "giao-viec-sla", dst)
    p = dst / "processes" / "task_normal.json"
    spec = json.loads(p.read_text(encoding="utf-8"))
    spec["version"] = int(spec["version"]) + 1
    spec["inputs"]["context"] = {"type": "object", "required": False}
    p.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    common = ["pack", "install", "--tenant", ALIAS, str(dst)]
    rc, plan = run_admin([*common, *who], stack_base.env)
    assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
    digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
    assert digest, plan
    rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], stack_base.env)
    assert rc == 0, out

    own = _task(ops, "CV-2026-0401", "Việc có ngữ cảnh")
    foreign = ops.create(
        "NV-0002",
        "task",
        {"task_no": "CV-2026-0402", "title": "Việc phòng NS", "kind": own["kind"],
         "assigner": ops.pid("NV-0002"), "assignee": ops.pid("NV-0003"),
         "deadline": "2099-12-31T17:00:00+07:00", "done_criteria": "Có số liệu đầy đủ"},
    )  # fmt: skip
    # Bề mặt tool chỉ nhận mảng phẳng (object lồng: unit test record_refs).
    hidden = ["ghi chú", f"task:{foreign['id']}"]
    denied = ops.tool(
        "NV-0009",
        "start_process",
        {"process_id": "task_normal", "subject_ref": f"task:{own['id']}",
         "parameters": {"assignee_user": f"person:{ops.pid('NV-0010')}", "assigner_user": "me",
                        "context": hidden},
         "idempotency_key": "it-start-nested-1"},
    )  # fmt: skip
    ghost = ops.tool(
        "NV-0009",
        "start_process",
        {"process_id": "task_normal", "subject_ref": f"task:{own['id']}",
         "parameters": {"assignee_user": f"person:{ops.pid('NV-0010')}", "assigner_user": "me",
                        "context": [f"task:{uuid.uuid4()}"]},
         "idempotency_key": "it-start-nested-2"},
    )  # fmt: skip
    assert denied["error"]["code"] == ghost["error"]["code"] == "not_found", (denied, ghost)
    ok = ops.tool(
        "NV-0009",
        "start_process",
        {"process_id": "task_normal", "subject_ref": f"task:{own['id']}",
         "parameters": {"assignee_user": f"person:{ops.pid('NV-0010')}", "assigner_user": "me",
                        "context": [f"task:{own['id']}"]},
         "idempotency_key": "it-start-nested-3"},
    )  # fmt: skip
    assert ok["ok"], ok
