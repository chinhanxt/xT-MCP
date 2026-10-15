# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit vòng 2 REST /workflow/v1 (docs/audit-workflow-api.md, "Vòng 2"): ủy quyền đổi trạng thái
giữa plan và xác nhận, rút đề nghị (withdraw), ETag. `xfail(strict=True)` = lỗi WA-11/WA-12.
"""

from __future__ import annotations

import concurrent.futures as cf
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.test_e2e import DEPT_HEAD, DIRECTOR, REQUESTER, Stack, _Form, sub
from tests.integration.workflow_api_audit.conftest import OTHER, A, B
from tests.integration.workflow_api_audit.test_workflow_api_audit import (
    WF,
    cli,
    decide,
    decide_confirmed,
    start,
    tok,
)

pytestmark = pytest.mark.integration
DEPUTY = (
    "NV-0011"  # phó phòng Kinh doanh; được cấp tạm vai trò dept_head để làm đích ủy quyền hợp lệ
)


def open_confirm(s: Stack, url: str, who_sub: str) -> tuple[httpx.Client, str, dict[str, str]]:
    """Mở trang xác nhận tới bước ngay trước khi bấm Đồng ý (để chen thao tác vào giữa)."""
    http = httpx.Client(base_url=s.app.base, follow_redirects=False, timeout=30)
    r = http.get(urlsplit(url).path)
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    code = s.idp.issue_code(s.idp.id_token(who_sub, nonce=q["nonce"], org=A))
    r = http.get("/desk/confirm/callback", params={"state": q["state"], "code": code})
    assert r.status_code == 200, r.text
    f = _Form()
    f.feed(r.text)
    return http, f.action, {**f.hidden, "decision": "approve"}


def tenant_id(s: Stack) -> Any:
    t = s.tenants[A]
    return getattr(t, "id", t)


def person_sql(pg: pgfixture.Pg, s: Stack, assignment: str) -> None:
    with pg.su() as c:
        c.execute(
            f"UPDATE xt_meta.person SET {assignment} WHERE tenant_id=%s AND key=%s",
            (tenant_id(s), DEPUTY),
        )


def deputy_ref(pg: pgfixture.Pg, s: Stack) -> str:
    with pg.su() as c:
        row = c.execute(
            "SELECT id FROM xt_meta.person WHERE tenant_id=%s AND key=%s", (tenant_id(s), DEPUTY)
        ).fetchone()
    assert row is not None
    return f"person:{row[0]}"


def withdraw(s: Stack, alias: str, who: str, iid: str, rev: int, key: str) -> httpx.Response:
    with cli(s, tok(s, alias, who)) as c:
        return c.post(
            f"{WF}/instances/{iid}/withdraw",
            json={"expected_revision": rev},
            headers={"Idempotency-Key": key},
        )


# ------------------------------------------------------------ hàng rào ĐẠT ----
@pytest.mark.parametrize(
    "change",
    ["active=false", "roles=array_remove(roles,'dept_head')"],
    ids=["deactivated", "role_removed"],
)
def test_delegate_target_changed_between_plan_and_confirm(
    wstack: Stack, pg: pgfixture.Pg, change: str
) -> None:
    s = wstack
    person_sql(
        pg, s, "active=true, roles=array_append(array_remove(roles,'dept_head'),'dept_head')"
    )
    try:
        d = start(s, REQUESTER, f"r2-race-{uuid.uuid4().hex[:12]}", 4_000_000)
        ref, iid = d["request"]["ref"], d["instance"]["id"]
        body = {"decision": "delegate", "delegate_to": deputy_ref(pg, s), "expected_revision": 2}
        key = f"r2-rk-{uuid.uuid4().hex[:10]}"
        p = decide(s, DEPT_HEAD, ref, body, key)
        assert p.status_code == 428, p.text  # đích hợp lệ lúc plan
        person_sql(pg, s, change)
        http, action, form = open_confirm(
            s, p.json()["error"]["details"]["confirm_url"], sub(A, DEPT_HEAD)
        )
        assert http.post(action, data=form).status_code == 409
        r = decide(s, DEPT_HEAD, ref, body, key)
        assert r.status_code == 409 and r.json()["error"]["code"] == "confirmation_declined"
        with cli(s, tok(s, A, REQUESTER)) as c:
            inst = c.get(f"{WF}/instances/{iid}").json()["data"]["instance"]
        assert inst["status"] == "running" and [t["step"] for t in inst["tasks"]] == ["dept_head"]
    finally:
        person_sql(pg, s, "active=true, roles=array_remove(roles,'dept_head')")


def test_withdraw_rules(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "r2-wd-rules-01", 2_000_000)
    iid = d["instance"]["id"]
    for who in (DEPT_HEAD, DIRECTOR):  # người duyệt không rút hộ được
        r = withdraw(s, A, who, iid, 2, f"r2-wd-{who}")
        assert r.status_code in (403, 404), r.text
    foreign = withdraw(s, B, REQUESTER, iid, 2, "r2-wd-foreign")
    ghost = withdraw(s, B, REQUESTER, str(uuid.uuid4()), 2, "r2-wd-fghost")
    assert foreign.status_code == ghost.status_code == 404
    assert foreign.json()["error"] == ghost.json()["error"]
    assert withdraw(s, A, REQUESTER, iid, 1, "r2-wd-stale01").status_code == 409
    ok = withdraw(s, A, REQUESTER, iid, 2, "r2-wd-ok00001")
    assert ok.status_code == 200 and ok.json()["data"]["to_state"] == "withdrawn", ok.text
    assert withdraw(s, A, REQUESTER, iid, 2, "r2-wd-ok00001").status_code == 200  # replay
    assert withdraw(s, A, REQUESTER, iid, 3, "r2-wd-ok00002").status_code == 409
    with cli(s, tok(s, A, REQUESTER)) as c:
        inst = c.get(f"{WF}/instances/{iid}").json()["data"]["instance"]
    assert inst["status"] == "withdrawn" and inst["tasks"] == [] and "withdraw" not in inst["links"]
    r = decide(s, DEPT_HEAD, d["request"]["ref"], {"decision": "approve", "expected_revision": 3},
               "r2-wd-dec-after")  # fmt: skip
    assert r.status_code in (403, 409) and "confirm_url" not in r.text


def test_withdraw_after_decision_and_pending_confirmation(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "r2-wd-after-01", 2_000_000)
    ok = decide_confirmed(
        s, DEPT_HEAD, d["request"]["ref"], {"decision": "approve", "expected_revision": 2},
        "r2-wd-after-ap",
    )  # fmt: skip
    assert ok.status_code == 200 and ok.json()["data"]["outcome"] == "request_approved"
    for rev in (2, 3):
        assert (
            withdraw(s, A, REQUESTER, d["instance"]["id"], rev, f"r2-wd-after-{rev}").status_code
            == 409
        )
    # 428 đang chờ -> người đề nghị rút -> người duyệt bấm xác nhận: không được chạy
    d = start(s, REQUESTER, "r2-wd-pend-01", 8_000_000)
    body = {"decision": "approve", "expected_revision": 2}
    p = decide(s, DEPT_HEAD, d["request"]["ref"], body, "r2-wd-pend-ap")
    assert p.status_code == 428
    assert withdraw(s, A, REQUESTER, d["instance"]["id"], 2, "r2-wd-pend-w").status_code == 200
    http, action, form = open_confirm(
        s, p.json()["error"]["details"]["confirm_url"], sub(A, DEPT_HEAD)
    )
    assert http.post(action, data=form).status_code == 409
    assert decide(s, DEPT_HEAD, d["request"]["ref"], body, "r2-wd-pend-ap").status_code == 409
    with cli(s, tok(s, A, REQUESTER)) as c:
        assert (
            c.get(f"{WF}/instances/{d['instance']['id']}").json()["data"]["instance"]["status"]
            == "withdrawn"
        )


def test_etag_edge_cases(wstack: Stack) -> None:
    s = wstack
    url = f"{WF}/definitions/proposal_approval/bpmn"
    with cli(s, tok(s, A, REQUESTER)) as c:
        et = c.get(url).headers["etag"]
        match = {"*", f"W/{et}", f'"a", {et} ,"b"'}
        nomatch = {et.strip('"'), et[:-3] + '"', et.upper(), f"w/{et}"}
        for h in match:
            r = c.get(url, headers={"If-None-Match": h})
            assert r.status_code == 304 and r.headers["etag"] == et and r.content == b"", h
            assert r.headers["cache-control"] == "private, no-cache"
        for h in nomatch:
            assert c.get(url, headers={"If-None-Match": h}).status_code == 200, h
        assert c.head(url, headers={"If-None-Match": et}).status_code == 304
        ghost = c.get(f"{WF}/definitions/khong_ton_tai/bpmn", headers={"If-None-Match": "*"})
    assert ghost.status_code == 404


# ------------------------------------------------------------ lỗi (xfail strict) ----
def test_wa11_concurrent_confirm_and_withdraw_never_500(wstack: Stack) -> None:
    s = wstack
    seen: list[tuple[int, int]] = []
    for i in range(60):  # tỉ lệ dính ~1/7..1/20 mỗi lượt; dừng ở lượt đầu ra 5xx
        d = start(s, REQUESTER, f"r2-cc-{uuid.uuid4().hex[:10]}", 2_000_000)
        body = {"decision": "approve", "expected_revision": 2}
        p = decide(
            s, DEPT_HEAD, d["request"]["ref"], body, f"r2-cc-ap-{i:04d}-{uuid.uuid4().hex[:6]}"
        )
        assert p.status_code == 428
        http, action, form = open_confirm(
            s, p.json()["error"]["details"]["confirm_url"], sub(A, DEPT_HEAD)
        )
        t = tok(s, A, REQUESTER)
        iid = d["instance"]["id"]

        def wd(t: str = t, iid: str = iid) -> int:
            with cli(s, t) as c:
                return c.post(
                    f"{WF}/instances/{iid}/withdraw",
                    json={"expected_revision": 2},
                    headers={"Idempotency-Key": f"r2-cc-wd-{uuid.uuid4().hex[:10]}"},
                ).status_code

        with cf.ThreadPoolExecutor(2) as ex:
            fw = ex.submit(wd)
            fc = ex.submit(http.post, action, data=form)
            pair = (fc.result().status_code, fw.result())
        seen.append(pair)
        assert pair[0] < 500 and pair[1] < 500, seen
        assert sorted(pair) != [200, 200], seen  # không bao giờ cả hai cùng thành công


def test_wa12_withdraw_by_non_requester_looks_like_ghost(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "r2-wa12-0001", 2_000_000)
    real = withdraw(s, A, OTHER, d["instance"]["id"], 2, "r2-wa12-real")
    ghost = withdraw(s, A, OTHER, str(uuid.uuid4()), 2, "r2-wa12-ghost")
    assert real.status_code == ghost.status_code == 404, real.text
    assert real.json()["error"] == ghost.json()["error"]
