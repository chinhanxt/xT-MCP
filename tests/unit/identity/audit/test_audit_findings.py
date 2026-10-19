# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Test hồi quy cho lỗi tìm thấy trong audit danh tính (`docs/archive/audit-identity.md`).

Mỗi test đánh dấu `xfail(strict=True)`: đang lỗi thì xfail; sửa xong test sẽ XPASS và làm suite đỏ,
khi đó xóa marker để giữ làm test hồi quy.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from tests.unit.identity.factory import (
    ISSUER,
    NOW,
    FakeClock,
    FakeJwks,
    access_claims,
    b64,
    new_rsa,
    rs_settings,
    sign,
    su_settings,
)
from xt.adapters.identity import config as cfg
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.receipts import ReceiptService
from xt.adapters.identity.single_use import InMemorySingleUseStore
from xt.adapters.identity.step_up import ConfirmationContext, StepUpVerifier
from xt.domain.identity import (
    ApprovalDecision,
    ConfirmationBinding,
    InvalidTokenError,
    ReceiptRejectedError,
    StepUpEvidence,
    StepUpRejectedError,
    request_digest,
)

KEY = new_rsa("kid-audit")


def _verifier(clock: FakeClock) -> AccessTokenVerifier:
    cache = JwksCache(FakeJwks(keys=[KEY]), ttl_s=300, min_refetch_interval_s=10, clock=clock)
    return AccessTokenVerifier(rs_settings(), cache, clock=clock)


def _sign_raw_payload(payload: str) -> str:
    header = b64(json.dumps({"alg": "RS256", "kid": KEY.kid, "typ": "JWT"}).encode())
    signing_input = f"{header}.{b64(payload.encode())}"
    private = KEY.private
    assert isinstance(private, rsa.RSAPrivateKey)
    sig = private.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{b64(sig)}"


# A1 (Trung bình): nhiều biên nhận cho cùng một confirmation đều tiêu được.
def test_a1_only_one_receipt_per_confirmation_can_be_consumed() -> None:
    clock = FakeClock()
    svc = ReceiptService(
        issuer="xt",
        signing_key=ed25519.Ed25519PrivateKey.generate(),
        ttl_s=300,
        max_evidence_age_s=120,
        store=InMemorySingleUseStore(clock=clock),
        clock=clock,
    )
    ev = StepUpEvidence("boss", None, "xt-desk", NOW - 5, "urn:xt:acr:otp", ("otp",), None, "j1")
    binding = ConfirmationBinding("C-1", request_digest({"amount": 12_000_000}), "alice")
    approve = svc.issue(binding, ev, ApprovalDecision.APPROVE)
    reject = svc.issue(binding, ev, ApprovalDecision.REJECT)
    svc.verify_and_consume(reject, "C-1", binding.request_digest)
    with pytest.raises(ReceiptRejectedError):
        svc.verify_and_consume(approve, "C-1", binding.request_digest)


# A1b: cùng một ID token step-up dùng lại được nhiều lần cho cùng confirmation.
def test_a1b_step_up_id_token_single_use() -> None:
    clock = FakeClock()
    cache = JwksCache(FakeJwks(keys=[KEY]), ttl_s=300, min_refetch_interval_s=10, clock=clock)
    v = StepUpVerifier(su_settings(), cache, clock=clock)
    claims = {
        "iss": ISSUER, "sub": "boss", "aud": "xt-desk", "azp": "xt-desk", "typ": "ID",
        "iat": NOW - 5, "exp": NOW + 295, "auth_time": NOW - 5, "nonce": "N1",
        "acr": "urn:xt:acr:otp", "amr": ["pwd", "otp"], "jti": "idp-1",
    }  # fmt: skip
    ctx = ConfirmationContext("C-1", "N1", NOW - 60, "alice")
    tok = sign(KEY, claims)
    v.verify(tok, ctx)
    with pytest.raises(StepUpRejectedError):
        v.verify(tok, ctx)


# A2 (Thấp-Trung bình): repr(IntrospectionSettings) lộ client secret.
def test_a2_introspection_settings_repr_hides_secret() -> None:
    s = cfg.load_introspection_settings(
        {
            "XT_IDP_INTROSPECTION": "high_risk",
            "XT_IDP_INTROSPECTION_URL": "https://kc.test/introspect",
            "XT_IDP_INTROSPECTION_CLIENT_ID": "xt-introspect",
            "XT_IDP_INTROSPECTION_CLIENT_SECRET": "S3CR3T-VALUE",
        }
    )
    assert "S3CR3T-VALUE" not in repr(s)


# A3 (Thấp): lỗi cấu hình URL in nguyên mật khẩu trong userinfo.
def test_a3_config_error_redacts_url_credentials() -> None:
    with pytest.raises(cfg.IdentityConfigError) as e:
        cfg.load_introspection_settings(
            {
                "XT_IDP_INTROSPECTION": "high_risk",
                "XT_IDP_INTROSPECTION_URL": "http://u:PASSW0RD@kc.test/x",
                "XT_IDP_INTROSPECTION_CLIENT_ID": "c",
                "XT_IDP_INTROSPECTION_CLIENT_SECRET": "s",
            }
        )
    assert "PASSW0RD" not in str(e.value)


# A4 (Thấp): claim sai kiểu ngoài khối try của StepUpVerifier ném InvalidTokenError.
def test_a4_step_up_bad_claim_type_is_step_up_rejection() -> None:
    clock = FakeClock()
    cache = JwksCache(FakeJwks(keys=[KEY]), ttl_s=300, min_refetch_interval_s=10, clock=clock)
    v = StepUpVerifier(su_settings(), cache, clock=clock)
    claims = {
        "iss": ISSUER, "sub": "boss", "aud": "xt-desk", "azp": "xt-desk", "typ": "ID",
        "iat": NOW - 5, "exp": NOW + 295, "auth_time": NOW - 5, "nonce": "N1",
        "acr": ["urn:xt:acr:otp"], "amr": ["otp"],
    }  # fmt: skip
    with pytest.raises(StepUpRejectedError):
        v.verify(sign(KEY, claims), ConfirmationContext("C-1", "N1", NOW - 60, "alice"))


