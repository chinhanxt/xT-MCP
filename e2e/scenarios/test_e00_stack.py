# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E0: stack minimal thật + `make demo-seed` (tenant, Organization, user TOTP, gói, bind-queue, doctor)."""

from __future__ import annotations

import json
import subprocess

import pytest
from xte2e import compose
from xte2e.kcadmin import Admin
from xte2e.report import Checks, Evidence

SCENARIO = "E0"
pytestmark = pytest.mark.e2e

REQUIRED = ["postgres", "keycloak", "apisix", "postgrest", "xt-api", "xt-worker", "xt-desk"]
PACKS_A = {"de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"}


def _health(project_service: str) -> str:
    r = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
            project_service,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.stdout.strip() or "missing"


def test_services_running(ev: Evidence) -> None:
    c = Checks(ev)
    issues_f = compose.settings().state_dir / "stack-issues.json"
    issues = json.loads(issues_f.read_text(encoding="utf-8")) if issues_f.exists() else []
    ev.step("lỗi khi dựng stack bằng compose.yaml gốc", issues)
    for msg in issues:
        c.check(False, msg)
    rows = dict(line.split("\t", 1) for line in compose.ps().splitlines() if "\t" in line)
    ev.step("docker compose ps", rows)
    for svc in REQUIRED:
        st = rows.get(svc, "")
        ok = st.startswith("running")
        detail = "" if ok else (compose.logs(svc, 3).strip().splitlines() or [""])[-1][-300:]
        if not ok and svc == "xt-worker":
            envs = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{range .Config.Env}}{{println .}}{{end}}",
                    f"{compose.settings().project}-xt-worker-1",
                ],
                capture_output=True,
                text=True,
            ).stdout
            if "XT_WORKFLOW_RELAY_SECONDS=" not in envs:
                detail += " | compose.yaml: xt-worker thiếu XT_WORKFLOW_RELAY_SECONDS (bắt buộc, composition/worker.py)"
        c.check(
            ok, f"{svc} đang chạy" + ("" if ok else f" (trạng thái {st or 'không có'}: {detail})")
        )
    init = rows.get("xt-init", "")
    c.check(init.startswith("exited") and "(0)" in init, f"xt-init hoàn tất mã 0 ({init})")
    proj = compose.settings().project
    api_health = _health(f"{proj}-xt-api-1")
    c.check(api_health == "healthy", f"xt-api có healthcheck và healthy (thực tế: {api_health})")
    c.done()


def test_doctor(ev: Evidence) -> None:
    rc, out = compose.xt(["doctor"])
    lines = out.strip().splitlines()
    ev.step("xt doctor", {"rc": rc, "out": lines[-20:]})
    bad = [
        ln
        for ln in lines
        if ": ok" not in ln and ln.strip() and not ln.startswith(("INFO", "DEBUG"))
    ]
    c = Checks(ev)
    c.check(rc == 0, f"xt doctor rc=0 (rc={rc})")
    c.check(not bad, "xt doctor không có mục unknown/fail" + (f": {bad}" if bad else ""))
    c.done()


def test_seed(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    st = world.state
    tenants = st["tenants"]
    c.check(
        set(tenants) >= {"binhminh", "hoamai"}, f"2 tenant (binhminh, hoamai): {sorted(tenants)}"
    )
    rows = compose.psql(
        "SELECT p.alias, t.seq, coalesce(string_agg(i.pack || '@' || i.version, ',' ORDER BY i.pack), '')"
        " FROM xt_meta.tenant_profile p JOIN xt_meta.tenant t ON t.id = p.tenant_id"
        " LEFT JOIN xt_meta.pack_install i ON i.tenant_id = p.tenant_id GROUP BY 1, 2 ORDER BY 2"
    )
    packs = {
        ln.split("\t")[0]: set(x.split("@")[0] for x in ln.split("\t")[2].split(",") if x)
        for ln in rows.splitlines()
    }
    ev.step("pack_install", {k: sorted(v) for k, v in packs.items()})
    missing_a = PACKS_A - packs.get("binhminh", set())
    fails = st.get("pack_failures", {})
    c.check(
        not missing_a,
        "tenant A cài đủ 3 gói qua `xt pack install`"
        + (
            f"; thiếu {sorted(missing_a)}: "
            + json.dumps(
                {k: v[:3] for k, v in fails.items() if k.startswith("binhminh/")},
                ensure_ascii=False,
            )
            if missing_a
            else ""
        ),
    )
    c.check(
        packs.get("binhminh") != packs.get("hoamai") and bool(packs.get("hoamai")),
        f"tenant B có bộ model khác A: A={sorted(packs.get('binhminh', []))} B={sorted(packs.get('hoamai', []))}",
    )
    people = compose.psql(
        "SELECT p.alias, count(*), count(subject) FROM xt_meta.person x JOIN xt_meta.tenant_profile p"
        " ON p.tenant_id = x.tenant_id GROUP BY 1 ORDER BY 1"
    )
    ev.step("xt_meta.person (alias, người, có đăng nhập)", people.splitlines())
    c.check(
        "binhminh\t60\t7" in people,
        "Bình Minh: 60 nhân sự từ seed/binh-minh/org.json, 7 có đăng nhập Keycloak",
    )
    binds = compose.psql(
        "SELECT p.alias, b.job_role FROM xt_queue.tenant_bindings b JOIN xt_meta.tenant_profile p"
        " ON p.tenant_id = b.tenant_id ORDER BY 1"
    )
    ev.step("xt_queue.tenant_bindings", binds.splitlines())
    c.check(binds.count("\n") + 1 >= 2 if binds else False, "bind-queue xong cho cả 2 tenant")
    relay = compose.psql(
        "SELECT count(*) FROM xt_queue.requests WHERE idempotency_key LIKE 'workflow.relay:%'"
    )
    c.check(int(relay or 0) >= 2, f"chuỗi relay hẹn giờ đã đăng ký ({relay})")

    kc = Admin()
    kc.login()
    for alias, t in tenants.items():
        members = {
            m["username"] for m in kc.get(f"/organizations/{t['org_id']}/members?max=200") or []
        }
        want = {u["username"] for u in world.users.values() if u.get("tenant") == alias}
        c.check(want <= members, f"Organization {alias}: {len(want)} user là thành viên")
    approvers = [u for u in world.users.values() if u.get("approver")]
    no_otp = []
    for u in approvers:
        creds = kc.get(f"/users/{u['sub']}/credentials") or []
        if not any(x["type"] == "otp" for x in creds):
            no_otp.append(u["username"])
    c.check(
        not no_otp and len(approvers) >= 6,
        f"{len(approvers)} người duyệt có TOTP" + (f"; thiếu: {no_otp}" if no_otp else ""),
    )
    multi = next((u for u in world.users.values() if u.get("orgs")), None)
    c.check(multi is not None and len(multi["orgs"]) == 2, "có user thuộc 2 Organization (cho E4)")
    c.done()
