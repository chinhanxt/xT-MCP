# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP (RFC 9449): kiểm proof `DPoP` ở resource server (§4.3, §7) cho client public.

Kiểm đúng §4.3: một header `DPoP`, JWS compact, `typ=dpop+jwt`, `alg` trong allowlist (không
`none`/HMAC), `jwk` là khóa CÔNG KHAI (không thành viên riêng), chữ ký đúng với `jwk` đó, `htm`
khớp method, `htu` khớp URI (chuẩn hóa: bỏ query/fragment, scheme/host chữ thường, bỏ cổng mặc
định), `iat` trong cửa sổ, `jti` dùng một lần (cache theo khóa), `ath` = base64url(SHA-256(access
token)), và thumbprint RFC 7638 của `jwk` == `cnf.jkt` của token. Lỗi -> `DpopError` (401
`WWW-Authenticate: DPoP`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import jwt

from xt.adapters.identity.config import IdentityConfigError, _Env
from xt.adapters.identity.single_use import InMemorySingleUseStore
from xt.application.ports import SingleUseStore
from xt.domain.identity import (
    IdentityProviderUnavailableError,
    InvalidTokenError,
    safe_detail,
)

PROOF_TYP = "dpop+jwt"
SUPPORTED_DPOP_ALGORITHMS = frozenset({"ES256", "ES384", "RS256", "PS256"})
DEFAULT_DPOP_ALGORITHMS = frozenset({"ES256"})
MAX_PROOF_BYTES = 8_192
MAX_JTI_CHARS = 256
_PRIVATE_MEMBERS = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
_THUMBPRINT_MEMBERS = {"EC": ("crv", "kty", "x", "y"), "RSA": ("e", "kty", "n")}
_ALG_KTY: dict[str, tuple[str, str | None]] = {
    "ES256": ("EC", "P-256"),
    "ES384": ("EC", "P-384"),
    "RS256": ("RSA", None),
    "PS256": ("RSA", None),
}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class DpopError(InvalidTokenError):
    """401 với challenge `DPoP`. `error` là mã OAuth: `invalid_dpop_proof` (proof sai) hoặc
    `invalid_token` (token gắn khóa mà thiếu proof/sai scheme; token không gắn mà dùng scheme
    DPoP)."""

    def __init__(
        self,
        reason: str,
        detail: str | None = None,
        *,
        error: str = "invalid_dpop_proof",
        algs: Iterable[str] = DEFAULT_DPOP_ALGORITHMS,
    ) -> None:
        super().__init__(reason, detail or "")
        self.error = error
        self.algs = tuple(sorted(algs))


@dataclass(frozen=True, slots=True)
class DpopSettings:
    """`required`: client trong `clients` PHẢI dùng token gắn DPoP (`XT_DPOP_REQUIRED`).
    Token có `cnf.jkt` luôn bị kiểm proof, bất kể cờ."""

    required: bool = False
    clients: frozenset[str] = frozenset({"xt-desk-agent"})
    algorithms: frozenset[str] = DEFAULT_DPOP_ALGORITHMS
    max_age_s: int = 60
    leeway_s: int = 30
    origins: tuple[str, ...] = ()  # origin công khai bổ sung (Desk proxy /mcp cùng origin)
    # `memory` (một tiến trình, test) | `postgres` (bảng xt_identity.dpop_jti, dùng chung mọi
    # worker/replica, sống qua restart; DPA-02). Store Postgres do composition root truyền vào.
    replay_store: str = "memory"


def load_dpop_settings(env: Mapping[str, str] | None = None) -> DpopSettings:
    e = _Env(env)
    required = e.boolean("XT_DPOP_REQUIRED", False)
    clients = frozenset(
        e.words("XT_DPOP_CLIENTS", required=False, sep=",")
        or (e.optional("XT_DESK_AGENT_CLIENT_ID") or "xt-desk-agent",)
    )
    algs = frozenset(
        e.words("XT_DPOP_ALGORITHMS", required=False, sep=",") or DEFAULT_DPOP_ALGORITHMS
    )
    bad = algs - SUPPORTED_DPOP_ALGORITHMS
    e.check(not bad, f"XT_DPOP_ALGORITHMS chứa thuật toán không cho phép: {sorted(bad)}")
    max_age = e.integer("XT_DPOP_PROOF_MAX_AGE_SECONDS", 60, 1, 300)
    leeway = e.integer("XT_DPOP_LEEWAY_SECONDS", 30, 0, 120)
    allow_http = e.boolean("XT_IDP_ALLOW_HTTP", False)
    replay = (e.optional("XT_DPOP_REPLAY_STORE") or "memory").lower()
    e.check(
        replay in ("memory", "postgres"),
        f"XT_DPOP_REPLAY_STORE={replay!r} phải là 'memory' hoặc 'postgres'",
    )
    workers = e.integer("WEB_CONCURRENCY", 1, 1, 1024)
    e.check(
        replay == "postgres" or workers == 1,
        "WEB_CONCURRENCY > 1 cần XT_DPOP_REPLAY_STORE=postgres (cache jti dùng chung, DPA-02)",
    )
    origins: list[str] = []
    for raw in (
        e.optional("XT_MCP_RESOURCE"),
        e.optional("XT_DESK_URL"),
        *e.words("XT_DPOP_ORIGINS", required=False, sep=","),
    ):
        if not raw:
            continue
        try:
            o = origin_of(raw)
        except ValueError:
            e.check(False, f"origin DPoP không hợp lệ: {raw!r}")
            continue
        e.check(
            o.startswith("https://") or allow_http,
            f"origin DPoP {o!r} dùng http; chỉ cho dev/test bằng XT_IDP_ALLOW_HTTP=true",
        )
        origins.append(o)
    return e.done(
        lambda: DpopSettings(
            required=required,
            clients=clients,
            algorithms=algs,
            max_age_s=max_age,
            leeway_s=leeway,
            origins=tuple(dict.fromkeys(origins)),
            replay_store=replay,
        )
    )


def _b64u_decode(s: str) -> bytes:
    if not isinstance(s, str) or "=" in s:
        raise ValueError("base64url")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def ath_of(value: str) -> str:
    """`ath` (RFC 9449 §4.2): base64url(SHA-256(ASCII của access token / giá trị được ràng))."""
    return b64u(hashlib.sha256(value.encode("ascii")).digest())


def origin_of(url: str) -> str:
    p = urlsplit(url.strip())
    if p.scheme.lower() not in _DEFAULT_PORTS or not p.hostname:
        raise ValueError(url)
    return normalize_htu(f"{p.scheme}://{p.netloc}/").rstrip("/")


def normalize_htu(url: str) -> str:
    """RFC 9449 §4.3 bước 9 + RFC 3986 §6.2: bỏ query/fragment, scheme/host chữ thường, bỏ cổng mặc
    định, path rỗng -> `/`. Không giải mã %-encoding (so khớp chuỗi path như server nhận)."""
    p = urlsplit(url)
    scheme = p.scheme.lower()
    host = p.hostname
    if scheme not in _DEFAULT_PORTS or not host or p.username or p.password:
        raise ValueError("htu")
    port = p.port
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    return urlunsplit((scheme, netloc, p.path or "/", "", ""))


def jwk_thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638 (base64url, SHA-256) trên thành viên bắt buộc theo `kty`."""
    members = _THUMBPRINT_MEMBERS.get(str(jwk.get("kty")))
    if members is None or any(not isinstance(jwk.get(m), str) for m in members):
        raise ValueError("jwk")
    doc = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return b64u(hashlib.sha256(doc.encode("utf-8")).digest())


def _canonical_b64(v: Any, length: int | None) -> bool:
    """Chuẩn tắc: không đệm, mã hóa lại ra đúng chuỗi (chặn bit thừa, như R2-02), đúng độ dài."""
    try:
        raw = _b64u_decode(v)
    except (ValueError, TypeError):
        return False
    return b64u(raw) == v and (length is None or len(raw) == length)


def _public_jwk(jwk: Any, alg: str) -> dict[str, Any]:
    if not isinstance(jwk, dict):
        raise DpopError("dpop_jwk", "missing jwk")
    if _PRIVATE_MEMBERS & set(jwk):
        raise DpopError("dpop_jwk", "private key material")
    kty, crv = _ALG_KTY[alg]
    if jwk.get("kty") != kty:
        raise DpopError("dpop_jwk", "kty/alg mismatch")
    if kty == "EC":
        size = 32 if crv == "P-256" else 48
        if jwk.get("crv") != crv or not all(_canonical_b64(jwk.get(c), size) for c in ("x", "y")):
            raise DpopError("dpop_jwk", "bad EC key")
    elif not (_canonical_b64(jwk.get("n"), None) and _canonical_b64(jwk.get("e"), None)):
        raise DpopError("dpop_jwk", "bad RSA key")
    elif len(_b64u_decode(jwk["n"])) * 8 < 2048:
        raise DpopError("dpop_jwk", "RSA < 2048")
    if jwk.get("use") not in (None, "sig"):
        raise DpopError("dpop_jwk", "use")
    return jwk


def _segment_json(seg: str) -> dict[str, Any]:
    try:
        doc = json.loads(_b64u_decode(seg))
    except (ValueError, TypeError) as exc:
        raise DpopError("dpop_malformed", "segment") from exc
    if not isinstance(doc, dict):
        raise DpopError("dpop_malformed", "segment not object")
    return doc


class DpopVerifier:
    """Kiểm proof DPoP. `replay` dùng chung cho mọi bề mặt của tiến trình (khóa gồm jkt + jti)."""

    def __init__(
        self,
        settings: DpopSettings,
        replay: SingleUseStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self._clock = clock
        self._replay = replay if replay is not None else InMemorySingleUseStore(clock)

    def _err(
        self, reason: str, detail: str | None = None, *, error: str = "invalid_dpop_proof"
    ) -> DpopError:
        return DpopError(reason, detail, error=error, algs=self.settings.algorithms)

    def required_for(self, client_id: str) -> bool:
        return self.settings.required and client_id in self.settings.clients

    def candidates(self, path: str) -> frozenset[str]:
        """htu chấp nhận cho `path` trên mọi origin công khai đã cấu hình của server này."""
        out: set[str] = set()
        for o in self.settings.origins:
            try:
                out.add(normalize_htu(o + path))
            except ValueError:
                continue
        return frozenset(out)

    def verify(
        self,
        proofs: Iterable[str],
        *,
        htm: str,
        htu: Iterable[str],
        bound_to: str | None,
        expected_jkt: str | None,
    ) -> str:
        """Trả jkt (RFC 7638) của khóa đã ký. `bound_to`: access token (hoặc vé) mà `ath` phải
        băm ra; None thì proof không được có `ath`. `expected_jkt`: None = chấp nhận mọi khóa."""
        s = self.settings
        items = list(proofs)
        if not items:
            raise self._err("dpop_missing", error="invalid_token")
        if len(items) != 1:
            raise self._err("dpop_multiple")  # §4.3 bước 1
        proof = items[0].strip()
        if len(proof) > MAX_PROOF_BYTES:
            raise self._err("dpop_too_large")
        parts = proof.split(".")
        if len(parts) != 3 or not parts[0] or not parts[1]:
            raise self._err("dpop_malformed", "not compact JWS")
        header = _segment_json(parts[0])
        typ = header.get("typ")
        if not isinstance(typ, str) or typ.lower() != PROOF_TYP:
            raise self._err("dpop_typ", safe_detail(typ))
        alg = header.get("alg")
        if not isinstance(alg, str) or alg not in s.algorithms:
            raise self._err("dpop_alg", safe_detail(alg))
        if "crit" in header:
            raise self._err("dpop_crit")
        jwk = _public_jwk(header.get("jwk"), alg)
        try:
            key = jwt.PyJWK(jwk, algorithm=alg)
            claims = jwt.decode(
                proof,
                key.key,
                algorithms=[alg],
                options={
                    "verify_signature": True,
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_sub": False,
                    "verify_jti": False,
                },
            )
        except jwt.InvalidSignatureError as exc:
            raise self._err("dpop_signature") from exc
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise self._err("dpop_malformed", type(exc).__name__) from exc
        jti = claims.get("jti")
        if not isinstance(jti, str) or not 1 <= len(jti) <= MAX_JTI_CHARS:
            raise self._err("dpop_jti")
        if claims.get("htm") != htm:
            raise self._err("dpop_htm", safe_detail(claims.get("htm")))
        raw_htu = claims.get("htu")
        try:
            got = normalize_htu(raw_htu) if isinstance(raw_htu, str) else None
        except ValueError:
            got = None
        if got is None or got not in {normalize_htu(u) for u in htu}:
            raise self._err("dpop_htu")
        ath = claims.get("ath")
        if bound_to is None:
            if ath is not None:
                raise self._err("dpop_ath_unexpected")
        elif not isinstance(ath, str) or not hmac.compare_digest(ath, ath_of(bound_to)):
            raise self._err("dpop_ath")
        jkt = jwk_thumbprint(jwk)
        if expected_jkt is not None and not hmac.compare_digest(jkt, expected_jkt):
            raise self._err("dpop_key_mismatch")
        # iat sau ath/jkt: proof cũ chỉ được báo "stale" khi đúng khóa + đúng giá trị ràng buộc
        # (/pii/reveal không tiêu vé với stale; kẻ không có khóa không được dò vé bằng iat cũ).
        iat = claims.get("iat")
        if isinstance(iat, bool) or not isinstance(iat, int | float) or not math.isfinite(iat):
            raise self._err("dpop_iat")  # DPA-01: NaN/±inf (json.loads nhận NaN) -> 401, không 500
        now = self._clock()
        if iat > now + s.leeway_s:
            raise self._err("dpop_iat_future")
        if iat < now - s.max_age_s - s.leeway_s:
            raise self._err("dpop_iat_stale")
        # Cuối cùng mới tiêu jti: proof sai không đốt jti của proof thật.
        expires = int(iat) + s.max_age_s + 2 * s.leeway_s
        try:
            first = self._replay.consume(f"dpop|{jkt}|{jti}", expires)
        except Exception as exc:  # store chung lỗi: fail-closed 503, không lùi về bộ nhớ
            raise IdentityProviderUnavailableError("dpop_replay_store", type(exc).__name__) from exc
        if not first:
            raise self._err("dpop_replay")
        return jkt


def build_dpop_verifier(
    env: Mapping[str, str] | None = None, replay: SingleUseStore | None = None
) -> DpopVerifier:
    """`XT_DPOP_REPLAY_STORE=postgres` bắt buộc truyền `replay` (store Postgres dùng chung);
    thiếu thì dừng khởi động thay vì lặng lẽ dùng bộ nhớ."""
    settings = load_dpop_settings(env)
    if settings.replay_store == "postgres" and replay is None:
        raise IdentityConfigError(
            ["XT_DPOP_REPLAY_STORE=postgres nhưng chưa có store Postgres (XT_DB_APP_URL)"]
        )
    return DpopVerifier(settings, replay)
