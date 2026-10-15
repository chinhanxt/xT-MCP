# SPDX-License-Identifier: Apache-2.0
"""Kênh cho người: vé một lần + khóa của iframe + fetch thẳng xT (mô phỏng WebCrypto bằng `cryptography`)."""
import base64
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from starlette.testclient import TestClient

from conftest import SANDBOX, assert_no_raw


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def iframe_key():
    k = ec.generate_private_key(ec.SECP256R1())
    n = k.public_key().public_numbers()
    return k, {"kty": "EC", "crv": "P-256", "x": b64u(n.x.to_bytes(32, "big")), "y": b64u(n.y.to_bytes(32, "big"))}


def p1363(k, msg: bytes) -> str:                     # định dạng chữ ký của WebCrypto
    r, s = decode_dss_signature(k.sign(msg, ec.ECDSA(hashes.SHA256())))
    return b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def mint(x, tokens, jwk):
    res, wire, env = x.call("pii_reveal_ticket", {"tokens": tokens, "pubkey_jwk": jwk})
    assert not res.is_error, wire
    t = res.meta["xt/reveal"]["ticket"]
    assert t not in res.content[0].text and t not in json.dumps(res.structured_content)   # vé chỉ ở _meta
    assert_no_raw(wire)
    return t


def redeem(http, ticket, k, origin=SANDBOX, ts=None):
    ts = ts or int(time.time())
    return http.post("/pii/reveal", json={"ticket": ticket, "ts": ts, "sig": p1363(k, f"{ticket}.{ts}".encode())},
                     headers={"origin": origin})


def test_human_reveal_direct_channel(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    k, jwk = iframe_key()
    toks = ["⟨NV-0042.ho_ten⟩", "⟨NV-0042.so_cccd⟩", "⟨NV-0042.luong⟩", "⟨NV-0042.suc_khoe⟩"]
    t = mint(x, toks, jwk)
    with TestClient(x.app.mcp.streamable_http_app()) as http:
        pre = http.options("/pii/reveal", headers={"origin": SANDBOX, "access-control-request-method": "POST"})
        assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == SANDBOX
        r = redeem(http, t, k)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        v = r.json()["values"]
        assert v["⟨NV-0042.luong⟩"] == 25000000 and v["⟨NV-0042.so_cccd⟩"] == "001090012345"
        print("\nNGƯỜI thấy (fetch thẳng xT) ->", r.json())
        assert redeem(http, t, k).status_code == 403                       # replay
    a = x.app.audit.rows()[-1]
    assert (a["tool"], a["tier"], a["mode"]) == ("pii_reveal", "human", "full")
    assert "nhan_vien.luong:reveal_human" in json.loads(a["egress_json"])


def test_reveal_rejects_copied_ticket_wrong_origin_and_role(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    k, jwk = iframe_key()
    with TestClient(x.app.mcp.streamable_http_app()) as http:
        t = mint(x, ["⟨NV-0042.luong⟩"], jwk)
        attacker, _ = iframe_key()                     # ai đó chép được vé từ log host nhưng không có khóa iframe
        assert redeem(http, t, attacker).status_code == 403
        t = mint(x, ["⟨NV-0042.luong⟩"], jwk)
        assert redeem(http, t, k, origin="https://evil.example").status_code == 403
        assert redeem(http, t, k, ts=int(time.time()) - 120).status_code == 403
        # người không có role hr/giam_doc: basic xem được, sensitive bị từ chối
        x.as_client("5c1e7a0e-dcr-claude", roles=frozenset({"nhan_vien"}))
        t = mint(x, ["⟨NV-0042.ho_ten⟩", "⟨NV-0042.luong⟩"], jwk)
        r = redeem(http, t, k).json()
        assert r["values"] == {"⟨NV-0042.ho_ten⟩": "Nguyễn Văn An"} and r["denied"] == ["⟨NV-0042.luong⟩"]


def test_cannot_mint_for_foreign_tokens(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    x.as_client("5c1e7a0e-dcr-claude", sid="sid-OTHER")
    _, jwk = iframe_key()
    res, wire, env = x.call("pii_reveal_ticket", {"tokens": ["⟨NV-0042.luong⟩"], "pubkey_jwk": jwk})
    assert res.is_error and env["error"]["code"] == "PII_TOKEN_UNKNOWN"
