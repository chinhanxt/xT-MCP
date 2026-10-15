# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ngữ cảnh một lời gọi trên dữ liệu tenant. Lấy từ token đã xác thực, KHÔNG từ tham số tool."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from xt.domain.model.ident import check_role

MAX_TENANTS_PER_DB = 200  # SMT §3: ~200 tenant x 30 model ≈ 6k bảng mỗi DB
MAX_MODEL_TABLES_PER_DB = 6000


def data_schema(tenant_seq: int) -> str:
    return f"xt_t{tenant_seq}"


def api_schema(tenant_seq: int) -> str:
    return f"api_t{tenant_seq}"


@dataclass(frozen=True, slots=True)
class TenantRef:
    seq: int
    id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.seq, int) or isinstance(self.seq, bool) or not 1 <= self.seq < 10**7:
            raise ValueError("tenant_seq phải là số nguyên 1..9999999")


@dataclass(frozen=True, slots=True)
class ModelActor:
    """Người/agent đang gọi, trong đúng một tenant. Tên role đi vào GUC nên bị whitelist."""

    tenant: TenantRef
    actor_id: UUID
    roles: tuple[str, ...]
    team_ids: tuple[UUID, ...] = ()
    department_ids: tuple[UUID, ...] = ()
    effective_actor: UUID | None = None
    client_azp: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        for r in self.roles:
            check_role(r)
        if self.client_azp is not None and (len(self.client_azp) > 200 or "," in self.client_azp):
            raise ValueError("client_azp không hợp lệ")
        if self.trace_id is not None and len(self.trace_id) > 128:
            raise ValueError("trace_id quá dài")
