# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E7: SLA leo thang. Tenant B (Hoa Mai) cài gói de-xuat-phe-duyet với SLA PT2M (chỉ đổi DỮ LIỆU gói,
demo-seed). Gửi đề nghị -> giữa chừng `docker compose restart xt-worker` -> trong <= 2 phút 15 s có sự
kiện `assignment.escalated` + bản ghi thông báo cho người nhận leo thang (`escalate_to:
requester.manager`), timer không mất; người nhận duyệt một lần là xong (không phải duyệt 2 lần)."""

from __future__ import annotations

import time

import pytest

from xte2e import compose
from xte2e.flows import B, B_EMPLOYEE, B_HEAD, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E7"
pytestmark = pytest.mark.e2e
DEADLINE = 135  # giây kể từ lúc gửi


def test_sla_escalation_survives_worker_restart(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    if world.tenants[B].get("sla") != "PT2M":
        ev.gap("demo-seed tenant B với SLA PT2M", "chạy lại make demo-seed trên volume mới")
    rows = dict(line.split("\t", 1) for line in compose.ps().splitlines() if "\t" in line)
    if not rows.get("xt-worker", "").startswith("running"):
        ev.gap("xt-worker chạy trên stack", rows.get("xt-worker", "không có"))
    u = world.by_key(B, B_EMPLOYEE)
    made = world.call(
        u,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(3_000_000),
            "idempotency_key": key("sla"),
        },
    )
    assert made.get("ok"), dumps(made)[:400]
    ref = made["data"]["record"]["ref"]
    t0 = time.time()
    sub = world.call(u, "submit_request", {"ref": ref, "idempotency_key": key("sla-s")})
    ev.step("submit_request", sub)
    assert sub.get("ok"), dumps(sub)[:400]
    iid = str(sub["data"]["process_instance"]).split(":", 1)[-1]
    due = compose.psql(
        f"SELECT due FROM xt_meta.workflow_timer_outbox WHERE instance_id::text = '{iid}' ORDER BY due LIMIT 1"
    )
    ev.step("timer outbox (due)", due)
    c.check(bool(due), "SLA ghi timer outbox cùng transaction với submit")
    time.sleep(20)
    r = compose.dc(["restart", "xt-worker"], check=False)
    ev.step(
        "docker compose restart xt-worker", {"rc": r.returncode, "t": round(time.time() - t0, 1)}
    )
    esc = ""
    while time.time() - t0 < DEADLINE + 90:  # chờ thêm để ghi được độ trễ thật nếu trễ hạn
        esc = compose.psql(
            f"SELECT created_at FROM xt_meta.process_event WHERE instance_id::text = '{iid}'"
            " AND type = 'assignment.escalated' LIMIT 1"
        )
        if esc:
            break
        time.sleep(5)
    took = round(time.time() - t0, 1)
    ev.step("leo thang", {"escalated_at": esc, "giây từ lúc gửi": took})
    events = compose.psql(
        f"SELECT type, created_at FROM xt_meta.process_event WHERE instance_id::text = '{iid}' ORDER BY created_at"
    )
    jobs = compose.psql(
        "SELECT j.status, count(*) FROM xt_queue.requests r JOIN procrastinate_jobs j ON j.id = r.job_id"
        f" WHERE r.idempotency_key LIKE 'workflow.timer:%' AND r.tenant_id = '{world.tenants[B]['id']}' GROUP BY 1"
    )
    ev.step(
        "process_event / job timer",
        {"events": events.splitlines(), "timer_jobs": jobs.splitlines()},
    )
    relay = compose.psql(
        "SELECT j.status, left(coalesce(r.last_error, ''), 200) FROM xt_queue.requests r"
        " JOIN procrastinate_jobs j ON j.id = r.job_id WHERE r.idempotency_key LIKE 'workflow.relay:%'"
        f" AND r.tenant_id = '{world.tenants[B]['id']}' ORDER BY j.id DESC LIMIT 1"
    )
    ev.step("chuỗi relay hẹn giờ (job mới nhất)", relay)
    c.check(
        not relay.startswith(("failed", "cancelled", "aborted")),
        f"chuỗi relay hẹn giờ còn sống ({relay!r})",
    )
    lat = compose.psql(
        f"SELECT round(extract(epoch FROM created_at) - {t0:.3f}) FROM xt_meta.process_event"
        f" WHERE instance_id::text = '{iid}' AND type = 'assignment.escalated' LIMIT 1"
    )
    ev.step("độ trễ leo thang (giây từ lúc gửi)", lat)
    c.check(
        bool(esc) and float(lat or 1e9) <= DEADLINE,
        f"assignment.escalated trong <= {DEADLINE} s dù worker restart (thực tế: "
        f"{lat + ' s' if lat else f'chưa có sau {took} s'}; SLA PT2M + chu kỳ relay XT_WORKFLOW_RELAY_SECONDS)",
    )
    notes = compose.psql(
        f"SELECT count(*) FROM xt_meta.notification WHERE dedupe_key LIKE 'esc:{iid}:%'"
    )
    c.check(int(notes or 0) >= 1, f"có bản ghi thông báo leo thang ({notes})")
    if esc:
        who = compose.psql(
            "SELECT p.key FROM xt_meta.notification n JOIN xt_meta.person p ON p.tenant_id = n.tenant_id"
            f" AND p.id = n.recipient WHERE n.dedupe_key LIKE 'esc:{iid}:%' LIMIT 1"
        )
        ev.step("người nhận leo thang (khóa nhân sự)", who)
        try:
            h = world.by_key(B, who or B_HEAD)
        except KeyError:
            ev.gap("đăng nhập cho người nhận leo thang", f"{who} không có trong tập user demo-seed")
        got = world.call(h, "get_record", {"ref": ref})
        ev.step("người nhận leo thang get_record", got)
        rev = ((got.get("data") or {}).get("record") or {}).get("revision") or sub["data"][
            "request"
        ]["revision"]
        args = {
            "ref": ref,
            "decision": "approve",
            "expected_revision": rev,
            "idempotency_key": key("sla-d"),
        }
        p = world.call(h, "decide_request", args)
        url = (err(p).get("details") or {}).get("confirm_url")
        if url:
            ev.step("người nhận leo thang xác nhận", world.confirm(url, h))
        fin = world.call(h, "decide_request", args)
        ev.step("quyết định", fin)
        state = ((fin.get("data") or {}).get("request") or {}).get("state")
        c.check(
            fin.get("ok") is True and state == "approved", f"duyệt một lần là xong (state={state})"
        )
    c.done()
