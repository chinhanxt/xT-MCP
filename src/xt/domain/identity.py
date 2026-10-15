# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểu miền cho danh tính: Principal, bằng chứng step-up, biên nhận duyệt, lỗi xác thực.

Module thuần: không I/O, không phụ thuộc thư viện JWT/HTTP.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

MAX_SAFE_INTEGER = 2**53 - 1
MAX_ERROR_DETAIL = 160


class PrincipalKind(StrEnum):
    """Suy từ client (`azp`), không từ grant: token mà client MCP (connector, agent Desk) trình cho
    resource server là `agent`, kể cả khi lấy bằng auth-code của một người (khi đó `on_behalf_of`
    = `sub` của người đó). Chỉ client phiên web Desk (cấu hình riêng) mới cho `human`."""

    HUMAN = "human"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class Principal:
    """Danh tính đã kiểm từ access token. `client_id` là `azp`. Với `agent`, tác nhân là client
    `client_id`; `on_behalf_of` là `sub` của người ủy quyền (None nếu là service account)."""

    subject: str
    kind: PrincipalKind
    client_id: str
    on_behalf_of: str | None
    issuer: str
    username: str | None
    organizations: tuple[str, ...]
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_time: int | None
    acr: str | None
    amr: tuple[str, ...]
    token_id: str | None
    issued_at: int
    expires_at: int

    @property
    def tenant(self) -> str | None:
        """Tenant duy nhất; None nếu không có Organization hoặc có nhiều (`tenant_ambiguous`)."""
        return self.organizations[0] if len(self.organizations) == 1 else None

    @property
    def tenant_ambiguous(self) -> bool:
        return len(self.organizations) > 1

    def has_scopes(self, required: Iterable[str]) -> bool:
        return frozenset(required) <= self.scopes


@dataclass(frozen=True, slots=True)
class StepUpEvidence:
    """Bằng chứng xác thực tươi (ID token của client confirm) đã kiểm đủ điều kiện."""

    subject: str
    username: str | None
    client_id: str
    auth_time: int
    acr: str
    amr: tuple[str, ...]
    session_id: str | None
    token_id: str | None


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class ConfirmationBinding:
    """Thứ biên nhận gắn vào: confirmation, digest chụp lúc tạo confirmation, người đề nghị."""

    confirmation_id: str
    request_digest: str
    requester_subject: str


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    receipt_id: str
    issuer: str
    confirmation_id: str
    request_digest: str
    approver: str
    requester: str
    decision: ApprovalDecision
    issued_at: int
    expires_at: int
    evidence: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ClientRegistration:
    """Sự thật về một client OAuth do IdP báo (Admin REST), dùng để xét connector DCR."""

    client_id: str
    enabled: bool
    public: bool
    protocol: str | None
    standard_flow: bool
    implicit_flow: bool
    direct_access_grants: bool
    service_accounts: bool
    bearer_only: bool
    full_scope_allowed: bool
    pkce_method: str | None
    redirect_uris: tuple[str, ...]
    protocol_mappers: tuple[str, ...]
    extra_grants: tuple[str, ...]  # thuộc tính `*grant.enabled`/token exchange đang bật


@dataclass(frozen=True, slots=True)
class ConnectorPolicy:
    """Chính sách DCR đã gia cố. `redirect_hosts`: host connector được phép (so khớp chính xác,
    không phân biệt hoa thường). `http` chỉ được nhận cho host loopback có trong danh sách."""

    redirect_hosts: frozenset[str]


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def connector_violation(reg: ClientRegistration, policy: ConnectorPolicy) -> str | None:
    """None nếu client là connector MCP hợp lệ; ngược lại là mã lý do (ổn định, cho log/test)."""
    checks: list[tuple[bool, str]] = [
        (reg.enabled, "disabled"),
        (reg.protocol in (None, "openid-connect"), "protocol"),
        (reg.public, "not_public"),
        (not reg.bearer_only, "bearer_only"),
        (reg.standard_flow, "no_authorization_code"),
        (not reg.implicit_flow, "implicit_flow"),
        (not reg.direct_access_grants, "password_grant"),
        (not reg.service_accounts, "service_account"),
        (not reg.extra_grants, "extra_grant"),
        (reg.pkce_method == "S256", "pkce_not_s256"),
        (not reg.full_scope_allowed, "full_scope"),
        (not reg.protocol_mappers, "protocol_mappers"),
        (bool(reg.redirect_uris), "no_redirect_uri"),
    ]
    for ok, reason in checks:
        if not ok:
            return reason
    hosts = {h.lower() for h in policy.redirect_hosts}
    for uri in reg.redirect_uris:
        if "*" in uri:
            return "redirect_wildcard"
        try:
            parts = urlsplit(uri)
            host = (parts.hostname or "").lower()
        except ValueError:
            return "redirect_invalid"
        if parts.fragment or parts.username or parts.password or host not in hosts:
            return "redirect_host"
        if parts.scheme == "http" and host in LOOPBACK_HOSTS:
            continue
        if parts.scheme != "https":
            return "redirect_scheme"
    return None


