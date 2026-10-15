# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng (port) mà application cần; adapter hiện thực."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from xt.domain.identity import ClientRegistration, PrincipalKind


class SingleUseStore(Protocol):
    """Tiêu một khóa đúng một lần (`cid:<confirmation_id>` của biên nhận, `idt:<jti>` của ID token
    step-up). Prod: UNIQUE cùng transaction với hành động. `expires_at` (epoch s) cho phép dọn khóa;
    None = giữ vĩnh viễn (một quyết định cho mỗi confirmation)."""

    def consume(self, key: str, expires_at: int | None) -> bool:
        """True nếu đây là lần dùng đầu tiên; False nếu đã dùng."""
        ...


@dataclass(frozen=True, slots=True)
class IntrospectionResult:
    active: bool
    subject: str | None
    client_id: str | None


class TokenIntrospector(Protocol):
    def introspect(self, token: str) -> IntrospectionResult: ...


class ClientDirectory(Protocol):
    """Tra cứu đăng ký client tại IdP (chỉ đọc). None = không tồn tại.
    Lỗi kết nối/quyền: ném `IdentityProviderUnavailableError`."""

    def find(self, client_id: str) -> ClientRegistration | None: ...


class ClientAllowlist(Protocol):
    """Phân loại `azp` đã kiểm chữ ký: AGENT (client MCP) hoặc HUMAN (phiên Desk).
    Client không được nhận: `InvalidTokenError("client_not_allowed")`."""

    def classify(self, client_id: str) -> PrincipalKind: ...
