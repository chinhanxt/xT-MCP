# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng AgentHR thuần (gate.py): từng nhánh chặn 1-7 của kế hoạch A2 + ánh xạ tool -> JD."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from xt.application.agenthr.gate import (
    AGENT_SUSPENDED,
    AGENT_TERMINATED,
    DAILY_LIMIT,
    HELD,
    OUTSIDE_HOURS,
    OUTSIDE_JD,
    OVER_VALUE,
    Intent,
    JobDescription,
    Permit,
    Profile,
    ScheduleError,
    capability_rows,
    check_write,
    hours_block,
    intent_model,
    parse_capabilities,
    parse_days,
    parse_window,
    status_block,
)
from xt.contracts.tools.envelope import REASONS

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
TODAY = date(2026, 10, 21)  # thứ Tư
JD = JobDescription(
    "jd1",
    parse_capabilities(
        [
            {"model": "payment_request", "thao_tac": ["read", "create", "update", "submit"]},
            {"model": "stock_item", "thao_tac": ["read"]},
        ]
    ),
    autonomy=1,
    daily_limit=3,
    value_limit=5_000_000,
    window="08:00-18:00",
    days="mon-fri",
)


def at(h: int, m: int = 0, d: date = TODAY) -> datetime:
    return datetime.combine(d, time(h, m), HCM)


def test_reasons_are_part_of_the_tool_contract() -> None:
    from xt.application.agenthr.gate import REASONS as GATE

    assert GATE <= REASONS


# ---- 1/2. trạng thái hồ sơ -----------------------------------------------------------------
@pytest.mark.parametrize(
    ("status", "reason"),
    [("cho_duyet", AGENT_SUSPENDED), ("tam_dung", AGENT_SUSPENDED), ("da_nghi", AGENT_TERMINATED)],
)
def test_not_on_duty_is_blocked(status: str, reason: str) -> None:
    b = status_block(Profile("p", status, "thu_cong" if status == "tam_dung" else None), TODAY)
    assert b is not None and b.reason == reason and b.remediation
    assert b.incident is None and not b.suspend


def test_on_duty_passes_and_expiry_suspends() -> None:
    assert status_block(Profile("p", "dang_lam"), TODAY) is None
    assert status_block(Profile("p", "dang_lam", valid_until=TODAY), TODAY) is None
    b = status_block(Profile("p", "dang_lam", valid_until=date(2026, 10, 20)), TODAY)
    assert b is not None and b.reason == AGENT_SUSPENDED and b.details["status"] == "het_hieu_luc"
    paused = status_block(Profile("p", "tam_dung", "han_muc"), TODAY)
    assert paused is not None and paused.details["pause_reason"] == "han_muc"


# ---- 4. ca làm -------------------------------------------------------------------------------
def test_working_hours_in_tenant_time() -> None:
    assert hours_block(JD, at(8, 0)) is None
    assert hours_block(JD, at(17, 59)) is None
    late = hours_block(JD, at(18, 0))
    assert late is not None and late.reason == OUTSIDE_HOURS
    early = hours_block(JD, at(7, 59))
    assert early is not None and early.reason == OUTSIDE_HOURS
    saturday = hours_block(JD, at(10, 0, date(2026, 10, 24)))
    assert saturday is not None and saturday.reason == OUTSIDE_HOURS
    # 02:00 UTC = 09:00 giờ Việt Nam: tính theo múi giờ tenant
    utc = datetime(2026, 10, 21, 2, 0, tzinfo=ZoneInfo("UTC")).astimezone(HCM)
    assert hours_block(JD, utc) is None


def test_no_schedule_means_any_time_and_bad_schedule_fails_closed() -> None:
    free = JobDescription("j", {}, window=None, days=None)
    assert hours_block(free, at(3, 0)) is None
    assert hours_block(None, at(3, 0)) is None
    bad = JobDescription("j", {}, window="22:00-06:00")
    b = hours_block(bad, at(23, 0))
    assert b is not None and b.reason == OUTSIDE_HOURS and "error" in b.details


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("mon-fri", {0, 1, 2, 3, 4}),
        ("mon,wed,fri", {0, 2, 4}),
        ("mon-wed,sat", {0, 1, 2, 5}),
        ("sun", {6}),
    ],
)
def test_parse_days(text: str, days: set[int]) -> None:
    assert parse_days(text) == frozenset(days)


@pytest.mark.parametrize("text", ["fri-mon", "monday", "mon--fri", "mon-tue-wed"])
def test_parse_days_rejects(text: str) -> None:
    with pytest.raises(ScheduleError):
        parse_days(text)


def test_parse_window() -> None:
    assert parse_window("08:00-18:00") == (time(8), time(18))
    assert parse_window("") is None and parse_days("all") is None and parse_days(None) is None
    for bad in ("8:00-18:00", "18:00-08:00", "08:00-08:00", "24:00-25:00"):
        with pytest.raises(ScheduleError):
            parse_window(bad)


