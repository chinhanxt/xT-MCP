# SPDX-License-Identifier: Apache-2.0
"""C. Biên nhận duyệt một lần, ký Ed25519 (JWS EdDSA), gắn với (request_id, request_digest, approver, decision, exp).

xT chỉ phát biên nhận SAU KHI đã kiểm bằng chứng từ Keycloak (token CIBA ở A, hoặc token step-up ở B).
Khi thực thi hành động L2/L3, xT tính lại digest từ bản ghi hiện tại và tiêu jti đúng một lần."""
import hashlib, json, os, secrets, sys, time, uuid
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

KEY_PATH = "/tmp/xt-spike-hitl/receipt_ed25519.pem"
FRESH_S = 120

def _key():
    if not os.path.exists(KEY_PATH):
        k = Ed25519PrivateKey.generate()
        open(KEY_PATH, "wb").write(k.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return serialization.load_pem_private_key(open(KEY_PATH, "rb").read(), None)

def digest(request: dict) -> str:
    canon = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(canon).hexdigest()

def issue(request: dict, evidence: dict, decision: str, ttl=300) -> str:
    """evidence = claims đã kiểm của token Keycloak (A hoặc B)."""
    now = int(time.time())
    if now - int(evidence["auth_time"]) > FRESH_S:
        raise PermissionError(f"auth_time cũ {now - evidence['auth_time']}s > {FRESH_S}s")
    if evidence["preferred_username"] == request["requester"]:
        raise PermissionError("SoD: người duyệt = người đề nghị")
    claims = dict(iss="xt", typ="xt-approval", jti=str(uuid.uuid4()), iat=now, exp=now + ttl,
        request_id=request["id"], request_digest=digest(request), decision=decision,
        approver=evidence["sub"], approver_username=evidence["preferred_username"],
        evidence=dict(azp=evidence["azp"], auth_time=evidence["auth_time"], acr=evidence.get("acr"),
                      amr=evidence.get("amr"), idp_jti=evidence.get("jti") or evidence.get("sid")))
    return jwt.encode(claims, _key(), algorithm="EdDSA", headers={"typ": "xt-approval+jwt"})

def consume(receipt: str, current_request: dict, used: set) -> dict:
    pub = _key().public_key()
    c = jwt.decode(receipt, pub, algorithms=["EdDSA"], issuer="xt",
                   options={"require": ["exp", "iat", "jti"]})
    if c["request_id"] != current_request["id"]:
        raise PermissionError(f"receipt cho {c['request_id']}, không phải {current_request['id']}")
    if c["request_digest"] != digest(current_request):
        raise PermissionError("request_digest không khớp: nội dung yêu cầu đã đổi sau khi duyệt")
    if c["jti"] in used:   # prod: UNIQUE(jti) trong bảng decisions, cùng transaction với hành động
        raise PermissionError("receipt đã dùng (replay)")
    if c["decision"] != "approve":
        raise PermissionError("decision != approve")
    used.add(c["jti"])
    return c

def demo(evidence_file):
    from common import show
    ev = json.load(open(evidence_file))
    r42 = json.load(open("/tmp/xt-spike-hitl/r42.json"))
    used = set()
    rc = issue(r42, ev, "approve")
    show("receipt (JWS EdDSA) header.payload", jwt.decode(rc, options={"verify_signature": False}))
    def attempt(label, fn):
        try:
            out = fn(); print(f"[OK ] {label}: approver={out['approver_username']} request={out['request_id']}")
        except Exception as e:
            print(f"[REJ] {label}: {type(e).__name__}: {e}")
    attempt("1. dùng đúng R-42", lambda: consume(rc, r42, used))
    attempt("2. replay lần 2 trên R-42", lambda: consume(rc, r42, used))
    r43 = dict(r42, id="R-43", requester="bob", amount=50_000_000)
    attempt("3. dùng receipt R-42 cho R-43", lambda: consume(rc, r43, set()))
    r42x = dict(r42, amount=120_000_000)
    attempt("4. R-42 bị sửa amount 12tr->120tr", lambda: consume(rc, r42x, set()))
    r42y = dict(r42, payee_account="9999-attacker")
    attempt("5. R-42 bị đổi tài khoản nhận", lambda: consume(rc, r42y, set()))
    p = rc.split("."); import base64
    body = json.loads(base64.urlsafe_b64decode(p[1] + "=="))
    body["request_id"], body["request_digest"] = "R-43", digest(r43)
    forged = p[0] + "." + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=") + "." + p[2]
    attempt("6. agent sửa payload receipt (giữ chữ ký cũ)", lambda: consume(forged, r43, set()))
    short = jwt.encode(dict(jwt.decode(rc, options={"verify_signature": False}), exp=int(time.time()) - 1,
                            jti=str(uuid.uuid4())), _key(), algorithm="EdDSA")
    attempt("7. receipt hết hạn", lambda: consume(short, r42, set()))
    stale = dict(ev, auth_time=int(time.time()) - 600)
    attempt("8. phát receipt từ bằng chứng auth_time 600s trước", lambda: issue(r42, stale, "approve"))
    selfapp = dict(ev, preferred_username="bob")
    attempt("9. phát receipt khi người duyệt = người đề nghị", lambda: issue(r42, selfapp, "approve"))

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(__file__))
    demo(sys.argv[1])
