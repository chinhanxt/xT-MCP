# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E2: W1 (tạm ứng 8 triệu) bằng MCP client có script, như một connector thật:
PRM -> DCR ẩn danh -> auth-code + PKCE (consent) -> create/submit -> trưởng phòng `decide_request`
(agent) -> `confirmation_required` -> người mở `/desk/confirm` (mật khẩu + TOTP) -> agent gọi lại
cùng key (replayed) -> giám đốc duyệt -> approved; mở link thiếu TOTP thì không có Decision."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

import pytest

from xte2e import mcpc, net, oidc, seed
from xte2e.flows import A, DEPT_HEAD, DIRECTOR, REQUESTER, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E2"
pytestmark = pytest.mark.e2e


class Connector:
    """Một MCP client đã DCR: mỗi người dùng một phiên auth-code + PKCE riêng."""

    def __init__(self, client_id: str, redirect: str) -> None:
        self.client_id, self.redirect = client_id, redirect
        self.sessions: dict[str, oidc.Session] = {}

    def token(self, u: dict[str, Any]) -> str:
        if u["username"] not in self.sessions:
            self.sessions[u["username"]] = oidc.Session(
                u, self.client_id, self.redirect, "openid mcp-xt"
            )
        return self.sessions[u["username"]].access()

    def call(self, u: dict[str, Any], tool: str, args: dict[str, Any]) -> dict[str, Any]:
        return mcpc.call(self.token(u), tool, args)


@pytest.fixture(scope="module")
def connector(world) -> Connector:  # type: ignore[no-untyped-def]
    redirect = f"http://127.0.0.1:{random.randint(20000, 60000)}/callback"
    reg = oidc.register_client(redirect, "xt-e2e connector", ip=net.run_ip())
    cid = reg["client_id"]
    # Người vận hành cấp L1 cho connector mới (agent DCR mặc định không có quyền): `xt org import --links`.
    t = world.tenants[A]
    grants = [
        {"person": k, "client_id": cid, "autonomy": 1, "roles": ["agent"]}
        for k in (REQUESTER, DEPT_HEAD, DIRECTOR)
    ]
    seed.import_org(A, Path(t["org_file"]), t["links"], grants)
    return Connector(cid, redirect)


