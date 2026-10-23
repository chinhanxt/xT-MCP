# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm bằng chứng step-up cho `/desk/confirm/{id}` (spike-hitl phương án B).

Bằng chứng là **ID token** của client confirm sau luồng auth-code + PKCE S256 với `prompt=login`,
`max_age=0`, `acr_values` (Keycloak ép phía server bằng `minimum.acr.value` + flow LoA). Kiểm:
chữ ký/iss/exp như access token, `aud` và `azp` == client confirm, `typ=ID`, `nonce` confirmation,
`acr` == mức OTP cấu hình, `auth_time` >= lúc tạo confirmation và còn trong cửa sổ tươi, `amr` (nếu
cấu hình) và đúng người duyệt, người duyệt khác người đề nghị. Không bao giờ chỉ dựa vào `amr` (B3).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode

from xt.adapters.identity.config import StepUpSettings
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.jwt_core import (
    check_standard_claims,
    numeric,
    str_list,
    string,
    verify_signed_jwt,
)
from xt.adapters.identity.single_use import InMemorySingleUseStore
from xt.application.ports import SingleUseStore
from xt.domain.identity import (
    InvalidTokenError,
    SeparationOfDutiesError,
    StepUpEvidence,
    StepUpRejectedError,
)

ID_TOKEN_TYP = "ID"


@dataclass(frozen=True, slots=True)
class ConfirmationContext:
    confirmation_id: str
    nonce: str
    created_at: int
    requester_subject: str
    confirmer_subject: str | None = None


@dataclass(frozen=True, slots=True)
class PkcePair:
    verifier: str
    challenge: str


def new_pkce_pair() -> PkcePair:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return PkcePair(verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="))


def new_nonce() -> str:
    return secrets.token_urlsafe(24)


def confirm_authorization_url(
    settings: StepUpSettings,
    authorization_endpoint: str,
    redirect_uri: str,
    *,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """URL authorize cho trang confirm. Tham số chỉ là gợi ý; Keycloak phải ép phía server (B2)."""
    q = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "max_age": "0",
        "acr_values": settings.effective_acr(),
    }
    return f"{authorization_endpoint}?{urlencode(q)}"


class StepUpVerifier:
    def __init__(
        self,
        settings: StepUpSettings,
        jwks: JwksCache,
        clock: Callable[[], float] = time.time,
        store: SingleUseStore | None = None,
    ) -> None:
        """`store` tiêu `jti` của ID token (mỗi lần đăng nhập step-up chỉ dùng được một lần).
        Mặc định in-memory (một tiến trình); nhiều tiến trình thì truyền bản Postgres."""
        self._s = settings
        self._jwks = jwks
        self._clock = clock
        self._store: SingleUseStore = store if store is not None else InMemorySingleUseStore(clock)

    def verify(self, id_token: str, ctx: ConfirmationContext) -> StepUpEvidence:
        try:
            return self._verify(id_token, ctx)
        except StepUpRejectedError:
            raise
        except InvalidTokenError as exc:
            raise StepUpRejectedError(exc.reason, exc.detail) from exc

    def _verify(self, id_token: str, ctx: ConfirmationContext) -> StepUpEvidence:
        s = self._s
        now = self._clock()
        want_acr, want_amr = s.effective()  # một lần/request: acr và amr cùng một chế độ
        _, claims = verify_signed_jwt(id_token, self._jwks, s.idp.algorithms, s.idp.max_token_bytes)
        _, exp = check_standard_claims(
            claims,
            issuer=s.idp.issuer,
            audience=s.client_id,
            token_type=ID_TOKEN_TYP,
            now=now,
            leeway_s=s.idp.leeway_s,
            max_lifetime_s=s.idp.max_token_lifetime_s,
        )
        azp = string(claims, "azp", required=True)
        auth_time = numeric(claims, "auth_time", required=True)
        sub = string(claims, "sub", required=True)
        nonce = string(claims, "nonce", required=True)
        jti = string(claims, "jti", required=True)
        acr = string(claims, "acr", required=False)
        amr = str_list(claims, "amr")
        username = string(claims, "preferred_username", required=False)
        sid = string(claims, "sid", required=False)
        assert auth_time is not None and sub is not None and nonce is not None and jti
        if azp != s.client_id:
            raise StepUpRejectedError("wrong_client", repr(azp))
        if not hmac.compare_digest(nonce.encode(), ctx.nonce.encode()):
            raise StepUpRejectedError("nonce_mismatch")
        if acr != want_acr:
            raise StepUpRejectedError("acr_insufficient", repr(acr))
        if auth_time + s.idp.leeway_s < ctx.created_at:
            raise StepUpRejectedError("auth_before_confirmation")
        if now - auth_time > s.max_auth_age_s:
            age = int(now - auth_time)
            raise StepUpRejectedError("auth_stale", f"{age}s > {s.max_auth_age_s}s")
        if auth_time > now + s.idp.leeway_s:
            raise StepUpRejectedError("auth_time_in_future")
        if not want_amr <= set(amr):
            raise StepUpRejectedError("amr_missing", f"{sorted(want_amr - set(amr))}")
        if ctx.confirmer_subject is not None and sub != ctx.confirmer_subject:
            raise StepUpRejectedError("wrong_approver")
        if sub == ctx.requester_subject:
            raise SeparationOfDutiesError("approver_is_requester")
        # Tiêu sau cùng: token bị loại vì lý do khác không bị đốt.
        if not self._store.consume(f"idt:{s.idp.issuer}:{jti}", exp + s.idp.leeway_s):
            raise StepUpRejectedError("id_token_replayed")
        return StepUpEvidence(
            subject=sub,
            username=username,
            client_id=s.client_id,
            auth_time=auth_time,
            acr=want_acr,
            amr=amr,
            session_id=sid,
            token_id=jti,
        )