# A5 (Thấp): exp=Infinity (json.loads chấp nhận) gây OverflowError thay vì 401.
def test_a5_infinite_exp_is_invalid_token() -> None:
    body = json.dumps(access_claims()).replace(f'"exp": {NOW + 290}', '"exp": Infinity')
    with pytest.raises(InvalidTokenError):
        _verifier(FakeClock()).verify(_sign_raw_payload(body))


# A6 (Thấp): thông báo lỗi echo nguyên giá trị header chưa kiểm chữ ký (kid dài, xuống dòng).
def test_a6_error_detail_bounded_and_single_line() -> None:
    tok = sign(KEY, access_claims(), kid="X" * 10_000 + "\nFAKE LOG LINE")
    with pytest.raises(InvalidTokenError) as e:
        _verifier(FakeClock()).verify(tok)
    msg = str(e.value)
    assert len(msg) <= 256 and "\n" not in msg


# A7 (Thấp): không có trần kích thước token.
def test_a7_oversized_token_rejected() -> None:
    tok = sign(KEY, access_claims(pad="A" * 1_000_000))
    with pytest.raises(InvalidTokenError):
        _verifier(FakeClock()).verify(tok)


# A8 (Thấp-Trung bình): request_digest không phải JCS (khóa int trùng khóa str, Decimal lỗi).
def test_a8_digest_rejects_non_string_keys() -> None:
    with pytest.raises((TypeError, ValueError)):
        request_digest({1: "x"})  # type: ignore[dict-item]


def test_a8_digest_decimal_same_as_int() -> None:
    assert request_digest({"amount": Decimal("12000000")}) == request_digest({"amount": 12000000})


def test_a8_digest_number_rules_documented() -> None:
    """Quy tắc sau sửa (docs/identity.md): tiền là số nguyên VND; float bị từ chối thay vì cho
    digest khác (JCS coi 12000000 và 12000000.0 là một, nên phân biệt chúng là sai chuẩn)."""
    a = request_digest({"amount": 12000000})
    with pytest.raises(ValueError):
        request_digest({"amount": 12000000.0})
    with pytest.raises(ValueError):
        request_digest({"amount": Decimal("12000000.5")})
    assert a != request_digest({"amount": "12,000,000"})


def test_receipt_signature_s_plus_l_rejected() -> None:
    """Ed25519 malleability: S + L phải bị từ chối (cryptography/OpenSSL kiểm S < L)."""
    clock = FakeClock()
    svc = ReceiptService(
        issuer="xt",
        signing_key=ed25519.Ed25519PrivateKey.generate(),
        ttl_s=300,
        max_evidence_age_s=120,
        store=InMemorySingleUseStore(clock=clock),
        clock=clock,
    )
    ev = StepUpEvidence("boss", None, "xt-desk", NOW - 5, "urn:xt:acr:otp", ("otp",), None, None)
    binding = ConfirmationBinding("C-1", request_digest({"a": 1}), "alice")
    h, p, s = svc.issue(binding, ev, ApprovalDecision.APPROVE).split(".")
    sig = base64.urlsafe_b64decode(s + "==")
    order = 2**252 + 27742317777372353535851937790883648493
    big_s = int.from_bytes(sig[32:], "little") + order
    if big_s >= 2**256:
        pytest.skip("S + L tràn 256 bit với chữ ký này")
    forged = sig[:32] + big_s.to_bytes(32, "little")
    with pytest.raises(ReceiptRejectedError):
        svc.verify_and_consume(f"{h}.{p}.{b64(forged)}", "C-1", binding.request_digest)


# A9: refetch JWKS chạy ngoài lock; kid đã biết không phải chờ mạng.
def test_a9_known_kid_not_blocked_by_inflight_refetch() -> None:
    import threading

    clock = FakeClock()
    started, release = threading.Event(), threading.Event()
    src = FakeJwks(keys=[KEY])

    def slow() -> object:
        if src.calls >= 1:
            started.set()
            release.wait(5)
        return src()

    cache = JwksCache(slow, ttl_s=300, min_refetch_interval_s=10, clock=clock)  # type: ignore[arg-type]
    v = AccessTokenVerifier(rs_settings(), cache, clock=clock)
    v.verify(sign(KEY, access_claims()))
    clock.advance(10)
    t = threading.Thread(target=_swallow, args=(cache,))
    t.start()
    assert started.wait(5), "refetch phải đang chạy"
    v.verify(sign(KEY, access_claims()))  # không bị chặn bởi fetch đang treo
    release.set()
    t.join(5)
    assert not t.is_alive()


def _swallow(cache: JwksCache) -> None:
    with pytest.raises(InvalidTokenError):
        cache.get("kid-unknown")


def test_a9_min_refetch_lower_bound() -> None:
    env = {
        "XT_IDP_ISSUER": "https://sso.test/realms/xt",
        "XT_IDP_JWKS_URL": "https://sso.test/certs",
        "XT_IDP_JWKS_MIN_REFETCH_SECONDS": "0",
        "XT_MCP_RESOURCE": "https://xt.test/mcp",
        "XT_MCP_ALLOWED_CLIENTS": "c",
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "mcp-xt",
    }
    with pytest.raises(cfg.IdentityConfigError):
        cfg.load_resource_server_settings(env)
