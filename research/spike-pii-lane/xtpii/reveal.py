# SPDX-License-Identifier: Apache-2.0
"""Kênh cho NGƯỜI xem giá trị thật, không đi qua host/LLM.

1. Iframe MCP App (ui://) tạo cặp khóa ECDSA P-256 *non-extractable* bằng WebCrypto.
2. Iframe gọi tool app-only `pii_reveal_ticket(tokens, pubkey_jwk)` qua host -> nhận vé (không phải PII).
   Vé đi qua host (và có thể lọt vào context/log của nhà cung cấp) nên vé phải vô dụng nếu bị chép:
   một lần dùng, sống 60 s, gắn `sub` + tập token + public key của iframe, chỉ nhận từ Origin sandbox đã khai.
3. Iframe `fetch` THẲNG tới xT `POST /pii/reveal` (khai trong `_meta.ui.csp.connectDomains`) kèm chữ ký
   P1363 trên "ticket.ts" -> xT trả giá trị thật qua TLS về trình duyệt người dùng; host không thấy body.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from .lane import Audit, CallCtx, LaneError
from .policy import Principal, TenantPolicy
from .vault import TOKEN_RE, Vault

TTL = 60
SKEW = 30


def b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwk_to_pub(jwk: dict) -> ec.EllipticCurvePublicKey:
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise LaneError("BAD_KEY", "pubkey phải là JWK EC P-256")
    return ec.EllipticCurvePublicNumbers(int.from_bytes(b64u_dec(jwk["x"]), "big"),
                                         int.from_bytes(b64u_dec(jwk["y"]), "big"), ec.SECP256R1()).public_key()


@dataclass
class RevealService:
    conn: sqlite3.Connection
    vault: Vault
    audit: Audit
    policy: TenantPolicy
    allowed_origins: frozenset[str]

    def __post_init__(self):
        self.conn.execute("""CREATE TABLE IF NOT EXISTS reveal_ticket(
            th TEXT PRIMARY KEY, principal_json TEXT, tokens_json TEXT, jwk_json TEXT, exp REAL, used INTEGER DEFAULT 0)""")

    def mint(self, p: Principal, tokens: list[str], pubkey_jwk: dict) -> dict:
        jwk_to_pub(pubkey_jwk)
        if not tokens or len(tokens) > 50:
            raise LaneError("BAD_TOKENS", "Cần 1..50 token")
        for t in tokens:
            if not TOKEN_RE.fullmatch(t) or self.vault.get(p.session_key, t) is None:
                raise LaneError("PII_TOKEN_UNKNOWN", f"Token {t} không thuộc phiên này")
        ticket = secrets.token_urlsafe(32)
        pj = json.dumps({"tenant": p.tenant, "azp": p.azp, "sub": p.sub, "sid": p.sid, "roles": sorted(p.roles)})
        self.conn.execute("INSERT INTO reveal_ticket(th,principal_json,tokens_json,jwk_json,exp) VALUES(?,?,?,?,?)",
                          (hashlib.sha256(ticket.encode()).hexdigest(), pj, json.dumps(tokens, ensure_ascii=False),
                           json.dumps(pubkey_jwk), time.time() + TTL))
        return {"ticket": ticket, "expires_in": TTL}

    def redeem(self, ticket: str, ts: int, sig_b64u: str, origin: str | None) -> dict:
        if origin not in self.allowed_origins:
            raise LaneError("BAD_ORIGIN", "Origin không được phép")
        th = hashlib.sha256(ticket.encode()).hexdigest()
        # đánh dấu đã dùng NGAY (nguyên tử) để chặn replay song song
        cur = self.conn.execute("UPDATE reveal_ticket SET used=1 WHERE th=? AND used=0 AND exp>? "
                                "RETURNING principal_json, tokens_json, jwk_json", (th, time.time()))
        r = cur.fetchone()
        if r is None:
            raise LaneError("TICKET_INVALID", "Vé không hợp lệ, đã dùng hoặc đã hết hạn")
        if abs(time.time() - ts) > SKEW:
            raise LaneError("TICKET_INVALID", "Dấu thời gian lệch")
        sig = b64u_dec(sig_b64u)
        if len(sig) != 64:
            raise LaneError("TICKET_INVALID", "Chữ ký phải dạng P1363 64 byte (WebCrypto)")
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        try:
            jwk_to_pub(json.loads(r[2])).verify(der, f"{ticket}.{ts}".encode(), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature:
            raise LaneError("TICKET_INVALID", "Chữ ký không khớp khóa của iframe")
        pd = json.loads(r[0])
        p = Principal(tenant=pd["tenant"], azp=pd["azp"], sub=pd["sub"], sid=pd["sid"], roles=frozenset(pd["roles"]),
                      tier="human")
        ctx = CallCtx(p=p, mode="full")
        values, denied = {}, []
        for t in json.loads(r[1]):
            e = self.vault.get(p.session_key, t)
            need = self.policy.reveal_roles.get(e.level, frozenset()) if e else frozenset()
            if e is None or (need and not (need & p.roles)):
                denied.append(t); continue
            values[t] = e.value
            ctx.egress[f"{e.model or 'free'}.{e.field or e.kind}:reveal_human"] += 1
        self.audit.write(ctx, "pii_reveal", "ok" if values else "denied")
        return {"values": values, "denied": denied}
