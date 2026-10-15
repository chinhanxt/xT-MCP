# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cấu hình lớp danh tính từ biến môi trường (stdlib).

Không có host/realm/secret mặc định. Thiếu hoặc sai -> `IdentityConfigError` liệt kê đủ mọi lỗi,
dùng lúc khởi động để dừng tiến trình. Danh sách biến: `docs/identity.md`.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeVar
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

SUPPORTED_ALGORITHMS = frozenset({"RS256", "ES256"})
FORBIDDEN_ADVERTISED_SCOPES = frozenset({"offline_access"})
MAX_LEEWAY_S = 120
MAX_INTROSPECTION_CACHE_S = 60
DEFAULT_MAX_TOKEN_BYTES = 16_384

T = TypeVar("T")


class IdentityConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("cấu hình danh tính sai:\n  - " + "\n  - ".join(problems))


class _Env:
    """Đọc env, gom lỗi thay vì dừng ở lỗi đầu tiên."""

    def __init__(self, env: Mapping[str, str] | None) -> None:
        self._env = os.environ if env is None else env
        self.problems: list[str] = []

    def _raw(self, name: str) -> str | None:
        v = self._env.get(name)
        if v is None:
            return None
        v = v.strip()
        return v or None

    def required(self, name: str) -> str:
        v = self._raw(name)
        if v is None:
            self.problems.append(f"{name} bắt buộc nhưng chưa đặt")
            return ""
        return v

    def optional(self, name: str) -> str | None:
        return self._raw(name)

    def integer(self, name: str, default: int, lo: int, hi: int) -> int:
        v = self._raw(name)
        if v is None:
            return default
        try:
            n = int(v)
        except ValueError:
            self.problems.append(f"{name}={v!r} không phải số nguyên")
            return default
        if not lo <= n <= hi:
            self.problems.append(f"{name}={n} ngoài khoảng [{lo}, {hi}]")
        return n

    def number(self, name: str, default: float, lo: float, hi: float) -> float:
        v = self._raw(name)
        if v is None:
            return default
        try:
            n = float(v)
        except ValueError:
            self.problems.append(f"{name}={v!r} không phải số")
            return default
        if not lo <= n <= hi:
            self.problems.append(f"{name}={n} ngoài khoảng [{lo}, {hi}]")
        return n

    def boolean(self, name: str, default: bool) -> bool:
        v = self._raw(name)
        if v is None:
            return default
        low = v.lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
        self.problems.append(f"{name}={v!r} không phải boolean (true/false)")
        return default

    def words(self, name: str, *, required: bool, sep: str | None = None) -> tuple[str, ...]:
        v = self.required(name) if required else (self._raw(name) or "")
        items = [x.strip() for x in (v.split(sep) if sep else v.split())]
        out = tuple(dict.fromkeys(x for x in items if x))
        if required and v and not out:
            self.problems.append(f"{name} rỗng")
        return out

    def url(self, name: str, allow_http: bool, *, required: bool = True) -> str:
        v = self.required(name) if required else (self._raw(name) or "")
        if not v:
            return ""
        try:
            parts = urlsplit(v)
        except ValueError:
            self.problems.append(f"{name} không phải URL hợp lệ")
            return v
        if parts.username or parts.password:
            # Không echo giá trị: có thể chứa mật khẩu.
            self.problems.append(f"{name} không được chứa thông tin đăng nhập trong URL")
            return v
        if parts.scheme not in ("https", "http") or not parts.hostname:
            self.problems.append(f"{name}={v!r} phải là URL http(s) tuyệt đối")
        elif parts.scheme == "http" and not allow_http:
            self.problems.append(
                f"{name}={v!r} dùng http; chỉ cho dev/test bằng XT_IDP_ALLOW_HTTP=true"
            )
        if parts.fragment:
            self.problems.append(f"{name}={v!r} không được có fragment")
        return v

    def check(self, ok: bool, message: str) -> None:
        if not ok:
            self.problems.append(message)

    def done(self, build: Callable[[], T]) -> T:
        if self.problems:
            raise IdentityConfigError(self.problems)
        return build()


@dataclass(frozen=True, slots=True)
class IdpSettings:
    """Phần chung về issuer Keycloak: dùng cho access token và ID token step-up."""

    issuer: str
    jwks_url: str
    algorithms: frozenset[str]
    leeway_s: int
    max_token_lifetime_s: int
    jwks_ttl_s: int
    jwks_min_refetch_interval_s: int
    http_timeout_s: float
    tenant_claim: str
    max_token_bytes: int = DEFAULT_MAX_TOKEN_BYTES


@dataclass(frozen=True, slots=True)
class ResourceServerSettings:
    """`allowed_clients`: client MCP (connector, agent) -> Principal `agent`.
    `human_clients`: client phiên web Desk -> Principal `human`. Client khác bị từ chối."""

    idp: IdpSettings
    resource: str
    allowed_clients: frozenset[str]
    required_scopes: frozenset[str]
    scopes_supported: tuple[str, ...]
    human_clients: frozenset[str] = frozenset()
    dynamic_connectors: bool = False


