# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit REST /workflow/v1 (docs/audit-workflow-api.md) qua HTTP thật trên Postgres 17.

- Test thường: hàng rào bảo mật đã ĐẠT, giữ làm regression.
- `xfail(strict=True)`: lỗi đã thấy (WA-xx trong báo cáo). Sửa xong thì test thành XPASS -> đỏ,
  nhắc gỡ marker.
"""

from __future__ import annotations

import concurrent.futures as cf
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from tests.integration.composition.idp import DESK_CLIENT
from tests.integration.composition.test_e2e import (
    DEPT_HEAD,
    DIRECTOR,
    REQUESTER,
    Stack,
    advance,
    confirm_in_desk,
    sub,
)
from tests.integration.workflow_api_audit.conftest import OTHER, OTHER_HEAD, A, B
from xt.adapters.http.workflow_api import TASK_ID

pytestmark = pytest.mark.integration
WF = "/workflow/v1"
ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- tiện ích ----
def tok(s: Stack, alias: str, key: str, **over: Any) -> str:
    claims: dict[str, Any] = {"aud": ["xt-workflow"], "scope": "openid xt-workflow"}
    claims.update(over)
    return s.idp.access_token(sub(alias, key), org=alias, **claims)


def cli(s: Stack, token: str | None) -> httpx.Client:
    h = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=s.app.base, headers=h, timeout=30)


def start(s: Stack, who: str, key: str, amount: int, alias: str = A) -> dict[str, Any]:
    body = {"definition_id": "proposal_approval", "values": advance(amount)}
    with cli(s, tok(s, alias, who)) as c:
        r = c.post(f"{WF}/instances", json=body, headers={"Idempotency-Key": key})
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


def dec_path(ref: str) -> str:
    return f"{WF}/tasks/{quote(ref, safe=':')}/decision"


def decide(s: Stack, who: str, ref: str, body: dict[str, Any], key: str) -> httpx.Response:
    with cli(s, tok(s, A, who)) as c:
        return c.post(dec_path(ref), json=body, headers={"Idempotency-Key": key})


def decide_confirmed(
    s: Stack, who: str, ref: str, body: dict[str, Any], key: str
) -> httpx.Response:
    r = decide(s, who, ref, body, key)
    if r.status_code == 428:
        confirm_in_desk(s, r.json()["error"]["details"]["confirm_url"], sub(A, who))
        r = decide(s, who, ref, body, key)
    return r


def inbox_rev(s: Stack, who: str, ref: str) -> int | None:
    with cli(s, tok(s, A, who)) as c:
        items = c.get(f"{WF}/inbox", params={"kinds": "approval"}).json()["data"]["items"]
    return next((int(i["revision"]) for i in items if i["ref"] == ref), None)


def owner_ref(s: Stack, alias: str, who: str, iid: str) -> str:
    with cli(s, tok(s, alias, who)) as c:
        return str(c.get(f"{WF}/instances/{iid}").json()["data"]["request"]["owner"]["ref"])


def strip_trace(err: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in err.items() if k != "explain_ref"}


# ------------------------------------------------------- hàng rào đã ĐẠT ----
def test_token_confusion_rejected(wstack: Stack) -> None:
    s = wstack
    cases = {
        "mcp": s.token(A, REQUESTER),
        "data": tok(s, A, REQUESTER, aud=["xt-data"], scope="openid xt-data"),
        "azp": tok(s, A, REQUESTER, azp="evil-client"),
        "desk_agent": tok(s, A, REQUESTER, azp="xt-desk-agent"),
        "human": tok(s, A, DEPT_HEAD, azp=DESK_CLIENT),
        "expired": tok(s, A, REQUESTER, exp=int(time.time()) - 100),
        "iss": tok(s, A, REQUESTER, iss="http://evil"),
    }
    for name, t in cases.items():
        with cli(s, t) as c:
            r = c.get(f"{WF}/inbox")
        assert r.status_code == 401, (name, r.text)
        assert r.headers["www-authenticate"].startswith('Bearer realm="xt-workflow"'), name
    with cli(s, tok(s, A, REQUESTER, scope="openid mcp-xt")) as c:
        r = c.get(f"{WF}/inbox")
    assert r.status_code == 403 and 'error="insufficient_scope"' in r.headers["www-authenticate"]
    with cli(s, tok(s, B, REQUESTER, organization=[A])) as c:  # sub của B, org A
        assert c.get(f"{WF}/inbox").json()["error"]["details"]["reason"] == "invalid_behalf"


def test_idempotency_scoped_per_actor_and_tenant(wstack: Stack) -> None:
    s = wstack
    body = {"definition_id": "proposal_approval", "values": advance(7_000_000)}
    k = {"Idempotency-Key": "audit-idem-0001"}
    with cli(s, tok(s, A, REQUESTER)) as c:
        first = c.post(f"{WF}/instances", json=body, headers=k)
        other_body = c.post(
            f"{WF}/instances", json={**body, "values": advance(7_500_000)}, headers=k
        )
        replay = c.post(f"{WF}/instances", json=body, headers=k)
    assert first.status_code == 201 and replay.status_code == 200
    assert other_body.status_code == 409
    assert other_body.json()["error"]["code"] == "idempotency_conflict"
    iid = first.json()["data"]["instance"]["id"]
    for alias, who in ((A, OTHER), (B, REQUESTER)):
        with cli(s, tok(s, alias, who)) as c:
            r = c.post(f"{WF}/instances", json=body, headers=k)
        assert r.status_code == 201, r.text
        assert r.json()["data"]["instance"]["id"] != iid


def test_idempotency_concurrent_same_key_one_instance(wstack: Stack) -> None:
    s = wstack
    t = tok(s, A, REQUESTER)
    body = {"definition_id": "proposal_approval", "values": advance(6_100_000)}

    def one(_: int) -> tuple[int, str | None]:
        with cli(s, t) as c:
            r = c.post(f"{WF}/instances", json=body, headers={"Idempotency-Key": "audit-conc-01"})
        d = r.json()
        return r.status_code, (d.get("data") or {}).get("instance", {}).get("id")

    with cf.ThreadPoolExecutor(10) as ex:
        res = list(ex.map(one, range(10)))
    assert all(st in (200, 201, 429) for st, _ in res), res
    assert [st for st, _ in res].count(201) == 1, res
    assert len({i for _, i in res if i}) == 1, res


def test_decision_forgery_blocked(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-forge-01", 8_000_000)
    ref = d["request"]["ref"]
    body = {"decision": "approve", "expected_revision": 2}
    for who, key in (
        (REQUESTER, "audit-forge-sod"),
        (OTHER, "audit-forge-oth"),
        (OTHER_HEAD, "audit-forge-ohd"),
        (DIRECTOR, "audit-forge-dir"),
    ):
        r = decide(s, who, ref, body, key)
        assert r.status_code == 403, (who, r.text)
        assert "confirm_url" not in r.text
    with cli(s, tok(s, B, DEPT_HEAD)) as c:
        r = c.post(
            f"{WF}/tasks/xrn:{A}:{quote(ref, safe=':')}/decision",
            json=body,
            headers={"Idempotency-Key": "audit-forge-xrn"},
        )
    assert r.status_code in (403, 404) and "confirm_url" not in r.text
    # autonomy 3 vẫn phải 428; xác nhận bởi người khác/tenant khác không có tác dụng
    hk = "audit-forge-dh1"
    p = decide(s, DEPT_HEAD, ref, body, hk)
    assert p.status_code == 428, p.text
    url = p.json()["error"]["details"]["confirm_url"]
    assert p.headers["link"] == f'<{url}>; rel="confirm"'
    assert (
        decide(s, DEPT_HEAD, ref, {**body, "decision": "reject", "reason": "x"}, hk).status_code
        == 409
    )
    for who, over in (
        (sub(A, OTHER), {}),
        (sub(A, REQUESTER), {}),
        (sub(B, DEPT_HEAD), {"org": B}),
    ):
        assert confirm_in_desk(s, url, who, **over).status_code == 403
    assert decide(s, DEPT_HEAD, ref, body, hk).status_code == 428
    assert confirm_in_desk(s, url, sub(A, DEPT_HEAD)).status_code == 200
    done = decide(s, DEPT_HEAD, ref, body, hk)
    assert done.status_code == 200 and done.json()["data"]["outcome"] == "step_completed"
    # khóa của trưởng phòng do người đề nghị dùng lại: không nhận receipt của người khác
    r = decide(s, REQUESTER, ref, body, hk)
    assert r.status_code == 403 and r.json()["error"]["details"]["reason"] == "self_approval"
    # request_info -> 400
    r = decide(
        s, DIRECTOR, ref, {"decision": "request_info", "expected_revision": 3}, "audit-ri-0001"
    )
    assert r.status_code == 400
    assert r.json()["error"]["details"]["reason"] == "decision_not_supported"


def test_decision_on_completed_instance(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-cmp-0001", 4_000_000)  # <= 5 triệu: một cấp
    ref, iid = d["request"]["ref"], d["instance"]["id"]
    r = decide_confirmed(
        s, DEPT_HEAD, ref, {"decision": "approve", "expected_revision": 2}, "audit-cmp-dh1"
    )
    assert r.status_code == 200 and r.json()["data"]["outcome"] == "request_approved", r.text
    with cli(s, tok(s, A, REQUESTER)) as c:
        st = c.get(f"{WF}/instances/{iid}").json()["data"]["instance"]
    assert st["status"] == "completed" and st["tasks"] == []
    for who in (DEPT_HEAD, DIRECTOR):
        for rev in (3, 4):
            r = decide(s, who, ref, {"decision": "reject", "reason": "x", "expected_revision": rev},
                       f"audit-cmp-{who[-2:]}{rev}")  # fmt: skip
            assert r.status_code in (403, 409), r.text
            assert "confirm_url" not in r.text


def test_tenant_isolation_indistinguishable(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-iso-0001", 2_000_000)
    ref, iid = d["request"]["ref"], d["instance"]["id"]
    body = {"decision": "approve", "expected_revision": 2}
    with cli(s, tok(s, B, REQUESTER)) as c:
        foreign = c.get(f"{WF}/instances/{iid}")
        ghost = c.get(f"{WF}/instances/{uuid.uuid4()}")
        fd = c.post(dec_path(ref), json=body, headers={"Idempotency-Key": "audit-iso-d1"})
        gd = c.post(dec_path(f"advance_request:{uuid.uuid4()}"), json=body,
                    headers={"Idempotency-Key": "audit-iso-d2"})  # fmt: skip
        fs = c.post(
            f"{WF}/instances", json={"ref": ref}, headers={"Idempotency-Key": "audit-iso-s1"}
        )
        gs = c.post(f"{WF}/instances", json={"ref": f"advance_request:{uuid.uuid4()}"},
                    headers={"Idempotency-Key": "audit-iso-s2"})  # fmt: skip
        bpmn_b = c.get(f"{WF}/definitions/proposal_approval/bpmn")
        ghost_def = c.get(f"{WF}/definitions/khong_ton_tai/bpmn")
    assert foreign.status_code == ghost.status_code == 404
    assert foreign.json()["error"] == ghost.json()["error"]
    for f, g in ((fd, gd), (fs, gs)):
        assert f.status_code == g.status_code
        assert strip_trace(f.json()["error"]) == strip_trace(g.json()["error"])
    assert ghost_def.status_code == 404
    text = bpmn_b.text
    assert "/home/" not in text and "xrn:" not in text and "<!DOCTYPE" not in text
    assert A not in text and B not in text and "@" not in text.replace("xmlns", "")


def test_error_envelopes_pii_scrubbed(wstack: Stack) -> None:
    s = wstack
    cccd, phone = "079203001234", "0912345678"
    with cli(s, tok(s, A, REQUESTER)) as c:
        rs = [
            c.get(f"{WF}/inbox", params={"due_before": cccd}),
            c.get(f"{WF}/inbox", params={"kinds": phone}),
            c.get(f"{WF}/inbox", params={"status": "tri.nguyen@binhminh.vn"}),
            c.post(f"{WF}/instances", content=b'{"ref":"a:b","ref":"' + cccd.encode() + b'"}',
                   headers={"Idempotency-Key": "audit-pii-001",
                            "Content-Type": "application/json"}),
            c.post(f"{WF}/instances", json={"ref": cccd, "note": cccd * 400},
                   headers={"Idempotency-Key": "audit-pii-002"}),
        ]  # fmt: skip
    for r in rs:
        assert r.status_code == 400, r.text
        assert cccd not in r.text and phone not in r.text and "binhminh.vn" not in r.text, r.text


# ------------------------------------------------------- lỗi (xfail strict) ----
def test_wa01_deploy_allows_the_client_that_gets_the_workflow_scope() -> None:
    text = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    block = text.split("\n  xt-api:\n", 1)[1].split("\n  xt-", 1)[0]  # service xt-api
    line = next((x for x in block.splitlines() if "XT_WORKFLOW_ALLOWED_CLIENTS:" in x), "")
    assert "xt-cli" in line or "XT_WORKFLOW_CLIENTS" in line, line


def test_wa02_delegation_to_unauthorized_person_does_not_deadlock(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-wa02-001", 5_100_000)
    ref = d["request"]["ref"]
    oth = start(s, OTHER, "audit-wa02-oth", 150_000)
    oh_ref = oth["route"][0]["approver"]["ref"]  # trưởng phòng Kế toán
    body = {"decision": "delegate", "delegate_to": oh_ref, "expected_revision": 2}
    r = decide_confirmed(s, DEPT_HEAD, ref, body, "audit-wa02-dlg")
    if r.status_code != 200:
        # bị từ chối ngay lúc plan (không 428, không 500): hành vi đúng
        assert r.status_code == 403, r.text
        assert r.json()["error"]["details"]["reason"] == "delegate_not_permitted", r.text
        return
    rev = inbox_rev(s, OTHER_HEAD, ref) or 3
    r = decide(
        s, OTHER_HEAD, ref, {"decision": "approve", "expected_revision": rev}, "audit-wa02-oh1"
    )
    assert r.status_code == 428, r.text


def test_wa03_invalid_delegate_target_rejected_before_confirmation(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-wa03-001", 4_000_000)
    ref, iid = d["request"]["ref"], d["instance"]["id"]
    b = start(s, REQUESTER, "audit-wa03-b01", 1_000_000, alias=B)
    targets = {
        "ghost": f"person:{uuid.uuid4()}",
        "foreign": owner_ref(s, B, REQUESTER, b["instance"]["id"]),
        "me": "me",
        "requester": owner_ref(s, A, REQUESTER, iid),
    }
    codes = {}
    for name, target in targets.items():
        body = {"decision": "delegate", "delegate_to": target, "expected_revision": 2}
        codes[name] = decide(s, DEPT_HEAD, ref, body, f"audit-wa03-{name}").status_code
    assert all(c in (400, 403, 404, 409, 422) for c in codes.values()), codes


def test_wa04_instance_task_id_is_the_decision_key(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-wa04-001", 2_000_000)
    with cli(s, tok(s, A, REQUESTER)) as c:
        tasks = c.get(f"{WF}/instances/{d['instance']['id']}").json()["data"]["instance"]["tasks"]
    assert tasks
    for t in tasks:
        assert TASK_ID.fullmatch(t["task_id"]) and t["task_id"] == d["request"]["ref"], t


def test_wa05_unknown_route_and_method_use_error_envelope(wstack: Stack) -> None:
    s = wstack
    with cli(s, tok(s, A, REQUESTER)) as c:
        rs = [c.get(f"{WF}/nope"), c.delete(f"{WF}/inbox"), c.get(f"{WF}/definitions/a/b/c")]
    for r in rs:
        assert r.headers["content-type"].startswith("application/json"), r.text
        assert r.json()["ok"] is False


def test_wa06_if_none_match_weak_and_list(wstack: Stack) -> None:
    s = wstack
    with cli(s, tok(s, A, REQUESTER)) as c:
        et = c.get(f"{WF}/definitions/proposal_approval/bpmn").headers["etag"]
        weak = c.get(
            f"{WF}/definitions/proposal_approval/bpmn", headers={"If-None-Match": f"W/{et}"}
        )
        lst = c.get(
            f"{WF}/definitions/proposal_approval/bpmn", headers={"If-None-Match": f'"x", {et}'}
        )
    assert weak.status_code == 304 and lst.status_code == 304


def test_wa07_same_tenant_unauthorized_instance_looks_like_ghost(wstack: Stack) -> None:
    s = wstack
    d = start(s, REQUESTER, "audit-wa07-001", 2_000_000)
    for who in (OTHER, OTHER_HEAD):
        with cli(s, tok(s, A, who)) as c:
            real = c.get(f"{WF}/instances/{d['instance']['id']}")
            ghost = c.get(f"{WF}/instances/{uuid.uuid4()}")
        assert real.status_code == ghost.status_code == 404, (who, real.text)
        assert real.json()["error"] == ghost.json()["error"], who


def test_wa08_multi_audience_token_rejected(wstack: Stack) -> None:
    s = wstack
    dual = s.idp.access_token(
        sub(A, REQUESTER), org=A, aud=[f"{s.app.base}/mcp", "xt-workflow"],
        scope="openid mcp-xt xt-workflow",
    )  # fmt: skip
    with cli(s, dual) as c:
        assert c.get(f"{WF}/inbox").status_code == 401


def test_wa09_definition_and_model_must_agree(wstack: Stack) -> None:
    s = wstack
    body = {
        "definition_id": "proposal_approval",
        "model_id": "person",
        "values": advance(1_300_000),
    }
    with cli(s, tok(s, A, REQUESTER)) as c:
        r = c.post(f"{WF}/instances", json=body, headers={"Idempotency-Key": "audit-wa09-001"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request", r.text


def test_wa10_openapi_declares_observed_statuses(wstack: Stack) -> None:
    s = wstack
    with httpx.Client(base_url=s.app.base) as c:
        paths = c.get(f"{WF}/openapi.json").json()["paths"]
    inst = paths[f"{WF}/instances"]["post"]["responses"]
    dec = paths[f"{WF}/tasks/{{task_id}}/decision"]["post"]["responses"]
    bpmn = paths[f"{WF}/definitions/{{definition_id}}/bpmn"]["get"]["responses"]
    assert {"413", "429"} <= set(inst) and {"413", "429", "422"} <= set(dec) and "304" in bpmn