def _jcs(v: Any, out: list[str]) -> None:
    """RFC 8785 (JCS) cho tập con xT dùng: object khóa chuỗi, mảng, chuỗi, bool, null, số nguyên.

    Số: chỉ số nguyên trong khoảng an toàn IEEE-754 (|n| <= 2^53-1). Tiền là số nguyên VND (đơn vị
    nhỏ nhất); giá trị thập phân thì truyền dạng chuỗi do nghiệp vụ chuẩn hóa. `Decimal` nguyên
    (NUMERIC từ Postgres) được nhận như số nguyên; `float` và `Decimal` có phần lẻ bị từ chối để
    digest không phụ thuộc định dạng số. Không chuẩn hóa Unicode (NFC làm ở biên nhập liệu).
    """
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, int | Decimal) and not isinstance(v, bool):
        if isinstance(v, Decimal):
            if not v.is_finite() or v != v.to_integral_value():
                raise ValueError(f"digest: Decimal không nguyên {v!s:.40}")
            v = int(v)
        if abs(v) > MAX_SAFE_INTEGER:
            raise ValueError("digest: số nguyên vượt 2^53-1")
        out.append(str(v))
    elif isinstance(v, float):
        raise ValueError("digest: không nhận float (tiền là số nguyên VND hoặc chuỗi)")
    elif isinstance(v, str):
        try:
            v.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("digest: chuỗi chứa surrogate lẻ") from exc
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, Mapping):
        keys = list(v.keys())
        if not all(isinstance(k, str) for k in keys):
            raise ValueError("digest: khóa object phải là chuỗi")
        out.append("{")
        for n, k in enumerate(sorted(keys, key=lambda k: k.encode("utf-16-be"))):
            if n:
                out.append(",")
            _jcs(k, out)
            out.append(":")
            _jcs(v[k], out)
        out.append("}")
    elif isinstance(v, list | tuple):
        out.append("[")
        for n, x in enumerate(v):
            if n:
                out.append(",")
            _jcs(x, out)
        out.append("]")
    else:
        raise ValueError(f"digest: kiểu không hỗ trợ {type(v).__name__}")


def canonical_json(value: Any) -> bytes:
    out: list[str] = []
    _jcs(value, out)
    return "".join(out).encode("utf-8")


def request_digest(request: Mapping[str, Any]) -> str:
    """SHA-256 hex của JSON chuẩn hóa RFC 8785 (xem `_jcs` cho quy tắc số/khóa)."""
    return hashlib.sha256(canonical_json(request)).hexdigest()


def safe_detail(value: Any) -> str:
    """Giá trị (có thể do attacker gửi) đưa vào thông báo lỗi: một dòng, giới hạn độ dài."""
    text = value if isinstance(value, str) else repr(value)
    text = "".join(c if c.isprintable() else "?" for c in text)
    return text if len(text) <= MAX_ERROR_DETAIL else text[: MAX_ERROR_DETAIL - 3] + "..."


class IdentityError(Exception):
    """Gốc các lỗi danh tính. `reason` là mã máy đọc (ổn định, dùng cho log/test)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        reason = safe_detail(reason)
        detail = safe_detail(detail) if detail else ""
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


class InvalidTokenError(IdentityError):
    """Token thiếu/sai/hết hạn -> HTTP 401 `invalid_token`."""


class TokenRevokedError(InvalidTokenError):
    """Introspection báo token không còn hiệu lực (user bị khóa, logout, thu hồi)."""


class InsufficientScopeError(IdentityError):
    """Token hợp lệ nhưng thiếu scope -> HTTP 403 `insufficient_scope`."""

    def __init__(self, required: Iterable[str], present: Iterable[str] = ()) -> None:
        self.required = frozenset(required)
        self.present = frozenset(present)
        missing = " ".join(sorted(self.required - self.present))
        super().__init__("insufficient_scope", f"missing: {missing}")


class StepUpRejectedError(IdentityError):
    """Bằng chứng step-up không đạt (client, acr, auth_time, nonce, người duyệt)."""


class SeparationOfDutiesError(StepUpRejectedError):
    """Người duyệt trùng người đề nghị."""


class ReceiptRejectedError(IdentityError):
    """Biên nhận duyệt không hợp lệ, sai yêu cầu, hết hạn hoặc đã dùng."""


class IdentityProviderUnavailableError(IdentityError):
    """Không lấy được JWKS/introspection; luôn fail-closed."""
