# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E5: làn PII. Client public (xt-desk-agent, tier masked) chỉ nhận token PII; người xem giá trị thật
qua `pii_reveal_ticket` (app-only) + `POST /pii/reveal` (vé một lần, chữ ký khóa khung), không qua
ngữ cảnh LLM; có `pii_egress`; ghi ngược token bị từ chối."""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from xte2e import compose, mcpc, net
from xte2e.flows import A, REQUESTER, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E5"
pytestmark = pytest.mark.e2e
ACCT = "0123456789012"
TOKEN_RE = re.compile(r"\[PII:[a-z2-7]{8,}\]|⟨[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.[a-z][a-z0-9_]{0,63}⟩")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _jwk(pub: ec.EllipticCurvePublicKey) -> dict[str, Any]:
    n = pub.public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": _b64u(n.x.to_bytes(32, "big")), "y": _b64u(n.y.to_bytes(32, "big"))}


def _sign(priv: ec.EllipticCurvePrivateKey, msg: str) -> str:
    r, s = decode_dss_signature(priv.sign(msg.encode(), ec.ECDSA(hashes.SHA256())))
    return _b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def test_masked_then_reveal(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    u = world.by_key(A, REQUESTER)
    made = world.call(u, "create_record", {"model_id": "advance_request", "idempotency_key": key("pii"),
                                           "values": advance(4_000_000, payout_method="bank_transfer", bank_account=ACCT)})
    ev.step("create_record có số tài khoản (client cloud/public)", made)
    assert made.get("ok"), dumps(made)[:500]
    acct = str(made["data"]["record"]["values"].get("bank_account"))
    c.check(ACCT not in dumps(made), "giá trị thật không có trong kết quả tool (ngữ cảnh LLM)")
    c.check(bool(TOKEN_RE.fullmatch(acct)), f"field PII trả token ({acct})")
    ref = made["data"]["record"]["ref"]

    tok = world.token(u)
    tools = {t.name: t for t in mcpc.list_tools(tok)}
    t = tools.get("pii_reveal_ticket")
    if t is None:
        ev.gap("W4 tool app-only pii_reveal_ticket", "tools/list không có")
    vis = ((getattr(t, "meta", None) or {}).get("ui") or {}).get("visibility")
    c.check(vis == ["app"], f"pii_reveal_ticket chỉ hiện cho App (visibility={vis})")

    priv = ec.generate_private_key(ec.SECP256R1())
    raw = mcpc.call_raw(tok, "pii_reveal_ticket", {"tokens": [acct], "pubkey_jwk": _jwk(priv.public_key())})
    meta = (getattr(raw, "meta", None) or {}).get("xt/reveal") or {}
    body_text = raw.content[0].text if raw.content else ""
    ev.step("pii_reveal_ticket", {"isError": raw.isError, "content": body_text[:400], "meta_keys": sorted(meta)})
    c.check(not raw.isError and bool(meta.get("ticket")) and bool(meta.get("url")), "vé nằm ở _meta['xt/reveal']")
    c.check(meta.get("ticket", "x") not in body_text, "vé không nằm trong content")
    if not meta.get("ticket"):
        c.done()
    url = meta["url"]
    c.check(url == f"{world.s.desk}/pii/reveal", f"URL đổi vé là origin Desk ({url})")
    ts = int(time.time())
    payload = {"ticket": meta["ticket"], "ts": ts, "sig": _sign(priv, f"{meta['ticket']}.{ts}")}
    hdr = {"Origin": world.s.desk, "Content-Type": "application/json"}
    with net.client() as h:
        pre = h.options(url, headers={**hdr, "Access-Control-Request-Method": "POST",
                                      "Access-Control-Request-Headers": "content-type"})
        r1 = h.post(url, content=json.dumps(payload), headers=hdr)
        r2 = h.post(url, content=json.dumps(payload), headers=hdr)
        bad_origin = h.post(url, content=json.dumps(payload), headers={**hdr, "Origin": "https://evil.example"})
    ev.step("POST /pii/reveal", {"preflight": pre.status_code, "first": r1.status_code, "replay": r2.status_code,
                                 "replay_body": r2.text[:200], "cache": r1.headers.get("cache-control"),
                                 "bad_origin": bad_origin.status_code})
    c.check(pre.status_code == 204, f"preflight CORS 204 ({pre.status_code})")
    vals = r1.json().get("values", {}) if r1.headers.get("content-type", "").startswith("application/json") else {}
    c.check(r1.status_code == 200 and vals.get(acct) == ACCT, f"đổi vé lần đầu -> giá trị thật ({r1.status_code})")
    c.check("no-store" in (r1.headers.get("cache-control") or ""), "Cache-Control: no-store")
    c.check(r2.status_code == 403, f"dùng lại vé -> 403 ({r2.status_code})")
    c.check(bad_origin.status_code in (400, 403), f"Origin lạ bị từ chối ({bad_origin.status_code})")
    egress = compose.psql("SELECT count(*) FROM xt_meta.pii_egress WHERE tier = 'human'")
    c.check(int(egress or 0) >= 1, f"pii_egress ghi lượt xem tier human ({egress})")

    got = world.call(u, "get_record", {"ref": ref})
    up = world.call(u, "update_record", {"ref": ref, "expected_revision": got["data"]["record"]["revision"],
                                         "values": {"bank_account": acct, "destination": "Huế"}, "idempotency_key": key("pii-w")})
    ev.step("ghi ngược token PII", up)
    c.check(up.get("ok") is False, f"ghi token PII vào field bị từ chối ({err(up).get('code')})")
    c.done()
