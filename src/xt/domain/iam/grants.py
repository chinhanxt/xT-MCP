# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Receipt duyệt của người và ủy quyền (delegation). Dữ liệu do host giữ, không nhận từ agent."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self

from xt.domain.iam.attributes import json_default
from xt.domain.iam.decision import AccessRequest


def fingerprint(value: Mapping[str, Any]) -> str:
    """sha256 của JSON chuẩn hóa (khóa sắp xếp, cấm NaN/Inf).

    Ném `ValueError`/`TypeError` nếu không tuần tự hóa được.
    """
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=json_default
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalBinding:
    """Receipt chỉ có giá trị cho đúng thao tác này: actor, người được đại diện, tenant, action,
    đối tượng, revision và nội dung bản ghi."""

    actor: str
    behalf: str | None
    tenant: str
    action: str
    xrn: str
    revision: int
    resource_digest: str

    @classmethod
    def of(cls, req: AccessRequest) -> Self:
        return cls(
            req.actor.id,
            req.actor.behalf,
            req.actor.tenant,
            req.action,
            req.xrn,
            req.revision,
            fingerprint(req.resource),
        )

    def mismatches(self, other: ApprovalBinding) -> tuple[str, ...]:
        return tuple(f for f in self.__slots__ if getattr(self, f) != getattr(other, f))


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    by: str
    expires: int
    binding: ApprovalBinding


@dataclass(frozen=True, slots=True)
class DelegationGrant:
    """`by` (vắng mặt) ủy quyền cho `to` làm `action` trên `resource`, trong [start, end),
    với trần số tiền `limit`. Chỉ cấp cho người; agent hành động qua `behalf` + trần tự chủ."""

    by: str
    to: str
    action: str
    resource: str
    limit: int | float
    start: int
    end: int
    absent: bool = True
    revoked: bool = False