@dataclass(frozen=True, slots=True)
class StepUpSettings:
    idp: IdpSettings
    client_id: str
    acr: str
    max_auth_age_s: int
    required_amr: frozenset[str]


@dataclass(frozen=True, slots=True)
class ReceiptSettings:
    issuer: str
    signing_key: Ed25519PrivateKey = field(repr=False)
    ttl_s: int


class IntrospectionMode(StrEnum):
    OFF = "off"
    HIGH_RISK = "high_risk"


@dataclass(frozen=True, slots=True)
class IntrospectionSettings:
    mode: IntrospectionMode
    endpoint: str | None
    client_id: str | None
    client_secret: str | None = field(repr=False)
    cache_ttl_s: int
    http_timeout_s: float


def _idp(e: _Env) -> IdpSettings:
    allow_http = e.boolean("XT_IDP_ALLOW_HTTP", False)
    issuer = e.url("XT_IDP_ISSUER", allow_http)
    jwks_url = e.url("XT_IDP_JWKS_URL", allow_http)
    algs = frozenset(e.words("XT_IDP_ALGORITHMS", required=False, sep=",") or SUPPORTED_ALGORITHMS)
    bad = algs - SUPPORTED_ALGORITHMS
    e.check(not bad, f"XT_IDP_ALGORITHMS chứa thuật toán không cho phép: {sorted(bad)}")
    ttl = e.integer("XT_IDP_JWKS_TTL_SECONDS", 300, 1, 86_400)
    refetch = e.integer("XT_IDP_JWKS_MIN_REFETCH_SECONDS", 10, 1, 3_600)
    e.check(
        refetch <= ttl,
        "XT_IDP_JWKS_MIN_REFETCH_SECONDS phải <= XT_IDP_JWKS_TTL_SECONDS",
    )
    return IdpSettings(
        issuer=issuer,
        jwks_url=jwks_url,
        algorithms=algs,
        leeway_s=e.integer("XT_IDP_LEEWAY_SECONDS", 30, 0, MAX_LEEWAY_S),
        max_token_lifetime_s=e.integer("XT_IDP_MAX_TOKEN_LIFETIME_SECONDS", 900, 1, 86_400),
        jwks_ttl_s=ttl,
        jwks_min_refetch_interval_s=refetch,
        http_timeout_s=e.number("XT_IDP_HTTP_TIMEOUT_SECONDS", 5.0, 0.1, 60.0),
        tenant_claim=e.optional("XT_IDP_TENANT_CLAIM") or "organization",
        max_token_bytes=e.integer(
            "XT_IDP_MAX_TOKEN_BYTES", DEFAULT_MAX_TOKEN_BYTES, 1_024, 1_048_576
        ),
    )


def load_resource_server_settings(env: Mapping[str, str] | None = None) -> ResourceServerSettings:
    e = _Env(env)
    idp = _idp(e)
    resource = e.url("XT_MCP_RESOURCE", e.boolean("XT_IDP_ALLOW_HTTP", False))
    if resource:
        e.check(not urlsplit(resource).query, "XT_MCP_RESOURCE không được có query (RFC 8707)")
    registry = e.optional("XT_MCP_CLIENT_REGISTRY") or "static"
    e.check(
        registry in ("static", "keycloak"),
        f"XT_MCP_CLIENT_REGISTRY={registry!r} phải là 'static' hoặc 'keycloak'",
    )
    dynamic = registry == "keycloak"
    # Có registry động thì danh sách tĩnh (client first-party) được phép rỗng.
    clients = frozenset(e.words("XT_MCP_ALLOWED_CLIENTS", required=not dynamic, sep=","))
    humans = frozenset(e.words("XT_MCP_HUMAN_CLIENTS", required=False, sep=","))
    e.check(
        not clients & humans,
        f"client vừa là agent vừa là human: {sorted(clients & humans)}",
    )
    required = frozenset(e.words("XT_MCP_REQUIRED_SCOPES", required=True))
    supported = e.words("XT_MCP_SCOPES_SUPPORTED", required=True)
    e.check(
        required <= set(supported),
        f"XT_MCP_SCOPES_SUPPORTED thiếu scope bắt buộc: {sorted(required - set(supported))}",
    )
    banned = (set(supported) | required) & FORBIDDEN_ADVERTISED_SCOPES
    e.check(not banned, f"không được quảng bá/bắt buộc {sorted(banned)} (MCP spec)")
    return e.done(
        lambda: ResourceServerSettings(
            idp=idp,
            resource=resource,
            allowed_clients=clients,
            required_scopes=required,
            scopes_supported=supported,
            human_clients=humans,
            dynamic_connectors=dynamic,
        )
    )


def load_step_up_settings(env: Mapping[str, str] | None = None) -> StepUpSettings:
    e = _Env(env)
    idp = _idp(e)
    client_id = e.required("XT_CONFIRM_CLIENT_ID")
    acr = e.required("XT_CONFIRM_ACR")
    max_age = e.integer("XT_CONFIRM_MAX_AUTH_AGE_SECONDS", 300, 1, 3_600)
    amr = frozenset(e.words("XT_CONFIRM_REQUIRED_AMR", required=False))
    return e.done(
        lambda: StepUpSettings(
            idp=idp, client_id=client_id, acr=acr, max_auth_age_s=max_age, required_amr=amr
        )
    )


