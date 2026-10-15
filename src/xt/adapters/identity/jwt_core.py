# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lõi kiểm JWT dùng chung: header, chữ ký qua JWKS, và các claim thời gian/issuer.

PyJWT chỉ dùng để kiểm chữ ký với đúng một thuật toán đã chốt từ header (đã lọc allowlist). Mọi
claim được kiểm ở đây với đồng hồ tiêm vào, để test xác định và không phụ thuộc mặc định của PyJWT.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import jwt
from jwt.types import Options

from xt.adapters.identity.jwks import JwksCache
from xt.domain.identity import InvalidTokenError, safe_detail

ACCEPTED_HEADER_TYP = frozenset({"jwt", "at+jwt"})

SIGNATURE_ONLY: Options = {
    "verify_signature": True,
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
    "verify_aud": False,
    "verify_iss": False,
    "verify_sub": False,
    "verify_jti": False,
}


def verify_signed_jwt(
    token: str, jwks: JwksCache, algorithms: frozenset[str], max_bytes: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Trả (header, claims) sau khi kiểm chữ ký. Không kiểm claim."""
    if isinstance(token, str) and len(token) > max_bytes:
        raise InvalidTokenError("token_too_large", f"{len(token)} > {max_bytes}")
    if not isinstance(token, str) or token.count(".") != 2:
        raise InvalidTokenError("malformed", "không phải JWS compact 3 phần")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("malformed", str(exc)) from exc
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in algorithms:
        raise InvalidTokenError("alg_not_allowed", safe_detail(alg))
    typ = header.get("typ")
    if typ is not None and (not isinstance(typ, str) or typ.lower() not in ACCEPTED_HEADER_TYP):
        raise InvalidTokenError("header_typ", safe_detail(typ))
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise InvalidTokenError("missing_kid")
    key = jwks.get(kid)
    if not key.compatible_with(alg):
        raise InvalidTokenError("key_alg_mismatch", f"kid={kid} alg={alg}")
    try:
        claims = jwt.decode(token, key.key, algorithms=[alg], options=SIGNATURE_ONLY)
    except jwt.InvalidSignatureError as exc:
        raise InvalidTokenError("bad_signature") from exc
    except jwt.PyJWTError as exc:
        raise InvalidTokenError("malformed", str(exc)) from exc
    return header, claims


def numeric(claims: Mapping[str, Any], name: str, *, required: bool) -> int | None:
    v = claims.get(name)
    if v is None:
        if required:
            raise InvalidTokenError(f"missing_claim:{name}")
        return None
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise InvalidTokenError(f"bad_claim:{name}")
    if isinstance(v, float) and not math.isfinite(v):
        raise InvalidTokenError(f"bad_claim:{name}")
    return int(v)


def string(claims: Mapping[str, Any], name: str, *, required: bool) -> str | None:
    v = claims.get(name)
    if v is None or v == "":
        if required:
            raise InvalidTokenError(f"missing_claim:{name}")
        return None
    if not isinstance(v, str):
        raise InvalidTokenError(f"bad_claim:{name}")
    return v


def str_list(claims: Mapping[str, Any], name: str) -> tuple[str, ...]:
    v = claims.get(name)
    if v is None:
        return ()
    if isinstance(v, str):
        return (v,)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return tuple(v)
    raise InvalidTokenError(f"bad_claim:{name}")


def check_standard_claims(
    claims: Mapping[str, Any],
    *,
    issuer: str,
    audience: str,
    token_type: str,
    now: float,
    leeway_s: int,
    max_lifetime_s: int,
) -> tuple[int, int]:
    """iss chính xác, aud chứa `audience`, typ (claim Keycloak), exp/iat bắt buộc, nbf nếu có,
    trần `exp - iat`, sub bắt buộc. Trả (iat, exp)."""
    if claims.get("iss") != issuer:
        raise InvalidTokenError("issuer", safe_detail(claims.get("iss")))
    exp = numeric(claims, "exp", required=True)
    iat = numeric(claims, "iat", required=True)
    nbf = numeric(claims, "nbf", required=False)
    assert exp is not None and iat is not None
    if now > exp + leeway_s:
        raise InvalidTokenError("expired")
    if iat > now + leeway_s:
        raise InvalidTokenError("iat_in_future")
    if nbf is not None and nbf > now + leeway_s:
        raise InvalidTokenError("not_yet_valid")
    if exp <= iat:
        raise InvalidTokenError("lifetime_invalid")
    if exp - iat > max_lifetime_s:
        raise InvalidTokenError("lifetime_exceeded", f"{exp - iat}s > {max_lifetime_s}s")
    if claims.get("typ") != token_type:
        raise InvalidTokenError("token_type", safe_detail(claims.get("typ")))
    string(claims, "sub", required=True)
    if audience not in str_list(claims, "aud"):
        raise InvalidTokenError("audience")
    return iat, exp
