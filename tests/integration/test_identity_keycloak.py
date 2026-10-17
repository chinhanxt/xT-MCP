# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lớp danh tính chạy với Keycloak 26.6.3 thật (cổng ngẫu nhiên). Chạy: `make test-integration`."""

from __future__ import annotations

import html
import http.cookiejar
import re
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration.keycloak import (
    ACR_OTP,
    CONFIRM_CB,
    RESOURCE,
    TOTP_RAW,
    Admin,
    Realm,
    totp,
)
from xt.adapters.identity import (
    AccessTokenVerifier,
    InMemorySingleUseStore,
    ReceiptService,
    StepUpVerifier,
    build_jwks_cache,
    build_protected_resource_metadata,
    build_revocation_guard,
    load_introspection_settings,
    load_resource_server_settings,
    load_step_up_settings,
)
from xt.adapters.identity.step_up import (
    ConfirmationContext,
    confirm_authorization_url,
    new_nonce,
    new_pkce_pair,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import (
    ApprovalDecision,
    ConfirmationBinding,
    InsufficientScopeError,
    InvalidTokenError,
    PrincipalKind,
    ReceiptRejectedError,
    SeparationOfDutiesError,
    StepUpRejectedError,
    TokenRevokedError,
    request_digest,
)

pytestmark = pytest.mark.integration


def verifier(realm: Realm, **env: str) -> AccessTokenVerifier:
    s = load_resource_server_settings(realm.env(**env))
    return AccessTokenVerifier(s, build_jwks_cache(s.idp))


def reject(v: AccessTokenVerifier, token: str, reason: str) -> None:
    with pytest.raises(InvalidTokenError) as e:
        v.verify(token)
    assert e.value.reason == reason, e.value


# ---------------------------------------------------------------- access token / resource server


def test_discovery_chain(realm: Realm) -> None:
    prm = build_protected_resource_metadata(load_resource_server_settings(realm.env()))
    assert prm["resource"] == RESOURCE
    md = httpx.get(f"{prm['authorization_servers'][0]}/.well-known/openid-configuration").json()
    assert md["issuer"] == realm.issuer
    assert "S256" in md["code_challenge_methods_supported"]


def test_allowlisted_client_user_token_accepted(realm: Realm) -> None:
    tok = realm.password_token("it-agent", "bob")
    p = verifier(realm).verify(tok["access_token"])
    assert p.subject == realm.users["bob"] and p.username == "bob"
    assert p.kind is PrincipalKind.AGENT and p.client_id == "it-agent"
    assert p.on_behalf_of == realm.users["bob"]
    assert "mcp-xt" in p.scopes and p.issuer == realm.issuer
    assert p.expires_at - p.issued_at == 300


def test_service_account_token_is_agent(realm: Realm) -> None:
    tok = realm.token("it-agent", grant_type="client_credentials")
    p = verifier(realm).verify(tok["access_token"])
    assert p.kind is PrincipalKind.AGENT and p.client_id == "it-agent" and p.on_behalf_of is None


def test_desk_client_token_is_human(realm: Realm) -> None:
    v = verifier(realm, XT_MCP_HUMAN_CLIENTS="it-rogue")
    p = v.verify(realm.password_token("it-rogue", "bob")["access_token"])
    assert p.kind is PrincipalKind.HUMAN and p.on_behalf_of is None


def test_rejections_on_real_tokens(realm: Realm) -> None:
    v = verifier(realm)
    # VS #1: client khác cùng realm có đúng audience MCP nhưng không trong allowlist.
    reject(v, realm.password_token("it-rogue", "bob")["access_token"], "client_not_allowed")
    # mcp-xt không phải realm default: client nội bộ khác không nhận aud MCP.
    reject(v, realm.password_token("it-plain", "bob")["access_token"], "audience")
    tok = realm.password_token("it-agent", "bob")
    with pytest.raises(InvalidTokenError) as e:
        v.verify(tok["refresh_token"])  # refresh token Keycloak ký HS512
    assert e.value.reason in ("alg_not_allowed", "token_type")
    reject(v, tok["id_token"], "token_type")
    h, p, s = tok["access_token"].split(".")
    reject(v, f"{h}.{p[:-4]}AAAA.{s}", "bad_signature")
    with pytest.raises(InsufficientScopeError):
        v.verify(tok["access_token"], required_scopes=frozenset({"xt:admin"}))
    capped = verifier(realm, XT_IDP_MAX_TOKEN_LIFETIME_SECONDS="60")
    reject(capped, tok["access_token"], "lifetime_exceeded")
    other_iss = verifier(realm, XT_IDP_ISSUER=realm.issuer + "/")
    reject(other_iss, tok["access_token"], "issuer")


def test_master_realm_token_rejected(realm: Realm, kc_admin: Admin) -> None:
    master = kc_admin.c.headers["Authorization"].removeprefix("Bearer ")
    reject(verifier(realm), master, "unknown_kid")


def test_expired_token_rejected(realm: Realm) -> None:
    v = verifier(realm, XT_IDP_LEEWAY_SECONDS="0")
    tok = realm.password_token("it-short", "bob")["access_token"]
    v.verify(tok)
    time.sleep(3.5)
    reject(v, tok, "expired")


def test_introspection_sees_disabled_user(realm: Realm, kc_admin: Admin) -> None:
    v = verifier(realm)
    tok = realm.password_token("it-agent", "bob")["access_token"]
    p = v.verify(tok)
    guard = build_revocation_guard(
        load_introspection_settings(
            realm.env(
                XT_IDP_INTROSPECTION="high_risk",
                XT_IDP_INTROSPECTION_URL=f"{realm.oidc}/token/introspect",
                XT_IDP_INTROSPECTION_CLIENT_ID="it-rs",
                XT_IDP_INTROSPECTION_CLIENT_SECRET=realm.secrets["it-rs"],
                XT_IDP_INTROSPECTION_CACHE_SECONDS="0",
            )
        )
    )
    guard.ensure_active(p, tok, RiskLevel.L3)
    kc_admin.set_user_enabled(realm.name, realm.users["bob"], False)
    try:
        v.verify(tok)  # JWT vẫn hợp lệ về chữ ký: lý do cần introspection
        guard.ensure_active(p, tok, RiskLevel.L1)  # L1 không hỏi IdP
        with pytest.raises(TokenRevokedError):
            guard.ensure_active(p, tok, RiskLevel.L3)
    finally:
        kc_admin.set_user_enabled(realm.name, realm.users["bob"], True)


def test_key_rotation_and_deleted_key(kc_admin: Admin) -> None:
    rot = kc_admin.create_realm("xt-it-rot", stepup=False)
    v = verifier(rot, XT_IDP_JWKS_TTL_SECONDS="2", XT_IDP_JWKS_MIN_REFETCH_SECONDS="1")
    old = rot.password_token("it-agent", "bob")["access_token"]
    v.verify(old)
    kc_admin.rotate_rsa_key(rot.name)
    new = rot.password_token("it-agent", "bob")["access_token"]
    time.sleep(1.1)  # khoảng refetch tối thiểu (>= 1 s) kể từ lần tải JWKS đầu
    assert new.split(".")[0] != old.split(".")[0], "token mới phải mang kid mới"
    v.verify(new)  # kid lạ -> refetch -> nhận ngay
    time.sleep(2.5)
    reject(v, old, "unknown_kid")  # khóa đã xóa khỏi Keycloak: hết TTL thì không còn tin
    v.verify(new)


# ---------------------------------------------------------------- step-up + biên nhận


class Browser:
    """httpx có cookie jar, headless. Keycloak đặt cookie Secure trên http://localhost."""

    def __init__(self) -> None:
        self.c = httpx.Client(timeout=30, follow_redirects=False)

    def follow(self, r: httpx.Response) -> tuple[str, str]:
        while r.status_code in (302, 303):
            loc = r.headers["location"]
            if loc.startswith(CONFIRM_CB):
                return "redirect", loc
            r = self.c.get(loc)
        return "page", r.text

    @staticmethod
    def form(page: str, fid: str) -> str:
        m = re.search(rf'<form[^>]*id="{fid}"[^>]*action="([^"]+)"', page) or re.search(
            rf'<form[^>]*action="([^"]+)"[^>]*id="{fid}"', page
        )
        assert m, f"không thấy form {fid}"
        return html.unescape(m.group(1))

    def login(self, page: str, user: str) -> tuple[str, str]:
        action = self.form(page, "kc-form-login")
        data = {"username": user, "password": f"{user}-pw", "credentialId": ""}
        return self.follow(self.c.post(action, data=data))

    def otp(self, page: str, code: str) -> tuple[str, str]:
        return self.follow(self.c.post(self.form(page, "kc-otp-login-form"), data={"otp": code}))


@pytest.fixture
def browser(monkeypatch: pytest.MonkeyPatch) -> Browser:
    monkeypatch.setattr(
        http.cookiejar.DefaultCookiePolicy, "return_ok_secure", lambda *_: True, raising=True
    )
    return Browser()


def _confirm_url(realm: Realm, nonce: str, challenge: str) -> str:
    s = load_step_up_settings(realm.env())
    return confirm_authorization_url(
        s,
        f"{realm.oidc}/auth",
        CONFIRM_CB,
        state=secrets.token_urlsafe(8),
        nonce=nonce,
        code_challenge=challenge,
    )


def test_step_up_confirm_and_receipt(realm: Realm, browser: Browser) -> None:
    settings = load_step_up_settings(realm.env())
    stepup = StepUpVerifier(settings, build_jwks_cache(settings.idp))
    request = {"tool": "decide_request", "ref": "R-42", "amount": 12_000_000, "by": "bob"}
    binding = ConfirmationBinding("cf_42", request_digest(request), realm.users["bob"])
    ctx = ConfirmationContext(
        confirmation_id="cf_42",
        nonce=new_nonce(),
        created_at=int(time.time()),
        requester_subject=realm.users["bob"],
        confirmer_subject=realm.users["alice"],
    )

    # Agent lái trình duyệt: biết mật khẩu, không có OTP -> không bao giờ ra code.
    pair = new_pkce_pair()
    kind, page = browser.follow(browser.c.get(_confirm_url(realm, ctx.nonce, pair.challenge)))
    assert kind == "page" and 'id="kc-form-login"' in page
    kind, page = browser.login(page, "alice")
    assert kind == "page" and 'id="kc-otp-login-form"' in page
    kind, page = browser.otp(page, "000000" if totp(TOTP_RAW) != "000000" else "111111")
    assert kind == "page" and 'id="kc-otp-login-form"' in page

    # Người thật nhập OTP trên điện thoại.
    kind, loc = browser.otp(page, totp(TOTP_RAW))
    assert kind == "redirect", loc[:300]
    code = parse_qs(urlsplit(loc).query)["code"][0]
    no_pkce = httpx.post(
        f"{realm.oidc}/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "it-confirm",
            "redirect_uri": CONFIRM_CB,
            "code": code,
        },
    )
    assert no_pkce.status_code == 400, "đổi code thiếu code_verifier phải bị Keycloak từ chối"
    # Keycloak đốt code sau lần thử sai: người duyệt làm lại với PKCE mới (spike-hitl B4).
    pair = new_pkce_pair()
    kind, page = browser.follow(browser.c.get(_confirm_url(realm, ctx.nonce, pair.challenge)))
    kind, page = browser.login(page, "alice")
    assert 'id="kc-otp-login-form"' in page
    code_now = totp(TOTP_RAW)
    kind, loc = browser.otp(page, code_now)
    if kind == "page":  # Keycloak chặn tái dùng mã cùng cửa sổ 30 s: chờ mã kế tiếp
        time.sleep(31 - time.time() % 30)
        kind, loc = browser.otp(loc, totp(TOTP_RAW))
    assert kind == "redirect"
    code = parse_qs(urlsplit(loc).query)["code"][0]
    tok = realm.token(
        "it-confirm",
        grant_type="authorization_code",
        redirect_uri=CONFIRM_CB,
        code=code,
        code_verifier=pair.verifier,
    )
    evidence = stepup.verify(tok["id_token"], ctx)
    assert evidence.subject == realm.users["alice"] and evidence.acr == ACR_OTP
    assert "otp" in evidence.amr and evidence.client_id == "it-confirm"
    with pytest.raises(StepUpRejectedError) as e:
        stepup.verify(tok["id_token"], ctx)  # ID token step-up chỉ dùng một lần
    assert e.value.reason == "id_token_replayed"

    # Sai ngữ cảnh: confirmation khác (nonce khác), người đề nghị tự duyệt, token client khác.
    with pytest.raises(StepUpRejectedError) as e:
        other = ConfirmationContext("cf_43", new_nonce(), ctx.created_at, "x")
        stepup.verify(tok["id_token"], other)
    assert e.value.reason == "nonce_mismatch"
    with pytest.raises(SeparationOfDutiesError):
        stepup.verify(
            tok["id_token"],
            ConfirmationContext("cf_42", ctx.nonce, ctx.created_at, realm.users["alice"]),
        )
    agent_id = realm.password_token("it-agent", "bob")["id_token"]
    with pytest.raises(StepUpRejectedError) as e:
        stepup.verify(agent_id, ctx)
    assert e.value.reason == "audience"
    with pytest.raises(StepUpRejectedError) as e:
        stepup.verify(tok["access_token"], ctx)  # access token không phải ID token
    assert e.value.reason in ("token_type", "audience")

    # Biên nhận Ed25519 dùng một lần.
    receipts = ReceiptService(
        issuer=RESOURCE,
        signing_key=Ed25519PrivateKey.generate(),
        ttl_s=300,
        max_evidence_age_s=settings.max_auth_age_s,
        store=InMemorySingleUseStore(),
    )
    rc = receipts.issue(binding, evidence, ApprovalDecision.APPROVE)
    with pytest.raises(ReceiptRejectedError):
        receipts.verify_and_consume(rc, "cf_42", request_digest({**request, "amount": 99}))
    ok = receipts.verify_and_consume(rc, "cf_42", binding.request_digest)
    assert ok.approver == realm.users["alice"] and ok.evidence["acr"] == ACR_OTP
    with pytest.raises(ReceiptRejectedError) as r:
        receipts.verify_and_consume(rc, "cf_42", binding.request_digest)
    assert r.value.reason == "confirmation_already_decided"
    second = receipts.issue(binding, evidence, ApprovalDecision.REJECT)
    with pytest.raises(ReceiptRejectedError) as r:
        receipts.verify_and_consume(second, "cf_42", binding.request_digest)
    assert r.value.reason == "confirmation_already_decided"
