# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit DPoP (docs/archive/audit-dpop.md): tấn công vào `DpopVerifier` / `AccessTokenVerifier`.

Test thường = hành vi đã kiểm là đúng (PASS, giữ làm hồi quy). `xfail(strict=True)` = lỗi còn mở
(DPA-xx); sửa xong thì test sẽ XPASS -> bỏ marker.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from tests.unit.identity.dpop_client import DpopKey, ath, b64u
from tests.unit.identity.factory import FakeClock
from xt.adapters.identity import DpopError, DpopSettings, DpopVerifier
from xt.adapters.identity.single_use import InMemorySingleUseStore

ORIGIN = "https://xt.example.test"
DESK_ORIGIN = "https://desk.example.test"
HTU = f"{ORIGIN}/mcp"
TOKEN = "at-value"
ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def key(clock: FakeClock) -> DpopKey:
    return DpopKey(clock)


def verifier(clock: FakeClock, **over: Any) -> DpopVerifier:
    s: dict[str, Any] = {"origins": (ORIGIN, DESK_ORIGIN)}
    s.update(over)
    return DpopVerifier(DpopSettings(**s), clock=clock)


@pytest.fixture
def dv(clock: FakeClock) -> DpopVerifier:
    return verifier(clock)


def check(dv: DpopVerifier, proof: str, key: DpopKey | None, **over: Any) -> str:
    kw: dict[str, Any] = {
        "htm": "POST",
        "htu": dv.candidates("/mcp"),
        "bound_to": TOKEN,
        "expected_jkt": key.jkt if key else None,
    }
    kw.update(over)
    return dv.verify([proof], **kw)


def reason(dv: DpopVerifier, proof: str, key: DpopKey | None, **over: Any) -> str:
    with pytest.raises(DpopError) as ei:
        check(dv, proof, key, **over)
    return ei.value.reason


def _raw(header: dict[str, Any], claims: dict[str, Any], sig: bytes = b"") -> str:
    enc = lambda o: b64u(json.dumps(o).encode())  # noqa: E731
    return f"{enc(header)}.{enc(claims)}.{b64u(sig)}"


def _claims(clock: FakeClock, **over: Any) -> dict[str, Any]:
    c = {"jti": "j-1", "htm": "POST", "htu": HTU, "iat": int(clock()), "ath": ath(TOKEN)}
    c.update(over)
    return c


# ------------------------------------------------------- replay giữa endpoint / method ----
def test_replay_across_configured_origins_blocked(dv: DpopVerifier, key: DpopKey) -> None:
    """Proof cho origin Desk được nhận ở origin MCP (cùng server), nhưng cache jti dùng chung."""
    p = key.proof("POST", f"{DESK_ORIGIN}/mcp", token=TOKEN)
    assert check(dv, p, key) == key.jkt
    assert reason(dv, p, key) == "dpop_replay"


@pytest.mark.parametrize(
    ("htm", "path"), [("GET", "/mcp"), ("DELETE", "/mcp"), ("POST", "/pii/reveal")]
)
def test_proof_not_valid_for_other_method_or_endpoint(
    dv: DpopVerifier, key: DpopKey, htm: str, path: str
) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    r = reason(dv, p, key, htm=htm, htu=dv.candidates(path))
    assert r in ("dpop_htm", "dpop_htu")


def test_mcp_proof_not_redeemable_at_pii_reveal(dv: DpopVerifier, key: DpopKey) -> None:
    """ath của /mcp = hash(access token), của /pii/reveal = hash(vé): không chéo được."""
    p = key.proof("POST", f"{ORIGIN}/pii/reveal", token=TOKEN)
    assert reason(dv, p, key, htu={f"{ORIGIN}/pii/reveal"}, bound_to="ticket-xyz") == "dpop_ath"


# ------------------------------------------------------------------- chuẩn hóa htu ----
@pytest.mark.parametrize(
    "htu",
    [
        "https://XT.Example.TEST/mcp",
        "HTTPS://xt.example.test/mcp",
        "https://xt.example.test:443/mcp",
        "https://xt.example.test/mcp?x=1",
        "https://xt.example.test/mcp#frag",
    ],
)
def test_htu_equivalent_forms_accepted(dv: DpopVerifier, key: DpopKey, htu: str) -> None:
    assert check(dv, key.proof("POST", htu, token=TOKEN), key) == key.jkt


