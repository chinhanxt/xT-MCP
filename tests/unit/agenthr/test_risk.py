# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""diff_risk (A5): mỗi loại cờ có test riêng; đổi JD không nâng quyền -> không cờ nào."""

from __future__ import annotations

from typing import Any

from xt.application.agenthr.gate import JobDescription, parse_capabilities
from xt.application.agenthr.risk import FLAGS, diff_risk

PII = {"employee_pii", "khach_hang"}
MONEY = {"payment_request", "advance_request"}


def jd(caps: list[dict[str, Any]], **kw: Any) -> JobDescription:
    base: dict[str, Any] = {
        "autonomy": 1,
        "daily_limit": 20,
        "value_limit": 5_000_000,
        "window": "08:00-17:00",
        "days": "mon-fri",
    }
    base.update(kw)
    return JobDescription("j", parse_capabilities(caps), **base)


def risk(old: JobDescription | None, new: JobDescription) -> list[str]:
    return diff_risk(old, new, pii_models=PII, money_models=MONEY)


OLD = jd([{"model": "stock_item", "thao_tac": ["read"]}])


def test_flag_names_match_contract() -> None:
    assert FLAGS == (
        "pii_reach_expansion",
        "money_reach_expansion",
        "outbound_release",
        "capability_expansion",
        "autonomy_raise",
        "limit_removed",
    )


def test_unchanged_or_narrowed_jd_has_no_flags() -> None:
    assert risk(OLD, OLD) == []
    narrower = jd([], daily_limit=10, value_limit=1_000_000, window="09:00-16:00", days="mon-wed")
    assert risk(OLD, narrower) == []


def test_pii_reach_expansion() -> None:
    new = jd([*[{"model": "stock_item", "thao_tac": ["read"]}],
              {"model": "employee_pii", "thao_tac": ["read"]}])  # fmt: skip
    assert risk(OLD, new) == ["pii_reach_expansion", "capability_expansion"]


def test_money_reach_expansion_only_for_writes() -> None:
    read_only = jd([{"model": "stock_item", "thao_tac": ["read"]},
                    {"model": "payment_request", "thao_tac": ["read"]}])  # fmt: skip
    assert risk(OLD, read_only) == ["capability_expansion"]
    write = jd([{"model": "stock_item", "thao_tac": ["read"]},
                {"model": "payment_request", "thao_tac": ["read", "create"]}])  # fmt: skip
    assert risk(OLD, write) == ["money_reach_expansion", "capability_expansion"]


def test_outbound_release() -> None:
    old = jd([{"model": "stock_request", "thao_tac": ["read", "create"]}])
    new = jd([{"model": "stock_request", "thao_tac": ["read", "create", "submit"]}])
    assert risk(old, new) == ["outbound_release", "capability_expansion"]


def test_capability_expansion_on_model_already_read() -> None:
    new = jd([{"model": "stock_item", "thao_tac": ["read", "update"]}])
    assert risk(OLD, new) == ["capability_expansion"]


def test_autonomy_raise() -> None:
    assert risk(OLD, jd([{"model": "stock_item", "thao_tac": ["read"]}], autonomy=2)) == [
        "autonomy_raise"
    ]
    assert risk(OLD, jd([{"model": "stock_item", "thao_tac": ["read"]}], autonomy=0)) == []


def test_limit_removed_or_relaxed() -> None:
    caps = [{"model": "stock_item", "thao_tac": ["read"]}]
    for kw in (
        {"daily_limit": 0},
        {"daily_limit": 21},
        {"value_limit": 0},
        {"value_limit": 6_000_000},
        {"window": None},
        {"window": "07:00-17:00"},
        {"window": "08:00-18:00"},
        {"days": None},
        {"days": "mon-sat"},
    ):
        assert risk(OLD, jd(caps, **kw)) == ["limit_removed"], kw


def test_first_hire_compares_with_empty_jd() -> None:
    hire = jd(
        [{"model": "payment_request", "thao_tac": ["read", "create", "submit"]},
         {"model": "khach_hang", "thao_tac": ["read"]}],
        autonomy=1,
    )  # fmt: skip
    assert risk(None, hire) == [
        "pii_reach_expansion",
        "money_reach_expansion",
        "outbound_release",
        "capability_expansion",
        "autonomy_raise",
    ]
    assert risk(None, jd([], autonomy=0)) == []
