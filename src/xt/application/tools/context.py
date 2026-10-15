# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ngữ cảnh một lời gọi tool: ai gọi (agent/người, đại diện cho ai), tenant, lớp client, thời điểm.

Không chứa token. Adapter (MCP) dựng từ `xt.domain.identity.Principal` đã kiểm.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from xt.domain.autonomy import RiskLevel
from xt.domain.identity import Principal, PrincipalKind


class ClientClass(StrEnum):
    """Lớp client cho làn PII (§3.8): `cloud` không bao giờ nhận `full`."""

    CLOUD = "cloud"
    LOCAL = "local"
    HUMAN = "human"


@dataclass(frozen=True, slots=True)
class PersonRef:
    """Người ở bề mặt tool (`person:<id>` + tên). `subject` là `sub` Keycloak nếu đã liên kết."""

    ref: str
    name: str
    subject: str | None = None

    def named(self) -> dict[str, str]:
        return {"ref": self.ref, "name": self.name}


@dataclass(frozen=True, slots=True)
class Actor:
    """Ảnh chụp danh tính đủ để thực thi lại một PendingOperation sau khi người xác nhận."""

    tenant: str
    subject: str
    kind: PrincipalKind
    client_id: str
    on_behalf_of: str | None
    scopes: frozenset[str] = frozenset()
    # Khóa DPoP đã kiểm của request (RFC 9449); vé /pii/reveal gắn vào khóa này. Không snapshot.
    dpop_jkt: str | None = None

    @property
    def is_agent(self) -> bool:
        # Fail-closed: chỉ đúng `human` là người.
        return self.kind is not PrincipalKind.HUMAN

    @property
    def represented_subject(self) -> str | None:
        """Người được đại diện (agent) hoặc chính người gọi (human). None: service account."""
        return self.on_behalf_of if self.is_agent else self.subject

    @property
    def key(self) -> str:
        """Effective actor cho namespace idempotency (§3.4): tenant + actor + tool."""
        return f"{self.kind.value}|{self.client_id}|{self.subject}|{self.on_behalf_of or ''}"

    def snapshot(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "subject": self.subject,
            "kind": self.kind.value,
            "client_id": self.client_id,
            "on_behalf_of": self.on_behalf_of,
        }

    @staticmethod
    def from_principal(principal: Principal, tenant: str) -> Actor:
        return Actor(
            tenant=tenant,
            subject=principal.subject,
            kind=principal.kind,
            client_id=principal.client_id,
            on_behalf_of=principal.on_behalf_of,
            scopes=principal.scopes,
            dpop_jkt=principal.dpop_jkt,
        )


def _no_check(level: RiskLevel) -> None:
    return None


@dataclass(frozen=True, slots=True)
class CallContext:
    actor: Actor
    client_class: ClientClass
    trace_id: str
    now: datetime
    # Kiểm thu hồi token (introspection) trước thao tác rủi ro cao; adapter gắn token vào closure.
    # Ném `xt.domain.identity.InvalidTokenError` nếu token đã bị thu hồi.
    ensure_active: Callable[[RiskLevel], None] = field(default=_no_check)
    confirmed_by: tuple[str, ...] = ()

    @property
    def tenant(self) -> str:
        return self.actor.tenant

    @property
    def now_iso(self) -> str:
        return iso(self.now)


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")
