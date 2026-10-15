# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E7: SLA leo thang. Tenant B cài gói de-xuat-phe-duyet với SLA PT2M (chỉ đổi dữ liệu gói, demo-seed).
Gửi đề nghị -> giữa chừng `docker compose restart xt-worker` -> trong <= 2 phút 15 s bước trưởng phòng
leo thang cho giám đốc (requester.manager), timer không mất; giám đốc duyệt một lần là đủ."""

from __future__ import annotations

import time

import pytest

from xte2e import compose
from xte2e.flows import B, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E7"
pytestmark = pytest.mark.e2e
REQ, HEAD, DIR = "AP-003", "AP-002", "AP-001"
DEADLINE = 135  # giây


def _inbox_has(world, who: str, ref: str) -> bool:  # type: ignore[no-untyped-def]
    inbox = world.call(world.by_key(B, who), "get_inbox", {"kinds": ["approval"]})
    return any(i.get("ref") == ref for i in (inbox.get("data") or {}).get("items", []))


def test_sla_escalation_survives_worker_restart(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    if world.tenants[B].get("sla") != "PT2M":
        ev.gap("demo-seed tenant B với SLA PT2M", "chạy lại make demo-seed trên volume mới")
    rows = dict(line.split("\t", 1) for line in compose.ps().splitlines() if "\t" in line)
    if not rows.get("xt-worker", "").startswith("running"):
        ev.gap("xt-worker chạy trên stack", rows.get("xt-worker", "không có"))
    u = world.by_key(B, REQ)
    made = world.call(u, "create_record", {"model_id": "advance_request", "values": advance(3_000_000),
                                           "idempotency_key": key("sla")})
    assert made.get("ok"), dumps(made)[:400]
    ref = made["data"]["record"]["ref"]
    t0 = time.time()
    sub = world.call(u, "submit_request", {"ref": ref, "idempotency_key": key("sla-s")})
    ev.step("submit_request", sub)
    assert sub.get("ok"), dumps(sub)[:400]
    iid = str(sub["data"]["process_instance"]).split(":", 1)[-1]
    due = compose.psql("SELECT due FROM xt_meta.workflow_timer_outbox WHERE instance_id::text = "
                       f"'{iid}' ORDER BY due LIMIT 1")
    ev.step("timer outbox", due)
    c.check(bool(due), "SLA ghi timer outbox cùng transaction")
    c.check(_inbox_has(world, HEAD, ref), "ban đầu: việc ở inbox trưởng phòng")
    time.sleep(20)
    r = compose.dc(["restart", "xt-worker"], check=False)
    ev.step("docker compose restart xt-worker", {"rc": r.returncode, "t": round(time.time() - t0, 1)})
    escalated = False
    while time.time() - t0 < DEADLINE:
        if _inbox_has(world, DIR, ref):
            escalated = True
            break
        time.sleep(5)
    took = round(time.time() - t0, 1)
    ev.step("leo thang", {"escalated": escalated, "giây": took})
    jobs = compose.psql("SELECT j.status, count(*) FROM xt_queue.requests r JOIN procrastinate_jobs j ON j.id = r.job_id"
                        " WHERE r.idempotency_key LIKE 'workflow.timer:%' GROUP BY 1")
    ev.step("job timer (status, số)", jobs.splitlines())
    c.check(escalated, f"trong <= {DEADLINE} s việc leo thang tới inbox giám đốc (đã chờ {took} s)")
    if escalated:
        d = world.by_key(B, DIR)
        got = world.call(d, "get_record", {"ref": ref})
        args = {"ref": ref, "decision": "approve", "expected_revision": got["data"]["record"]["revision"],
                "idempotency_key": key("sla-d")}
        p = world.call(d, "decide_request", args)
        url = (err(p).get("details") or {}).get("confirm_url")
        if url:
            ev.step("giám đốc xác nhận", world.confirm(url, d))
        fin = world.call(d, "decide_request", args)
        ev.step("giám đốc quyết định", fin)
        c.check(fin.get("ok") is True, "giám đốc duyệt được")
        c.check(not _inbox_has(world, DIR, ref), "giám đốc không phải duyệt lần 2")
    c.done()