@pytest.mark.parametrize(
    "htu",
    [
        "https://xt.example.test/mcp/",  # trailing slash
        "https://xt.example.test/MCP",  # path phân biệt hoa thường
        "https://xt.example.test/%6Dcp",  # không giải mã %-encoding (fail-closed)
        "https://xt.example.test//mcp",
        "https://xt.example.test/./mcp",
        "https://xt.example.test./mcp",  # FQDN có dấu chấm cuối
        "https://xt.example.test:8443/mcp",
        "http://xt.example.test/mcp",
        "https://user@xt.example.test/mcp",
        "https://evil.example/mcp",  # Host header giả không mở rộng tập htu
        "https://xt.example.test.evil.example/mcp",
        "/mcp",
    ],
)
def test_htu_tricks_rejected(dv: DpopVerifier, key: DpopKey, htu: str) -> None:
    assert reason(dv, key.proof("POST", htu, token=TOKEN), key) == "dpop_htu"


def test_htu_candidates_come_from_config_not_host_header(dv: DpopVerifier) -> None:
    assert dv.candidates("/mcp") == {HTU, f"{DESK_ORIGIN}/mcp"}


# ------------------------------------------------------------------- iat / đồng hồ ----
@pytest.mark.parametrize(("delta", "ok"), [(30, True), (31, False), (-90, True), (-91, False)])
def test_iat_window_edges(clock: FakeClock, key: DpopKey, delta: int, ok: bool) -> None:
    dv = verifier(clock)
    p = key.proof("POST", HTU, token=TOKEN, iat=int(clock()) + delta)
    if ok:
        assert check(dv, p, key) == key.jkt
    else:
        assert reason(dv, p, key) in ("dpop_iat_future", "dpop_iat_stale")


@pytest.mark.parametrize("iat", [float("inf"), -float("inf"), 1e30, True, "1700000000", None])
def test_iat_non_numeric_or_infinite_rejected(dv: DpopVerifier, key: DpopKey, iat: Any) -> None:
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, iat=iat), key).startswith("dpop_iat")


def test_iat_nan_is_a_dpop_error(dv: DpopVerifier, key: DpopKey) -> None:
    with pytest.raises(DpopError):
        check(dv, key.proof("POST", HTU, token=TOKEN, iat=float("nan")), key)


def test_replay_window_outlives_acceptance_window(clock: FakeClock, key: DpopKey) -> None:
    """jti phải được nhớ ít nhất tới khi iat hết hạn (kể cả iat tương lai tối đa)."""
    dv = verifier(clock)
    p = key.proof("POST", HTU, token=TOKEN, iat=int(clock()) + 30)
    check(dv, p, key)
    clock.advance(30 + 60 + 30)  # đúng mép cửa sổ chấp nhận
    with pytest.raises(DpopError) as ei:
        check(dv, p, key)
    assert ei.value.reason == "dpop_replay"


def test_presigned_future_proof_accepted_later(clock: FakeClock, key: DpopKey) -> None:
    """Rủi ro tồn dư (không nonce): mã XSS ký sẵn proof cho iat tương lai, dùng sau khi đóng tab."""
    dv = verifier(clock)
    later = key.proof("POST", HTU, token=TOKEN, iat=int(clock()) + 3600)
    assert reason(dv, later, key) == "dpop_iat_future"  # thử sớm: không đốt jti
    clock.advance(3600)
    assert check(dv, later, key) == key.jkt


# --------------------------------------------------------------------- cache jti ----
def test_stolen_proof_cannot_change_jti(dv: DpopVerifier, key: DpopKey) -> None:
    """jti nằm trong phần được ký: đổi hoa/thường hay encoding của jti làm hỏng chữ ký."""
    p = key.proof("POST", HTU, token=TOKEN, jti="AbC")
    check(dv, p, key)
    h, body, sig = p.split(".")
    c = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    for jti in ("abc", "ABC", "AbC ", "AbC"[:-1] + "c"):
        c["jti"] = jti
        forged = f"{h}.{b64u(json.dumps(c).encode())}.{sig}"
        assert reason(dv, forged, key) in ("dpop_signature", "dpop_replay")


