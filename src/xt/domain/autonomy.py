# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Thang mức tự chủ (architecture-v2 §5.4)."""

from __future__ import annotations

from enum import StrEnum


class RiskLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    HUMAN_ONLY = "human_only"

    @property
    def high_risk(self) -> bool:
        """L2, L3, human_only: phải kiểm thu hồi token (introspection) trước khi thực thi."""
        return self in (RiskLevel.L2, RiskLevel.L3, RiskLevel.HUMAN_ONLY)
