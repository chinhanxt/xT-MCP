# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E4: cô lập 2 tenant qua MCP (`search_records`/`get_record`) và PostgREST `/data` (Accept-Profile),
user thuộc 2 Organization. Token Keycloak thật, qua APISIX."""

from __future__ import annotations

import uuid

import pytest

from xte2e import net, oidc
from xte2e.config import HEADLESS_CLIENT, HEADLESS_REDIRECT
from xte2e.flows import A, B, B_EMPLOYEE, REQUESTER, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E4"
pytestmark = pytest.mark.e2e


def test_mcp_two_tenants(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    ua, ub = world.by_key(A, REQUESTER), world.by_key(B, B_EMPLOYEE)
    made = world.call(ua, "create_record", {"model_id": "advance_request", "values": advance(1_234_000),
                                            "idempotency_key": key("rls")})
    ev.step("A tạo bản ghi", made)
    assert made.get("ok"), dumps(made)
    ref = made["data"]["record"]["ref"]
    found = world.call(ub, "search_records", {"model_id": "advance_request"})
    ev.step("B search_records advance_request", found)
    c.check(found.get("ok") is True, "B tìm được trên model của chính mình")
    c.check(all(i.get("ref") != ref for i in (found.get("data") or {}).get("items", [])), "B không thấy bản ghi của A")
    other = world.call(ub, "get_record", {"ref": ref})
    ghost = world.call(ub, "get_record", {"ref": f"advance_request:{uuid.uuid4()}"})
    ev.step("B get_record(ref của A) / ref không tồn tại", {"other": other, "ghost": ghost})
    c.check(other.get("ok") is False and "1234000" not in dumps(other), "B đọc ref của A bị từ chối, không lộ dữ liệu")
    c.check(err(other).get("code") == err(ghost).get("code"), f"lỗi giống hệt id không tồn tại ({err(other).get('code')} vs {err(ghost).get('code')})")
    c.done()


def test_postgrest_profiles(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    ta, tb = world.tenants[A], world.tenants[B]
    ua, ub = world.by_key(A, REQUESTER), world.by_key(B, B_EMPLOYEE)
    tok_a = world.token(ua, client=HEADLESS_CLIENT, scope="openid xt-data", redirect=HEADLESS_REDIRECT)
    tok_b = world.token(ub, client=HEADLESS_CLIENT, scope="openid xt-data", redirect=HEADLESS_REDIRECT)
    cl = oidc.claims(tok_a)
    ev.step("token data A (claims)", {k: cl.get(k) for k in ("aud", "azp", "organization", "data_role", "scope")})
    c.check(cl.get("aud") == "xt-data" and cl.get("azp") == HEADLESS_CLIENT, "token data: aud=xt-data, azp=xt-headless")
    url = f"{world.s.api}/data/advance_request"
    with net.client() as h:
        def get(tok: str | None, prof: str) -> tuple[int, str]:
            hd = {"Accept-Profile": prof}
            if tok:
                hd["Authorization"] = f"Bearer {tok}"
            r = h.get(url, headers=hd)
            return r.status_code, r.text[:400]

        own = get(tok_a, ta["profile"])
        cross = get(tok_a, tb["profile"])
        cross_b = get(tok_b, ta["profile"])
        anon = get(None, ta["profile"])
        own_b = get(tok_b, tb["profile"])
        mcp_tok = get(world.token(ua), ta["profile"])
    ev.step("GET /data/advance_request", {"A->A": own, "A->B": cross, "B->A": cross_b, "không token": anon,
                                         "B->B": own_b, "token MCP": mcp_tok})
    c.check(own[0] == 200, f"A đọc profile của A: 200 (thực tế {own[0]})")
    c.check(own_b[0] == 200 and "1234000" not in own_b[1], f"B đọc profile của B: 200, không có dữ liệu A ({own_b[0]})")
    c.check(cross[0] in (401, 403, 406), f"A gửi Accept-Profile của B bị từ chối (thực tế {cross[0]})")
    c.check(cross_b[0] in (401, 403, 406), f"B gửi Accept-Profile của A bị từ chối (thực tế {cross_b[0]})")
    c.check(anon[0] == 401, f"không token -> 401 (thực tế {anon[0]})")
    c.check(mcp_tok[0] in (401, 403), f"token MCP (aud khác) không dùng được cho /data (thực tế {mcp_tok[0]})")
    c.done()


def test_user_in_two_organizations(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    """Keycloak 26 (Organizations, identity-first) bắt user nhiều tổ chức chọn một lúc đăng nhập, nên token
    thật chỉ mang đúng 1 Organization (decisions.md D-E2E-3). Kiểm: token đúng tổ chức đã chọn, xT không
    cấp quyền sang tổ chức kia; nếu token mang >1 tổ chức thì xT phải trả `tenant_ambiguous`."""
    c = Checks(ev)
    m = next(u for u in world.users.values() if u.get("orgs"))
    sess = world.session(m)
    tok = sess.access()
    orgs = oidc.claims(tok).get("organization") or []
    ev.step("token user 2 tổ chức", {"organization": orgs, "chọn lúc đăng nhập": sess.browser.picked_org})
    out = world.call(m, "inspect_workspace", {})
    ev.step("inspect_workspace", out)
    if len(orgs) > 1:
        c.check(err(out).get("code") in ("unauthenticated", "access_denied") and "tenant_ambiguous" in dumps(out),
                "token nhiều tổ chức -> tenant_ambiguous")
    else:
        c.check(orgs == [sess.browser.picked_org], f"token chỉ mang tổ chức đã chọn ({orgs})")
        c.check(out.get("ok") is False and err(out).get("code") == "access_denied",
                "user không có trong sơ đồ tổ chức của tenant -> access_denied (không vào được tenant nào)")
        other = world.call(m, "search_records", {"model_id": "advance_request"})
        c.check(other.get("ok") is False, "không đọc được dữ liệu của tổ chức kia")
    c.done()