def test_parse_capabilities_rejects_bad_shapes() -> None:
    for bad in (
        None,
        {},
        [{"model": "x"}],
        [{"model": "X", "thao_tac": ["read"]}],
        [{"model": "x", "thao_tac": ["delete"]}],
        [{"model": "x", "thao_tac": []}],
    ):
        with pytest.raises(ValueError):
            parse_capabilities(bad)
    caps = parse_capabilities(
        [{"model": "a", "thao_tac": ["read"]}, {"model": "a", "thao_tac": ["update"]}]
    )
    assert caps == {"a": frozenset({"read", "update"})}
    # dạng chuẩn của hợp đồng: object phẳng với cờ boolean (đi qua schema values của MCP)
    flat = [{"model": "a", "read": True, "create": False, "update": True, "submit": False}]
    assert parse_capabilities(flat) == caps
    assert capability_rows(caps) == [
        {"model": "a", "read": True, "create": False, "update": True, "submit": False}
    ]
    for bad in ([{"model": "a", "read": False}], [{"model": "a", "read": "yes"}],
                [{"model": "a", "read": True, "thao_tac": ["read"]}],
                [{"model": "a", "read": True, "delete": True}]):  # fmt: skip
        with pytest.raises(ValueError):
            parse_capabilities(bad)
    # dạng cũ thao_tac dạng chuỗi vẫn đọc được
    assert parse_capabilities([{"model": "a", "thao_tac": "read, update"}]) == caps
    with pytest.raises(ValueError):
        parse_capabilities([{"model": "a", "thao_tac": "read,approve"}])
    with pytest.raises(ValueError):
        parse_capabilities([{"model": "a", "thao_tac": " , "}])


# ---- ánh xạ tool -> (model, thao tác) -------------------------------------------------------
def test_intent_model() -> None:
    assert intent_model("create_record", {"model_id": "payment_request"}) == (
        "payment_request",
        "create",
    )
    assert intent_model("update_record", {"ref": "payment_request:1f"}) == (
        "payment_request",
        "update",
    )
    assert intent_model("transition_record", {"ref": "task:t1"}) == ("task", "update")
    assert intent_model("submit_request", {"ref": "advance_request:9"}) == (
        "advance_request",
        "submit",
    )
    assert intent_model("create_task", {}) == ("task", "create")
    assert intent_model("notify", {}) == ("notification", "create")
    assert intent_model("decide_request", {"ref": "advance_request:9"}) is None
    assert intent_model("search_records", {}) is None


# ---- 3/5/6/7. lời ghi -------------------------------------------------------------------------
def w(model: str = "payment_request", op: str = "create", *amounts: int) -> Intent:
    return Intent("create_record", model, op, tuple(amounts))  # type: ignore[arg-type]


def test_write_inside_jd_passes() -> None:
    assert check_write(JD, w("payment_request", "create", 4_000_000), 0, [], TODAY) is None
    assert check_write(JD, w("payment_request", "update"), 2, [], TODAY) is None


@pytest.mark.parametrize(
    "intent",
    [w("stock_item", "update"), w("advance_request", "create"), w("ho_so_agent", "update")],
)
def test_outside_job_description(intent: Intent) -> None:
    b = check_write(JD, intent, 0, [], TODAY)
    assert b is not None and b.reason == OUTSIDE_JD and b.incident is None


def test_pack_models_never_writable_even_if_listed() -> None:
    jd = JobDescription("j", parse_capabilities([{"model": "jd_agent", "thao_tac": ["create"]}]))
    b = check_write(jd, w("jd_agent", "create"), 0, [], TODAY)
    assert b is not None and b.reason == OUTSIDE_JD


def test_no_effective_jd_blocks_every_write() -> None:
    b = check_write(None, w(), 0, [], TODAY)
    assert b is not None and b.reason == OUTSIDE_JD and b.details["jd"] is None


def test_over_value_limit_creates_incident_without_pausing() -> None:
    b = check_write(JD, w("payment_request", "create", 50_000_000), 0, [], TODAY)
    assert b is not None and b.reason == OVER_VALUE
    assert b.incident == "vuot_gia_tri" and not b.suspend
    assert b.details == {"value": 50_000_000, "limit": 5_000_000, "model": "payment_request"}
    assert check_write(JD, w("payment_request", "create", 5_000_000), 0, [], TODAY) is None
    unlimited = JobDescription("j", JD.capabilities, value_limit=0)
    assert check_write(unlimited, w("payment_request", "create", 10**12), 0, [], TODAY) is None


def test_daily_limit_blocks_creates_incident_and_pauses() -> None:
    assert check_write(JD, w(), 2, [], TODAY) is None
    b = check_write(JD, w(), 3, [], TODAY)
    assert b is not None and b.reason == DAILY_LIMIT
    assert b.incident == "vuot_han_muc" and b.suspend
    unlimited = JobDescription("j", JD.capabilities, daily_limit=0)
    assert check_write(unlimited, w(), 10_000, [], TODAY) is None


def test_submit_needs_matching_release() -> None:
    sub = w("payment_request", "submit", 1_000_000)
    b = check_write(JD, sub, 0, [], TODAY)
    assert b is not None and b.reason == HELD and b.incident == "giu_nhap"
    assert b.details == {"release": "request.submit:payment_request"}
    wrong = [Permit("request.submit:advance_request")]
    assert check_write(JD, sub, 0, wrong, TODAY) is not None
    assert check_write(JD, sub, 0, [Permit("request.submit:payment_request")], TODAY) is None
    assert check_write(JD, sub, 0, [Permit("request.submit:*")], TODAY) is None
    future = [Permit("request.submit:payment_request", since=date(2026, 10, 22))]
    assert check_write(JD, sub, 0, future, TODAY) is not None
    expired = [Permit("request.submit:payment_request", until=date(2026, 10, 20))]
    assert check_write(JD, sub, 0, expired, TODAY) is not None


def test_order_value_before_limit_before_release() -> None:
    sub = w("payment_request", "submit", 50_000_000)
    b = check_write(JD, sub, 3, [], TODAY)
    assert b is not None and b.reason == OVER_VALUE
    b = check_write(JD, w("payment_request", "submit", 1), 3, [], TODAY)
    assert b is not None and b.reason == DAILY_LIMIT
