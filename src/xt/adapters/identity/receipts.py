# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Biên nhận duyệt một lần, JWS EdDSA (Ed25519) (spike-hitl phương án C).

Gắn với `(confirmation_id, request_digest, approver, decision, exp)`. Chỉ phát sau khi đã có
`StepUpEvidence` hợp lệ. Lúc thực thi: tính lại digest từ PendingOperation hiện tại, kiểm khớp, rồi
tiêu `jti` đúng một lần qua `SingleUseStore` (prod: cùng transaction với hành động).
`request_digest` phải được chụp lúc tạo confirmation (đúng thứ hiển thị cho người duyệt).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from xt.adapters.identity.config import ReceiptSettings
from xt.adapters.identity.jwt_core import SIGNATURE_ONLY
from xt.application.ports import SingleUseStore
from xt.domain.identity import (
    ApprovalDecision,
    ApprovalReceipt,
    ConfirmationBinding,
    ReceiptRejectedError,
    SeparationOfDutiesError,
    StepUpEvidence,
    StepUpRejectedError,
)

RECEIPT_TYP = "xt-approval+jwt"
RECEIPT_ALG = "EdDSA"
MAX_RECEIPT_BYTES = 8_192


def ed25519_thumbprint(public_key: Ed25519PublicKey) -> str:
    """RFC 7638 thumbprint của JWK OKP/Ed25519, dùng làm `kid`."""
    x = base64.urlsafe_b64encode(public_key.public_bytes(Encoding.Raw, PublicFormat.Raw))
    canon = json.dumps(
        {"crv": "Ed25519", "kty": "OKP", "x": x.decode().rstrip("=")},
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(hashlib.sha256(canon.encode()).digest()).decode().rstrip("=")


class ReceiptService:
    def __init__(
        self,
        *,
        issuer: str,
        signing_key: Ed25519PrivateKey,
        ttl_s: int,
        max_evidence_age_s: int,
        store: SingleUseStore,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._iss = issuer
        self._key = signing_key
        self._pub = signing_key.public_key()
        self._kid = ed25519_thumbprint(self._pub)
        self._ttl = ttl_s
        self._max_evidence_age = max_evidence_age_s
        self._store = store
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: ReceiptSettings,
        store: SingleUseStore,
        max_evidence_age_s: int,
        clock: Callable[[], float] = time.time,
    ) -> ReceiptService:
        return cls(
            issuer=settings.issuer,
            signing_key=settings.signing_key,
            ttl_s=settings.ttl_s,
            max_evidence_age_s=max_evidence_age_s,
            store=store,
            clock=clock,
        )

    @property
    def key_id(self) -> str:
        return self._kid

    def issue(
        self,
        binding: ConfirmationBinding,
        evidence: StepUpEvidence,
        decision: ApprovalDecision,
    ) -> str:
        now = int(self._clock())
        if evidence.subject == binding.requester_subject:
            raise SeparationOfDutiesError("approver_is_requester")
        if now - evidence.auth_time > self._max_evidence_age:
            raise StepUpRejectedError("auth_stale", f"{now - evidence.auth_time}s")
        claims = {
            "iss": self._iss,
            "jti": uuid.uuid4().hex,  # định danh biên nhận để audit
            "iat": now,
            "exp": now + self._ttl,
            "sub": evidence.subject,
            "cid": binding.confirmation_id,
            "rdg": binding.request_digest,
            "req": binding.requester_subject,
            "decision": ApprovalDecision(decision).value,
            "evidence": {
                "azp": evidence.client_id,
                "acr": evidence.acr,
                "amr": list(evidence.amr),
                "auth_time": evidence.auth_time,
                "sid": evidence.session_id,
                "idp_jti": evidence.token_id,
            },
        }
        return jwt.encode(
            claims, self._key, algorithm=RECEIPT_ALG, headers={"typ": RECEIPT_TYP, "kid": self._kid}
        )

    def verify_and_consume(
        self, receipt: str, confirmation_id: str, current_request_digest: str
    ) -> ApprovalReceipt:
        """Kiểm chữ ký + ràng buộc rồi mới tiêu `cid:<confirmation_id>`: mỗi confirmation chỉ một
        quyết định (approve hoặc reject), dù phát được bao nhiêu biên nhận. Biên nhận sai không đốt
        khóa. Khóa giữ vĩnh viễn (prod: UNIQUE(confirmation_id) trong bảng decisions)."""
        if len(receipt) > MAX_RECEIPT_BYTES:
            raise ReceiptRejectedError("receipt_too_large")
        try:
            header = jwt.get_unverified_header(receipt)
        except jwt.PyJWTError as exc:
            raise ReceiptRejectedError("malformed", str(exc)) from exc
        if header.get("alg") != RECEIPT_ALG:
            raise ReceiptRejectedError("alg_not_allowed", repr(header.get("alg")))
        if header.get("typ") != RECEIPT_TYP:
            raise ReceiptRejectedError("receipt_typ", repr(header.get("typ")))
        if header.get("kid") != self._kid:
            raise ReceiptRejectedError("unknown_kid")
        try:
            c = jwt.decode(receipt, self._pub, algorithms=[RECEIPT_ALG], options=SIGNATURE_ONLY)
        except jwt.InvalidSignatureError as exc:
            raise ReceiptRejectedError("bad_signature") from exc
        except jwt.PyJWTError as exc:
            raise ReceiptRejectedError("malformed", str(exc)) from exc
        now = self._clock()
        if c.get("iss") != self._iss:
            raise ReceiptRejectedError("issuer")
        exp, iat, jti = c.get("exp"), c.get("iat"), c.get("jti")
        if not isinstance(exp, int) or not isinstance(iat, int) or not isinstance(jti, str):
            raise ReceiptRejectedError("missing_claim")
        if now >= exp:
            raise ReceiptRejectedError("expired")
        if iat > now or exp - iat > self._ttl:
            raise ReceiptRejectedError("lifetime_invalid")
        if c.get("cid") != confirmation_id:
            raise ReceiptRejectedError("wrong_confirmation")
        rdg = c.get("rdg")
        if not isinstance(rdg, str) or not hmac.compare_digest(
            rdg.encode(), current_request_digest.encode()
        ):
            raise ReceiptRejectedError("digest_mismatch")
        sub, req = c.get("sub"), c.get("req")
        if not isinstance(sub, str) or not isinstance(req, str) or not sub:
            raise ReceiptRejectedError("missing_claim")
        if sub == req:
            raise ReceiptRejectedError("approver_is_requester")
        try:
            decision = ApprovalDecision(str(c.get("decision")))
        except ValueError as exc:
            raise ReceiptRejectedError("bad_decision") from exc
        if not self._store.consume(f"cid:{confirmation_id}", None):
            raise ReceiptRejectedError("confirmation_already_decided")
        evidence = c.get("evidence")
        return ApprovalReceipt(
            receipt_id=jti,
            issuer=self._iss,
            confirmation_id=confirmation_id,
            request_digest=rdg,
            approver=sub,
            requester=req,
            decision=decision,
            issued_at=iat,
            expires_at=exp,
            evidence=evidence if isinstance(evidence, dict) else {},
        )
