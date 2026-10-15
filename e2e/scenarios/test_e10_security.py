# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E10: `deploy/tests/security_smoke.sh` thật; auth âm qua APISIX (alg=none, HS256 khóa công khai,
refresh/ID token làm bearer, token realm master, token sai aud, chữ ký hỏng); SoD: người đề nghị tự
duyệt, agent gọi `decide_request` 100 lần -> 0 Decision, trigger DB `decider ≠ requester`."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess

import pytest

from xte2e import compose, mcpc, net
from xte2e.config import DEPLOY, HEADLESS_CLIENT, HEADLESS_REDIRECT
from xte2e.flows import A, DEPT_HEAD, REQUESTER, advance, dumps, err, key
from xte2e.kcadmin import Admin
from xte2e.report import Checks, Evidence

SCENARIO = "E10"
pytestmark = pytest.mark.e2e


def _b64(d: bytes) -> str:
    return base64.urlsafe_b64encode(d).rstrip(b"=").decode()


def test_security_smoke(ev: Evidence) -> None:
    r = subprocess.run(["bash", str(DEPLOY / "tests" / "security_smoke.sh")], capture_output=True, text=True,
                       timeout=600, cwd=DEPLOY)
    lines = r.stdout.splitlines()
    fails = [ln.strip() for ln in lines if ln.strip().startswith("FAIL")]
    ev.step("security_smoke.sh", {"rc": r.returncode, "tail": lines[-3:], "fails": fails})
    assert r.returncode == 0 and not fails, f"security_smoke: {lines[-1] if lines else ''} {fails[:5]}"


def _status(tok: str, url: str) -> int:
    with net.client() as h:
        return h.post(url, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream"},
                      json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code


def test_auth_negative(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    u = world.by_key(A, REQUESTER)
    sess = world.session(u)
    good = sess.access()
    tok = sess.tok
    assert tok is not None
    hdr, payload, sig = good.split(".")
    url = world.s.mcp_url
    c.check(_status(good, url) == 200, "token hợp lệ -> 200 (đối chứng)")
    none_tok = f"{_b64(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{payload}."
    with net.client() as h:
        jwks = h.get(f"{world.s.issuer}/protocol/openid-connect/certs").json()
    pub = json.dumps(jwks["keys"][0]).encode()
    hs_head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": jwks["keys"][0].get("kid")}).encode())
    hs_tok = f"{hs_head}.{payload}.{_b64(hmac.new(pub, f'{hs_head}.{payload}'.encode(), hashlib.sha256).digest())}"
    broken = f"{hdr}.{payload}.{sig[:-4]}AAAA"
    kc = Admin()
    kc.login()
    master = kc.token
    data_tok = world.token(u, client=HEADLESS_CLIENT, scope="openid xt-data", redirect=HEADLESS_REDIRECT)
    cases = {"alg=none": none_tok, "HS256 ký bằng khóa công khai": hs_tok, "chữ ký hỏng": broken,
             "refresh token làm bearer": tok.refresh or "x", "ID token làm bearer": tok.id_token or "x",
             "token realm master (admin-cli)": master, "token aud xt-data": data_tok}
    res = {name: _status(t, url) for name, t in cases.items()}
    ev.step("POST /mcp với token xấu", res)
    for name, code in res.items():
        c.check(code == 401, f"{name} -> 401 (thực tế {code})")
    c.done()


def test_sod(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    req, dh = world.by_key(A, REQUESTER), world.by_key(A, DEPT_HEAD)
    made = world.call(req, "create_record", {"model_id": "advance_request", "values": advance(2_500_000), "idempotency_key": key("sod")})
    ref = made["data"]["record"]["ref"]
    sub = world.call(req, "submit_request", {"ref": ref, "idempotency_key": key("sod-s")})
    assert sub.get("ok"), dumps(sub)[:300]
    got = world.call(req, "get_record", {"ref": ref})
    rev = got["data"]["record"]["revision"]
    own = world.call(req, "decide_request", {"ref": ref, "decision": "approve", "expected_revision": rev, "idempotency_key": key("sod-own")})
    ev.step("người đề nghị tự duyệt (agent của chính họ)", own)
    c.check(err(own).get("code") == "access_denied" and "confirm_url" not in dumps(own),
            f"tự duyệt -> access_denied, không có link xác nhận ({err(own).get('code')})")

    tok = world.token(dh)
    import anyio

    async def hammer() -> list[str]:
        codes: list[str] = []
        async with mcpc.session(tok) as s:
            for i in range(100):
                r = mcpc.envelope(await s.call_tool("decide_request", {"ref": ref, "decision": "approve", "expected_revision": rev,
                                                                       "idempotency_key": f"{key('sod-h')}-{i}"}))
                codes.append(str(err(r).get("code") or ("ok" if r.get("ok") else "?")))
        return codes

    codes = anyio.run(hammer)
    ev.step("agent trưởng phòng gọi decide_request 100 lần", {c2: codes.count(c2) for c2 in set(codes)})
    after = world.call(req, "get_record", {"ref": ref})
    c.check("ok" not in codes and after["data"]["record"]["state"] == "in_review",
            "100 lần agent -> 0 Decision (vẫn in_review)")
    trig = compose.psql("SELECT tgrelid::regclass || ':' || tgname FROM pg_trigger WHERE NOT tgisinternal"
                        " AND (tgname ILIKE '%sod%' OR tgname ILIKE '%decider%' OR tgname ILIKE '%self_approv%')")
    ev.step("trigger SoD trong DB", trig.splitlines())
    c.check(bool(trig), "W9: có trigger DB chặn decider = requester")
    c.done()
