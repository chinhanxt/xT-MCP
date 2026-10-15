# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Step-up `/desk/confirm` (spike-hitl B): token Desk/agent không thay được ID token confirm."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.unit.identity.factory import ISSUER, NOW, FakeClock, SigningKey, sign, su_settings
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.step_up import (
    ConfirmationContext,
    StepUpVerifier,
    confirm_authorization_url,
    new_nonce,
    new_pkce_pair,
)
from xt.domain.identity import SeparationOfDutiesError, StepUpRejectedError

CTX = ConfirmationContext(
    confirmation_id="cf_42",
    nonce="nonce-cf42",
    created_at=NOW - 60,
    requester_subject="user-bob",
    confirmer_subject="user-alice",
)


def id_claims(**over: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "user-alice",
        "aud": "xt-desk",
        "azp": "xt-desk",
        "typ": "ID",
        "iat": NOW - 5,
        "exp": NOW + 295,
        "auth_time": NOW - 5,
        "nonce": "nonce-cf42",
        "acr": "urn:xt:acr:otp",
        "amr": ["pwd", "otp"],
        "sid": "sess-1",
        "jti": "idt-1",
        "preferred_username": "alice",
    }
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


@pytest.fixture
def verifier(jwks: JwksCache, clock: FakeClock) -> StepUpVerifier:
    return StepUpVerifier(su_settings(), jwks, clock)


def reject(v: StepUpVerifier, token: str, reason: str, ctx: ConfirmationContext = CTX) -> None:
    with pytest.raises(StepUpRejectedError) as e:
        v.verify(token, ctx)
    assert e.value.reason == reason, e.value


def test_fresh_otp_evidence_accepted(verifier: StepUpVerifier, rsa_key: SigningKey) -> None:
    ev = verifier.verify(sign(rsa_key, id_claims()), CTX)
    assert ev.subject == "user-alice" and ev.client_id == "xt-desk"
    assert ev.acr == "urn:xt:acr:otp" and ev.amr == ("pwd", "otp")
    assert ev.auth_time == NOW - 5 and ev.session_id == "sess-1" and ev.token_id == "idt-1"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        # B3: token im lặng của client khác (aud/azp khác), dù nonce chép được và amr có otp.
        ({"aud": "xt-desk-agent", "azp": "xt-desk-agent", "acr": "0"}, "audience"),
        ({"azp": "claude-connector"}, "wrong_client"),
        ({"azp": None}, "missing_claim:azp"),
        ({"acr": "0"}, "acr_insufficient"),
        ({"acr": "urn:xt:acr:pwd"}, "acr_insufficient"),
        ({"acr": None}, "acr_insufficient"),
        ({"amr": ["pwd"]}, "amr_missing"),
        ({"auth_time": NOW - 200}, "auth_before_confirmation"),
        ({"auth_time": None}, "missing_claim:auth_time"),
        ({"auth_time": NOW + 120, "iat": NOW, "exp": NOW + 300}, "auth_time_in_future"),
        ({"nonce": "nonce-other"}, "nonce_mismatch"),
        ({"nonce": None}, "missing_claim:nonce"),
        ({"typ": "Bearer"}, "token_type"),
        ({"sub": "user-carol"}, "wrong_approver"),
        ({"exp": NOW - 31}, "expired"),
        ({"iss": "https://evil.test/realms/xt"}, "issuer"),
    ],
)
def test_rejections(
    verifier: StepUpVerifier, rsa_key: SigningKey, over: dict[str, Any], reason: str
) -> None:
    reject(verifier, sign(rsa_key, id_claims(**over)), reason)


def test_auth_time_freshness_boundary(
    verifier: StepUpVerifier, rsa_key: SigningKey, clock: FakeClock
) -> None:
    ctx = ConfirmationContext("cf_1", "nonce-cf42", NOW - 400, "user-bob")
    verifier.verify(sign(rsa_key, id_claims(auth_time=NOW - 300)), ctx)
    reject(verifier, sign(rsa_key, id_claims(auth_time=NOW - 301)), "auth_stale", ctx)


def test_id_token_single_use(verifier: StepUpVerifier, rsa_key: SigningKey) -> None:
    tok = sign(rsa_key, id_claims())
    verifier.verify(tok, CTX)
    reject(verifier, tok, "id_token_replayed")
    reject(verifier, sign(rsa_key, id_claims(jti=None)), "missing_claim:jti")


def test_bad_claim_types_are_step_up_rejections(
    verifier: StepUpVerifier, rsa_key: SigningKey
) -> None:
    reject(verifier, sign(rsa_key, id_claims(amr="otp ")), "amr_missing")
    reject(verifier, sign(rsa_key, id_claims(sid=5)), "bad_claim:sid")


def test_requester_cannot_approve(verifier: StepUpVerifier, rsa_key: SigningKey) -> None:
    ctx = ConfirmationContext("cf_1", "nonce-cf42", NOW - 60, "user-alice")
    with pytest.raises(SeparationOfDutiesError):
        verifier.verify(sign(rsa_key, id_claims()), ctx)


def test_bad_signature_mapped(verifier: StepUpVerifier, rsa_key: SigningKey) -> None:
    reject(verifier, "x.y.z", "malformed")


def test_amr_not_required_when_unset(
    jwks: JwksCache, clock: FakeClock, rsa_key: SigningKey
) -> None:
    v = StepUpVerifier(su_settings(required_amr=frozenset()), jwks, clock)
    v.verify(sign(rsa_key, id_claims(amr=None)), CTX)


def test_authorization_url_and_pkce() -> None:
    pair = new_pkce_pair()
    expect = base64.urlsafe_b64encode(hashlib.sha256(pair.verifier.encode()).digest())
    assert pair.challenge == expect.decode().rstrip("=")
    nonce = new_nonce()
    url = confirm_authorization_url(
        su_settings(),
        "https://sso.example.test/realms/xt/protocol/openid-connect/auth",
        "https://xt.example.test/desk/confirm/cb",
        state="st",
        nonce=nonce,
        code_challenge=pair.challenge,
    )
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert q["client_id"] == "xt-desk" and q["code_challenge_method"] == "S256"
    assert q["prompt"] == "login" and q["max_age"] == "0" and q["acr_values"] == "urn:xt:acr:otp"
    assert q["nonce"] == nonce and q["code_challenge"] == pair.challenge