def load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"{path}: không phải khóa riêng Ed25519 (PKCS#8 PEM)")
    return key


def load_receipt_settings(env: Mapping[str, str] | None = None) -> ReceiptSettings:
    e = _Env(env)
    issuer = e.required("XT_RECEIPT_ISSUER")
    key_file = e.required("XT_RECEIPT_SIGNING_KEY_FILE")
    ttl = e.integer("XT_RECEIPT_TTL_SECONDS", 300, 1, 3_600)
    key: Ed25519PrivateKey | None = None
    if key_file:
        try:
            key = load_ed25519_private_key(Path(key_file))
        except (OSError, ValueError, TypeError) as exc:
            e.check(False, f"XT_RECEIPT_SIGNING_KEY_FILE={key_file!r}: {exc}")
    return e.done(lambda: ReceiptSettings(issuer=issuer, signing_key=_must(key), ttl_s=ttl))


def load_introspection_settings(env: Mapping[str, str] | None = None) -> IntrospectionSettings:
    e = _Env(env)
    raw_mode = e.required("XT_IDP_INTROSPECTION")
    mode = IntrospectionMode.OFF
    if raw_mode:
        try:
            mode = IntrospectionMode(raw_mode)
        except ValueError:
            e.check(False, f"XT_IDP_INTROSPECTION={raw_mode!r} phải là 'off' hoặc 'high_risk'")
    allow_http = e.boolean("XT_IDP_ALLOW_HTTP", False)
    names = (
        "XT_IDP_INTROSPECTION_URL",
        "XT_IDP_INTROSPECTION_CLIENT_ID",
        "XT_IDP_INTROSPECTION_CLIENT_SECRET",
    )
    cache = e.integer("XT_IDP_INTROSPECTION_CACHE_SECONDS", 30, 0, MAX_INTROSPECTION_CACHE_S)
    timeout = e.number("XT_IDP_HTTP_TIMEOUT_SECONDS", 5.0, 0.1, 60.0)
    if mode is IntrospectionMode.OFF:
        stray = [n for n in names if e.optional(n) is not None]
        e.check(not stray, f"XT_IDP_INTROSPECTION=off nhưng có đặt {stray}; bỏ hoặc bật high_risk")
        return e.done(lambda: IntrospectionSettings(mode, None, None, None, cache, timeout))
    endpoint = e.url(names[0], allow_http)
    client_id = e.required(names[1])
    secret = e.required(names[2])
    return e.done(lambda: IntrospectionSettings(mode, endpoint, client_id, secret, cache, timeout))


def _must[V](v: V | None) -> V:
    if v is None:  # pragma: no cover - done() đã chặn trước
        raise AssertionError("giá trị bắt buộc bị thiếu")
    return v


@dataclass(frozen=True, slots=True)
class ConnectorRegistrySettings:
    """Allowlist động cho connector DCR (`XT_MCP_CLIENT_REGISTRY=keycloak`)."""

    admin_api_url: str
    token_url: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_hosts: frozenset[str] = frozenset()
    cache_ttl_s: int = 60
    negative_cache_ttl_s: int = 30
    lookups_per_minute: int = 60
    http_timeout_s: float = 5.0


def load_connector_registry_settings(
    env: Mapping[str, str] | None = None,
) -> ConnectorRegistrySettings:
    e = _Env(env)
    allow_http = e.boolean("XT_IDP_ALLOW_HTTP", False)
    api = e.url("XT_IDP_ADMIN_API_URL", allow_http)
    token = e.url("XT_IDP_ADMIN_TOKEN_URL", allow_http)
    cid = e.required("XT_IDP_ADMIN_CLIENT_ID")
    secret = e.required("XT_IDP_ADMIN_CLIENT_SECRET")
    hosts = frozenset(
        h.lower() for h in e.words("XT_MCP_CONNECTOR_REDIRECT_HOSTS", required=True, sep=",")
    )
    e.check(
        not any("*" in h or "/" in h for h in hosts),
        "XT_MCP_CONNECTOR_REDIRECT_HOSTS: chỉ tên host",
    )
    ttl = e.integer("XT_MCP_CONNECTOR_CACHE_SECONDS", 60, 1, 300)
    neg = e.integer("XT_MCP_CONNECTOR_NEGATIVE_CACHE_SECONDS", 30, 1, 300)
    rate = e.integer("XT_MCP_CONNECTOR_LOOKUPS_PER_MINUTE", 60, 1, 6_000)
    timeout = e.number("XT_IDP_HTTP_TIMEOUT_SECONDS", 5.0, 0.1, 60.0)
    return e.done(
        lambda: ConnectorRegistrySettings(
            admin_api_url=api.rstrip("/"),
            token_url=token,
            client_id=cid,
            client_secret=secret,
            redirect_hosts=hosts,
            cache_ttl_s=ttl,
            negative_cache_ttl_s=neg,
            lookups_per_minute=rate,
            http_timeout_s=timeout,
        )
    )
