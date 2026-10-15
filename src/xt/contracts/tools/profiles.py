# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hồ sơ catalog (tool-contracts-v1 §10). Server mặc định quảng bá đủ catalog (REA); `core-10`
dành cho client local với model nhỏ (qwen3.5:4b đo 3.916 token so với 8.155 của catalog 17)."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType

from xt.contracts.tools.catalog import BY_NAME, CATALOG
from xt.contracts.tools.contract import ToolContract


class Profile(StrEnum):
    FULL = "full"
    CORE_10 = "core-10"


CORE_10: tuple[str, ...] = (
    "find_people",
    "search_records",
    "aggregate_records",
    "get_record",
    "get_inbox",
    "create_record",
    "create_task",
    "submit_request",
    "transition_record",
    "decide_request",
)

PROFILES: Mapping[Profile, tuple[str, ...]] = MappingProxyType(
    {Profile.FULL: tuple(c.name for c in CATALOG), Profile.CORE_10: CORE_10}
)


def profile_tools(profile: Profile) -> tuple[ToolContract, ...]:
    """Thứ tự theo catalog (§4.1), không theo danh sách hồ sơ."""
    names = set(PROFILES[profile])
    missing = names - set(BY_NAME)
    if missing:
        raise ValueError(f"hồ sơ {profile} chứa tool không có trong catalog: {sorted(missing)}")
    return tuple(c for c in CATALOG if c.name in names)


def profiles_of(name: str) -> list[str]:
    return [p.value for p, names in PROFILES.items() if name in names]
