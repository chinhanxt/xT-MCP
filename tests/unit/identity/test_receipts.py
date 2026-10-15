# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Biên nhận Ed25519 (spike-hitl C): replay, sửa, hết hạn, sai yêu cầu, SoD."""

from __future__ import annotations

import base64
import json
from dataclasses import replace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.unit.identity.factory import NOW, FakeClock, b64
from xt.adapters.identity.receipts import RECEIPT_TYP, ReceiptService
from xt.adapters.identity.single_use import InMemorySingleUseStore
from xt.domain.identity import (
    ApprovalDecision,
    ConfirmationBinding,
    ReceiptRejectedError,
    SeparationOfDutiesError,
    StepUpEvidence,
    StepUpRejectedError,
    request_digest,
)

R42 = {"id": "R-42", "requester": "user-bob", "amount": 12_000_000, "payee": "0001-bob"}
DIGEST = request_digest(R42)
BINDING = ConfirmationBinding("cf_42", DIGEST, "user-bob")
EVIDENCE = StepUpEvidence(
    subject="user-alice",
    username="alice",
    client_id="xt-desk",
    auth_time=NOW - 10,
    acr="urn:xt:acr:otp",
    amr=("pwd", "otp"),
    session_id="sess-1",
    token_id="idt-1",
)
KEY = Ed25519PrivateKey.generate()


@pytest.fixture
def store(clock: FakeClock) -> InMemorySingleUseStore:
    return InMemorySingleUseStore(clock)


@pytest.fixture
def svc(clock: FakeClock, store: InMemorySingleUseStore) -> ReceiptService:
    return ReceiptService(
        issuer="https://xt.example.test",
        signing_key=KEY,
        ttl_s=300,
        max_evidence_age_s=300,
        store=store,
        clock=clock,
    )


def rejected(fn: object, reason: str) -> None:
    assert callable(fn)
    with pytest.raises(ReceiptRejectedError) as e:
        fn()
    assert e.value.reason == reason, e.value


def test_issue_and_consume_once(svc: ReceiptService, store: InMemorySingleUseStore) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.APPROVE)
    hdr = jwt.get_unverified_header(rc)
    assert hdr == {"alg": "EdDSA", "typ": RECEIPT_TYP, "kid": svc.key_id}
    r = svc.verify_and_consume(rc, "cf_42", DIGEST)
    assert r.approver == "user-alice" and r.requester == "user-bob"
    assert r.decision is ApprovalDecision.APPROVE and r.confirmation_id == "cf_42"
    assert r.expires_at - r.issued_at == 300
    assert r.evidence["azp"] == "xt-desk" and r.evidence["amr"] == ["pwd", "otp"]
    assert len(store) == 1
    rejected(lambda: svc.verify_and_consume(rc, "cf_42", DIGEST), "confirmation_already_decided")


def test_reject_decision_is_bound_too(svc: ReceiptService) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.REJECT)
    assert svc.verify_and_consume(rc, "cf_42", DIGEST).decision is ApprovalDecision.REJECT


def test_wrong_confirmation(svc: ReceiptService) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.APPROVE)
    rejected(lambda: svc.verify_and_consume(rc, "cf_43", DIGEST), "wrong_confirmation")


@pytest.mark.parametrize(
    "changed",
    [dict(R42, amount=120_000_000), dict(R42, payee="9999-attacker"), dict(R42, id="R-43")],
)
def test_request_changed_after_approval(svc: ReceiptService, changed: dict[str, object]) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.APPROVE)
    rejected(
        lambda: svc.verify_and_consume(rc, "cf_42", request_digest(changed)), "digest_mismatch"
    )
    # Biên nhận sai yêu cầu không đốt jti: vẫn dùng được cho đúng yêu cầu.
    svc.verify_and_consume(rc, "cf_42", DIGEST)


def test_tampered_payload_keeps_old_signature(svc: ReceiptService) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.REJECT)
    h, p, s = rc.split(".")
    body = json.loads(base64.urlsafe_b64decode(p + "=="))
    body["decision"] = "approve"
    forged = f"{h}.{b64(json.dumps(body).encode())}.{s}"
    rejected(lambda: svc.verify_and_consume(forged, "cf_42", DIGEST), "bad_signature")


def test_expired(svc: ReceiptService, clock: FakeClock) -> None:
    rc = svc.issue(BINDING, EVIDENCE, ApprovalDecision.APPROVE)
    clock.advance(300)
    rejected(lambda: svc.verify_and_consume(rc, "cf_42", DIGEST), "expired")


