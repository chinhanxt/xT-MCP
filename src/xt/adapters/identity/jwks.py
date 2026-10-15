# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cache JWKS có TTL, refetch khi gặp kid lạ (giới hạn tần suất), fail-closed.

Sửa VS #3 (`research/verification-spikes.md`): `PyJWKClient(cache_keys=True)` tin kid đã xóa vô
thời hạn. Ở đây cả bộ khóa được thay mới mỗi lần fetch, nên kid biến mất khỏi JWKS sẽ không còn
được tin sau tối đa `ttl_s`. Hết TTL mà không fetch được thì bỏ toàn bộ khóa (không dùng khóa cũ).
Tự viết thay vì `PyJWKClient` để tiêm đồng hồ cho test, bỏ khóa `use=enc`/thuật toán ngoài danh sách
và giới hạn tần suất refetch một cách xác định.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ec import SECP256R1, EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from xt.adapters.identity.config import IdpSettings
from xt.domain.identity import IdentityProviderUnavailableError, InvalidTokenError

log = logging.getLogger(__name__)

MIN_RSA_BITS = 2048

JwksFetcher = Callable[[], Mapping[str, Any]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class VerificationKey:
    kid: str
    kty: str
    alg: str | None
    key: RSAPublicKey | EllipticCurvePublicKey

    def compatible_with(self, alg: str) -> bool:
        if self.alg is not None and self.alg != alg:
            return False
        if alg == "RS256":
            return isinstance(self.key, RSAPublicKey)
        if alg == "ES256":
            return isinstance(self.key, EllipticCurvePublicKey) and isinstance(
                self.key.curve, SECP256R1
            )
        return False


class HttpJwksFetcher:
    """GET JWKS qua HTTP. URL có thể là địa chỉ nội bộ khác `iss` công khai (arch v2 §5.1)."""

    def __init__(self, url: str, timeout_s: float, client: httpx.Client | None = None) -> None:
        self._url = url
        self._client = client or httpx.Client(timeout=timeout_s, follow_redirects=False)

    def __call__(self) -> Mapping[str, Any]:
        r = self._client.get(self._url, headers={"Accept": "application/json"})
        r.raise_for_status()
        doc = r.json()
        if not isinstance(doc, dict) or not isinstance(doc.get("keys"), list):
            raise ValueError("JWKS không có mảng 'keys'")
        return doc


def parse_jwks(doc: Mapping[str, Any]) -> dict[str, VerificationKey]:
    """Chỉ giữ khóa ký công khai RSA (>= 2048 bit) và EC P-256. Kid trùng thì bỏ cả hai."""
    keys: dict[str, VerificationKey] = {}
    dup: set[str] = set()
    for jwk in doc.get("keys", []):
        if not isinstance(jwk, dict):
            continue
        kid, kty, use, alg = jwk.get("kid"), jwk.get("kty"), jwk.get("use"), jwk.get("alg")
        if not isinstance(kid, str) or not kid or use not in (None, "sig"):
            continue
        if alg is not None and not isinstance(alg, str):
            continue
        try:
            key: RSAPublicKey | EllipticCurvePublicKey
            if kty == "RSA":
                k = RSAAlgorithm.from_jwk(dict(jwk))
                if not isinstance(k, RSAPublicKey) or k.key_size < MIN_RSA_BITS:
                    continue
                key = k
            elif kty == "EC":
                if jwk.get("crv") != "P-256":
                    continue
                e = ECAlgorithm.from_jwk(dict(jwk))
                if not isinstance(e, EllipticCurvePublicKey):
                    continue
                key = e
            else:
                continue
        except Exception as exc:  # JWK hỏng: bỏ khóa đó, không làm hỏng cả bộ
            log.warning("bỏ JWK kid=%s: %s", kid, exc)
            continue
        if kid in keys or kid in dup:
            dup.add(kid)
            keys.pop(kid, None)
            continue
        keys[kid] = VerificationKey(kid=kid, kty=kty, alg=alg, key=key)
    return keys


class JwksCache:
    """Kid đã biết trả ngay (chỉ giữ lock trong vài phép gán). Gọi mạng nằm ngoài lock, theo kiểu
    single-flight: một luồng fetch, luồng khác cần kid lạ thì chờ kết quả lần fetch đó; luồng dùng
    kid đã biết không bao giờ chờ mạng."""

    def __init__(
        self,
        fetcher: JwksFetcher,
        ttl_s: float,
        min_refetch_interval_s: float,
        clock: Clock = time.monotonic,
    ) -> None:
        self._fetch = fetcher
        self._ttl = ttl_s
        self._min_interval = min_refetch_interval_s
        self._clock = clock
        self._cond = threading.Condition()
        self._keys: dict[str, VerificationKey] | None = None
        self._fetched_at = 0.0
        self._last_attempt: float | None = None
        self._inflight = False
        self._generation = 0
        self._last_error: str | None = None

    def _may_fetch(self, now: float) -> bool:
        return self._last_attempt is None or now - self._last_attempt >= self._min_interval

    def _expire(self, now: float) -> None:
        if self._keys is not None and now - self._fetched_at >= self._ttl:
            self._keys = None  # hết TTL: không tin khóa cũ nữa

    def _lookup(self, kid: str) -> VerificationKey | None:
        return None if self._keys is None else self._keys.get(kid)

    def get(self, kid: str) -> VerificationKey:
        with self._cond:
            now = self._clock()
            self._expire(now)
            key = self._lookup(kid)
            if key is not None:
                return key
            if self._inflight:
                gen = self._generation
                self._cond.wait_for(lambda: self._generation != gen)
                return self._result(kid)
            if not self._may_fetch(now):
                return self._result(kid)
            self._inflight = True
            self._last_attempt = now
        try:
            keys: dict[str, VerificationKey] | None = parse_jwks(self._fetch())
            error = None
        except Exception as exc:
            log.error("không tải được JWKS: %s", exc)
            keys, error = None, str(exc)
        with self._cond:
            if keys is not None:
                self._keys = keys
                self._fetched_at = now
            self._last_error = error
            self._inflight = False
            self._generation += 1
            self._cond.notify_all()
            if error is not None and self._lookup(kid) is None:
                raise IdentityProviderUnavailableError("jwks_unavailable", error)
            return self._result(kid)

    def _result(self, kid: str) -> VerificationKey:
        """Gọi khi đang giữ lock, sau khi không thể/không cần fetch thêm."""
        self._expire(self._clock())
        if self._keys is None:
            raise IdentityProviderUnavailableError(
                "jwks_unavailable", self._last_error or "đang chờ refetch"
            )
        key = self._keys.get(kid)
        if key is None:
            raise InvalidTokenError("unknown_kid", kid)
        return key


def build_jwks_cache(idp: IdpSettings, fetcher: JwksFetcher | None = None) -> JwksCache:
    """Một cache cho mỗi issuer; access token và ID token step-up dùng chung được."""
    return JwksCache(
        fetcher or HttpJwksFetcher(idp.jwks_url, idp.http_timeout_s),
        ttl_s=idp.jwks_ttl_s,
        min_refetch_interval_s=idp.jwks_min_refetch_interval_s,
    )
