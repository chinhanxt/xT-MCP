# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Mức rủi ro của action IAM. Là metadata của host, không bao giờ nhận từ tham số agent."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from xt.domain.autonomy import RiskLevel

_RANK = {
    RiskLevel.L0: 0,
    RiskLevel.L1: 1,
    RiskLevel.L2: 2,
    RiskLevel.L3: 3,
    RiskLevel.HUMAN_ONLY: 4,
}


def rank(level: RiskLevel) -> int:
    """L0..L3 -> 0..3; `human_only` -> 4 (nằm ngoài thang L)."""
    return _RANK[level]


ActionCatalog = Mapping[str, RiskLevel]

_L = RiskLevel
DEFAULT_ACTION_LEVELS: ActionCatalog = MappingProxyType(
    {
        # Action gốc của spike IAM.
        "record.read": _L.L0,
        "record.create": _L.L1,
        "record.update": _L.L1,
        "record.approve": _L.L2,
        "record.delete": _L.L3,
        "policy.activate": _L.L3,
        # Action IAM của tool-contracts-v1: mức = trần tĩnh `max_autonomy`,
        # effective_level do server tính theo từng lời gọi.
        "workspace.read": _L.L0,
        "model.read": _L.L0,
        "process.read": _L.L0,
        "person.read": _L.L0,
        "record.search": _L.L0,
        "record.aggregate": _L.L0,
        "inbox.read": _L.L0,
        "history.read": _L.L0,
        "record.transition": _L.L3,
        "file.create": _L.L1,
        "request.submit": _L.L2,
        "request.decide": _L.HUMAN_ONLY,
        "task.create": _L.L2,
        "model.propose": _L.L1,
        "process.start": _L.L3,
        "process.propose": _L.L1,
        "notification.send": _L.L2,
    }
)