def test_signed_by_other_key(svc: ReceiptService, clock: FakeClock) -> None:
    other = ReceiptService(
        issuer="https://xt.example.test",
        signing_key=Ed25519PrivateKey.generate(),
        ttl_s=300,
        max_evidence_age_s=300,
        store=InMemorySingleUseStore(clock),
        clock=clock,
    )
    rc = other.issue(BINDING, EVIDENCE, ApprovalDecision.APPROVE)
    rejected(lambda: svc.verify_and_consume(rc, "cf_42", DIGEST), "unknown_kid")
    h, p, s = rc.split(".")
    hdr = json.loads(base64.urlsafe_b64decode(h + "=="))
    hdr["kid"] = svc.key_id
    relabeled = f"{b64(json.dumps(hdr).encode())}.{p}.{s}"
    rejected(lambda: svc.verify_and_consume(relabeled, "cf_42", DIGEST), "bad_signature")


def _forge(svc: ReceiptService, claims: dict[str, object], **headers: object) -> str:
    hdr: dict[str, object] = {"typ": RECEIPT_TYP, "kid": svc.key_id}
    hdr.update(headers)
    return jwt.encode(claims, KEY, algorithm="EdDSA", headers=hdr)


def _claims(**over: object) -> dict[str, object]:
    c: dict[str, object] = {
        "iss": "https://xt.example.test",
        "jti": "j1",
        "iat": NOW,
        "exp": NOW + 300,
        "sub": "user-alice",
        "cid": "cf_42",
        "rdg": DIGEST,
        "req": "user-bob",
        "decision": "approve",
    }
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


@pytest.mark.parametrize(
    ("claims", "reason"),
    [
        (_claims(iss="https://evil.test"), "issuer"),
        (_claims(exp=None), "missing_claim"),
        (_claims(jti=None), "missing_claim"),
        (_claims(exp=NOW + 3600), "lifetime_invalid"),
        (_claims(iat=NOW + 10, exp=NOW + 200), "lifetime_invalid"),
        (_claims(sub="user-bob"), "approver_is_requester"),
        (_claims(req=None), "missing_claim"),
        (_claims(decision="maybe"), "bad_decision"),
    ],
)
def test_claim_rejections(svc: ReceiptService, claims: dict[str, object], reason: str) -> None:
    tok = _forge(svc, claims)
    rejected(lambda: svc.verify_and_consume(tok, "cf_42", DIGEST), reason)


def test_header_rejections(svc: ReceiptService) -> None:
    rejected(lambda: svc.verify_and_consume("garbage", "cf_42", DIGEST), "malformed")
    wrong_typ = _forge(svc, _claims(), typ="JWT")
    rejected(lambda: svc.verify_and_consume(wrong_typ, "cf_42", DIGEST), "receipt_typ")
    hs = jwt.encode(_claims(), "k" * 32, algorithm="HS256", headers={"typ": RECEIPT_TYP})
    rejected(lambda: svc.verify_and_consume(hs, "cf_42", DIGEST), "alg_not_allowed")
    h, p, _ = _forge(svc, _claims()).split(".")
    rejected(lambda: svc.verify_and_consume(f"{h}.{p}.@@", "cf_42", DIGEST), "malformed")


def test_issue_requires_fresh_evidence_and_sod(svc: ReceiptService) -> None:
    with pytest.raises(StepUpRejectedError) as e:
        svc.issue(BINDING, replace(EVIDENCE, auth_time=NOW - 301), ApprovalDecision.APPROVE)
    assert e.value.reason == "auth_stale"
    with pytest.raises(SeparationOfDutiesError):
        svc.issue(BINDING, replace(EVIDENCE, subject="user-bob"), ApprovalDecision.APPROVE)


def test_request_digest_canonical() -> None:
    a = {"b": 1, "a": "Đà Nẵng", "n": [1, 2]}
    b = {"n": [1, 2], "a": "Đà Nẵng", "b": 1}
    assert request_digest(a) == request_digest(b)
    assert request_digest(a) != request_digest({**a, "n": [2, 1]})
    with pytest.raises(ValueError):
        request_digest({"x": float("nan")})


def test_store_purges_expired(clock: FakeClock) -> None:
    st = InMemorySingleUseStore(clock)
    assert st.consume("a", NOW + 5) and not st.consume("a", NOW + 5)
    clock.advance(5)
    assert st.consume("b", NOW + 100)
    assert len(st) == 1