def test_jti_scoped_per_key(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    """Kẻ khác dùng cùng jti không đốt được jti của chủ khóa."""
    thief = DpopKey(clock)
    check(dv, thief.proof("POST", HTU, token=TOKEN, jti="shared"), thief)
    assert check(dv, key.proof("POST", HTU, token=TOKEN, jti="shared"), key) == key.jkt


def test_failed_proof_does_not_burn_jti(dv: DpopVerifier, key: DpopKey) -> None:
    bad = key.proof("POST", HTU, token="other-token", jti="keep")
    assert reason(dv, bad, key) == "dpop_ath"
    assert check(dv, key.proof("POST", HTU, token=TOKEN, jti="keep"), key) == key.jkt


def test_replay_rejected_by_second_process(clock: FakeClock, key: DpopKey) -> None:
    """DPA-02: hai tiến trình dùng chung store (bản Postgres: tests/integration/
    test_dpop_replay_pg.py, kể cả restart). Cấu hình `postgres` mà thiếu store thì dừng khởi động,
    không lặng lẽ lùi về bộ nhớ; nhiều worker với store bộ nhớ cũng bị từ chối."""
    from xt.adapters.identity import IdentityConfigError, build_dpop_verifier

    env = {"XT_DPOP_ORIGINS": ORIGIN, "XT_DPOP_REPLAY_STORE": "postgres"}
    with pytest.raises(IdentityConfigError):
        build_dpop_verifier(env)
    with pytest.raises(IdentityConfigError):
        build_dpop_verifier({"XT_DPOP_ORIGINS": ORIGIN, "WEB_CONCURRENCY": "4"})
    shared = InMemorySingleUseStore(clock)  # thay cho bảng xt_identity.dpop_jti
    worker_a, worker_b = build_dpop_verifier(env, shared), build_dpop_verifier(env, shared)
    worker_a._clock = worker_b._clock = clock
    p = key.proof("POST", HTU, token=TOKEN)
    check(worker_a, p, key)
    with pytest.raises(DpopError):
        check(worker_b, p, key)


def test_default_replay_store_is_in_memory(clock: FakeClock) -> None:
    """Định lượng DPA-02: build_dpop_verifier() không nhận store chung theo mặc định."""
    from xt.adapters.identity import build_dpop_verifier

    dv = build_dpop_verifier({"XT_DPOP_ORIGINS": ORIGIN})
    assert isinstance(dv._replay, InMemorySingleUseStore)


# ------------------------------------------------------------------ alg confusion ----
def test_alg_none_rejected(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    p = _raw({"typ": "dpop+jwt", "alg": "none", "jwk": key.jwk}, _claims(clock))
    assert reason(dv, p, key) == "dpop_alg"


def test_hs256_with_public_key_as_secret_rejected(
    dv: DpopVerifier, key: DpopKey, clock: FakeClock
) -> None:
    import hashlib
    import hmac

    secret = json.dumps(key.jwk, separators=(",", ":")).encode()

    def hs(header: dict[str, Any]) -> str:
        unsigned = _raw(header, _claims(clock)).rsplit(".", 1)[0]
        return f"{unsigned}.{b64u(hmac.new(secret, unsigned.encode(), hashlib.sha256).digest())}"

    assert reason(dv, hs({"typ": "dpop+jwt", "alg": "HS256", "jwk": key.jwk}), key) == "dpop_alg"
    multi = verifier(clock, algorithms=frozenset({"ES256", "RS256"}))
    oct_jwk = {"kty": "oct", "k": b64u(secret)}
    assert (
        reason(multi, hs({"typ": "dpop+jwt", "alg": "HS256", "jwk": oct_jwk}), None) == "dpop_alg"
    )
    p = hs({"typ": "dpop+jwt", "alg": "ES256", "jwk": oct_jwk})
    assert reason(multi, p, None) == "dpop_jwk"  # thành viên riêng `k`


def test_rs256_header_with_ec_jwk_rejected(key: DpopKey, clock: FakeClock) -> None:
    multi = verifier(clock, algorithms=frozenset({"ES256", "RS256", "PS256", "ES384"}))
    for alg in ("RS256", "PS256", "ES384"):
        p = _raw({"typ": "dpop+jwt", "alg": alg, "jwk": key.jwk}, _claims(clock), b"x" * 64)
        assert reason(multi, p, key) == "dpop_jwk"


def test_es256_header_with_rsa_jwk_rejected(clock: FakeClock) -> None:
    multi = verifier(clock, algorithms=frozenset({"ES256", "RS256"}))
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = priv.public_key().public_numbers()
    rjwk = {
        "kty": "RSA",
        "n": b64u(n.n.to_bytes(256, "big")),
        "e": b64u(n.e.to_bytes(3, "big")),
    }
    p = jwt.encode(
        _claims(clock), priv, algorithm="RS256", headers={"typ": "dpop+jwt", "jwk": rjwk}
    )
    tampered = _raw({"typ": "dpop+jwt", "alg": "ES256", "jwk": rjwk}, _claims(clock))
    assert reason(multi, tampered, None) == "dpop_jwk"
    assert check(multi, p, None)  # RS256 thật (khi được cấu hình) vẫn hợp lệ


# ------------------------------------------------------------------------- jwk ----
def test_jwk_with_private_d_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    d = key.private.private_numbers().private_value
    j = {**key.jwk, "d": b64u(d.to_bytes(32, "big"))}
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, header={"jwk": j}), None) == "dpop_jwk"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda j: {**j, "x": j["x"] + "="},  # có đệm
        lambda j: {**j, "x": "AA" + j["x"]},  # sai độ dài
        lambda j: {**j, "crv": "P-384"},
        lambda j: {**j, "use": "enc"},
        lambda j: {k: v for k, v in j.items() if k != "y"},
    ],
)
def test_non_canonical_jwk_rejected(dv: DpopVerifier, key: DpopKey, mutate: Any) -> None:
    j = mutate(dict(key.jwk))
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, header={"jwk": j}), None) == "dpop_jwk"