def test_discovery_and_401(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    with net.client() as h:
        prm = h.get(f"https://{world.s.hosts['mcp']}/.well-known/oauth-protected-resource/mcp")
        ev.step(
            "PRM",
            {
                "status": prm.status_code,
                "body": prm.json() if prm.status_code == 200 else prm.text[:300],
            },
        )
        c.check(
            prm.status_code == 200
            and world.s.issuer in prm.json().get("authorization_servers", []),
            "PRM khai authorization server = issuer Keycloak",
        )
        r = h.post(
            world.s.mcp_url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        ev.step(
            "POST /mcp không token",
            {"status": r.status_code, "www-authenticate": r.headers.get("www-authenticate")},
        )
        c.check(
            r.status_code == 401
            and "resource_metadata" in (r.headers.get("www-authenticate") or ""),
            "không token -> 401 + WWW-Authenticate resource_metadata",
        )
        disc = h.get(f"{world.s.issuer}/.well-known/openid-configuration").json()
        c.check(bool(disc.get("registration_endpoint")), "discovery có registration_endpoint (DCR)")
    c.done()


def test_w1_end_to_end(ev: Evidence, world, connector: Connector) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    req, dh, dr = world.by_key(A, REQUESTER), world.by_key(A, DEPT_HEAD), world.by_key(A, DIRECTOR)
    tok = connector.token(req)
    cl = oidc.claims(tok)
    ev.step("token connector DCR", {k: cl.get(k) for k in ("azp", "aud", "organization", "scope")})
    c.check(cl.get("azp") == connector.client_id, "token do client DCR cấp (azp)")

    created = connector.call(
        req,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(8_000_000),
            "idempotency_key": key("w1-c"),
        },
    )
    ev.step("create_record 8 triệu", created)
    assert created.get("ok"), f"create_record: {dumps(created)[:600]}"
    ref = created["data"]["record"]["ref"]
    sub_args = {"ref": ref, "idempotency_key": key("w1-s"), "note": "Công tác Đà Nẵng 3 ngày"}
    submitted = connector.call(req, "submit_request", sub_args)
    ev.step("submit_request", submitted)
    assert submitted.get("ok"), f"submit_request: {dumps(submitted)[:600]}"
    route = [r["step"] for r in submitted["data"]["route"]]
    c.check(route == ["dept_head", "director"], f"tuyến duyệt dept_head -> director ({route})")
    again = connector.call(req, "submit_request", sub_args)
    c.check(
        again.get("ok") and again["data"] == submitted["data"],
        "gửi lại cùng key -> cùng kết quả (idempotent)",
    )

    inbox = connector.call(dh, "get_inbox", {"kinds": ["approval"]})
    ev.step("get_inbox trưởng phòng", inbox)
    item = next(
        (i for i in (inbox.get("data") or {}).get("items", []) if i.get("ref") == ref), None
    )
    assert item, "đề nghị không có trong inbox trưởng phòng"
    dec = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": item["revision"],
        "idempotency_key": key("w1-dh"),
    }
    pending = connector.call(dh, "decide_request", dec)
    ev.step("decide_request (agent)", pending)
    c.check(
        err(pending).get("code") == "confirmation_required",
        "agent quyết định -> confirmation_required",
    )
    url = (err(pending).get("details") or {}).get("confirm_url", "")
    c.check(
        url.startswith(f"{world.s.desk}/desk/confirm/"), f"confirm_url trên host Desk ({url[:60]})"
    )
    assert url

    no_otp = world.confirm(url, dh, with_totp=False)
    ev.step("mở link nhưng không có TOTP", no_otp)
    c.check(
        no_otp.get("stage") == "idp" and "OTP" in no_otp.get("error", ""),
        "Keycloak dừng ở bước OTP",
    )
    still = connector.call(dh, "decide_request", dec)
    c.check(err(still).get("code") == "confirmation_required", "chưa có Decision khi thiếu TOTP")

    ok = world.confirm(url, dh)
    ev.step("trưởng phòng xác nhận (mật khẩu + TOTP)", ok)
    c.check(
        ok.get("stage") == "decided"
        and ok.get("status") == 200
        and "Đã thực hiện" in ok.get("body", ""),
        "trang /desk/confirm: Đã thực hiện",
    )
    done = connector.call(dh, "decide_request", dec)
    ev.step("agent gọi lại cùng key", done)
    c.check(
        done.get("ok") is True and done["data"].get("outcome") == "step_completed",
        "bước trưởng phòng hoàn tất",
    )
    receipt = (done.get("meta") or {}).get("receipt") or {}
    c.check(
        receipt.get("replayed") is True,
        f"replayed=true trên biên nhận ({receipt.get('replayed')!r})",
    )

    got = connector.call(dr, "get_record", {"ref": ref})
    dec2 = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": got["data"]["record"]["revision"],
        "idempotency_key": key("w1-dr"),
    }
    p2 = connector.call(dr, "decide_request", dec2)
    url2 = (err(p2).get("details") or {}).get("confirm_url", "")
    assert url2, f"giám đốc: {dumps(p2)[:400]}"
    ok2 = world.confirm(url2, dr)
    ev.step("giám đốc xác nhận", ok2)
    final = connector.call(dr, "decide_request", dec2)
    ev.step("kết quả cuối", final)
    c.check(
        final.get("ok") is True
        and (final.get("data") or {}).get("request", {}).get("state") == "approved",
        "đề nghị approved",
    )
    hist = connector.call(req, "get_history", {"ref": ref, "view": "audit"})
    ev.step("get_history audit", (hist.get("data") or {}).get("chain_verified"))
    c.check((hist.get("data") or {}).get("chain_verified") is True, "chuỗi audit kiểm OK")
    tl = connector.call(req, "get_history", {"ref": ref})
    actions = [e.get("action") for e in (tl.get("data") or {}).get("events", [])]
    ev.step("dòng thời gian", actions)
    c.check(
        "advance_request.created" in actions and actions.index("advance_request.created") == 0
        if actions
        else False,
        "audit đúng thứ tự (created đầu tiên)",
    )
    c.done()