def test_off_curve_point_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    j = {**key.jwk, "y": b64u((1).to_bytes(32, "big"))}
    r = reason(dv, key.proof("POST", HTU, token=TOKEN, header={"jwk": j}), None)
    assert r in ("dpop_malformed", "dpop_signature")


def test_embedded_jku_x5u_kid_x5c_ignored(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    """Chỉ `jwk` nhúng được dùng; không fetch jku/x5u; kid của chủ khóa không giúp kẻ trộm."""
    extra = {"jku": "http://127.0.0.1:1/jwks", "x5u": "http://127.0.0.1:1/c", "kid": key.jkt}
    assert check(dv, key.proof("POST", HTU, token=TOKEN, header=extra), key) == key.jkt
    thief = DpopKey(clock)
    p = thief.proof("POST", HTU, token=TOKEN, header={**extra, "x5c": ["AAAA"]})
    assert reason(dv, p, key) == "dpop_key_mismatch"


def test_jwk_swapped_after_signing_rejected(
    dv: DpopVerifier, key: DpopKey, clock: FakeClock
) -> None:
    thief = DpopKey(clock)
    p = thief.proof("POST", HTU, token=TOKEN)
    _, body, sig = p.split(".")
    h = b64u(json.dumps({"typ": "dpop+jwt", "alg": "ES256", "jwk": key.jwk}).encode())
    assert reason(dv, f"{h}.{body}.{sig}", key) == "dpop_signature"


def test_crit_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN, header={"crit": ["x-ext"], "x-ext": 1})
    assert reason(dv, p, key) == "dpop_crit"


def test_p384_key_with_es256_rejected(dv: DpopVerifier, clock: FakeClock) -> None:
    priv = ec.generate_private_key(ec.SECP384R1())
    n = priv.public_key().public_numbers()
    j = {
        "kty": "EC",
        "crv": "P-384",
        "x": b64u(n.x.to_bytes(48, "big")),
        "y": b64u(n.y.to_bytes(48, "big")),
    }
    p = _raw({"typ": "dpop+jwt", "alg": "ES256", "jwk": j}, _claims(clock), b"x" * 64)
    assert reason(dv, p, None) == "dpop_jwk"


# --------------------------------------------------------------------------- Desk ----
def test_desk_private_key_non_extractable() -> None:
    src = (ROOT / "desk/src/dpop.ts").read_text()
    assert 'generateKey({ name: "ECDSA", namedCurve: "P-256" }, false' in src
    assert (
        'exportKey("jwk", pair.publicKey)' in src
        and "privateKey)" not in src.split("exportKey")[1][:40]
    )


def test_desk_logout_rotates_dpop_key_and_ends_sso() -> None:
    dpop = (ROOT / "desk/src/dpop.ts").read_text()
    oidc = (ROOT / "desk/src/oidc.ts").read_text()
    logout = oidc.split("logout(): void {", 1)[1].split("\n  }", 1)[0]
    assert ".delete(" in dpop or "clear(" in dpop  # KeyStore có thao tác xóa khóa
    assert "end_session" in oidc or "revoke" in logout
