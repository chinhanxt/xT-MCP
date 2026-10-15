# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from xt.adapters.queue.cron import cron_to_utc


@pytest.mark.parametrize(
    ("cron", "timezone", "expected"),
    [
        ("0 8 * * 1", "Asia/Ho_Chi_Minh", "0 1 * * 1"),
        ("15 2 * * 1,3,5", "Asia/Ho_Chi_Minh", "15 19 * * 0,2,4"),
        ("30 23 * * 6", "Etc/GMT+5", "30 4 * * 0"),
        ("0 0 * * *", "Asia/Kathmandu", "15 18 * * *"),
        ("0 0 * * 0,7,1", "Asia/Ho_Chi_Minh", "0 17 * * 0,6"),
        (" 05\t08 * * 7 ", "UTC", "5 8 * * 0"),
    ],
)
def test_fixed_offset_cron_conversion(cron: str, timezone: str, expected: str) -> None:
    assert cron_to_utc(cron, timezone) == expected


@pytest.mark.parametrize("timezone", ["Europe/Berlin", "America/New_York", "Australia/Lord_Howe"])
def test_rejects_zones_with_future_offset_changes(timezone: str) -> None:
    with pytest.raises(ValueError, match=r"fixed|offset|DST"):
        cron_to_utc("0 8 * * 1", timezone)


@pytest.mark.parametrize(
    "cron",
    [
        "",
        "0 8 * *",
        "0 8 * * * *",
        "@daily",
        "60 8 * * *",
        "0 24 * * *",
        "-1 8 * * *",
        "0 -1 * * *",
        "*/5 8 * * *",
        "0 * * * *",
        "0 8,9 * * *",
        "0 8 1 * *",
        "0 8 * 1 *",
        "0 8 * * 8",
        "0 8 * * 1-5",
        "0 8 * * MON",
        "0 8 * * 1,",
        "0 8 * * 1,,2",
        "\u0660 8 * * *",
    ],
)
def test_rejects_invalid_or_unsupported_cron(cron: str) -> None:
    with pytest.raises(ValueError):
        cron_to_utc(cron, "UTC")


@pytest.mark.parametrize(
    "timezone", ["Missing/Timezone", "../UTC", "/etc/passwd", "", "UTC/../UTC"]
)
def test_rejects_unknown_or_invalid_timezone(timezone: str) -> None:
    with pytest.raises(ValueError):
        cron_to_utc("0 8 * * 1", timezone)


def _tzif(
    offsets: tuple[int, ...] = (0,),
    transitions: tuple[tuple[int, int], ...] = (),
    footer: bytes = b"UTC0",
) -> bytes:
    """A complete TZif v2 fixture, with independently specified transition times."""

    def block(width: int, items: tuple[tuple[int, int], ...]) -> bytes:
        header = b"TZif2" + bytes(15) + struct.pack(">6I", 0, 0, 0, len(items), len(offsets), 4)
        times = b"".join(struct.pack(">q" if width == 8 else ">i", t) for t, _ in items)
        indices = bytes(index for _, index in items)
        types = b"".join(struct.pack(">iBB", offset, 0, 0) for offset in offsets)
        return header + times + indices + types + b"UTC\0"

    return block(4, ()) + block(8, transitions) + b"\n" + footer + b"\n"


def _install_tzif(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: bytes) -> str:
    monkeypatch.setattr("zoneinfo.TZPATH", (str(tmp_path),))
    (tmp_path / "Test").mkdir()
    (tmp_path / "Test" / "Zone").write_bytes(data)
    return "Test/Zone"


def test_rejects_single_future_change_centuries_away(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    change = int(datetime(2400, 1, 1, tzinfo=UTC).timestamp())
    restore = int(datetime(2401, 1, 1, tzinfo=UTC).timestamp())
    timezone = _install_tzif(monkeypatch, tmp_path, _tzif((0, 3600), ((change, 1), (restore, 0))))
    with pytest.raises(ValueError, match=r"offset|fixed"):
        cron_to_utc("0 8 * * *", timezone)


def test_rejects_permanent_future_change(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    change = int(datetime(2400, 1, 1, tzinfo=UTC).timestamp())
    timezone = _install_tzif(
        monkeypatch, tmp_path, _tzif((0, 3600), ((change, 1),), footer=b"XXX-1")
    )
    with pytest.raises(ValueError, match="offset"):
        cron_to_utc("0 8 * * *", timezone)


def test_historical_changes_do_not_block_a_provably_fixed_future(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    change = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp())
    timezone = _install_tzif(
        monkeypatch, tmp_path, _tzif((0, 3600), ((change, 1),), footer=b"XXX-1")
    )
    assert cron_to_utc("0 0 * * 1", timezone) == "0 23 * * 0"


@pytest.mark.parametrize("footer", [b"", b"UTC0DST,M3.2.0,M11.1.0"])
def test_rejects_unproven_or_seasonal_future_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, footer: bytes
) -> None:
    timezone = _install_tzif(monkeypatch, tmp_path, _tzif(footer=footer))
    with pytest.raises(ValueError):
        cron_to_utc("0 8 * * *", timezone)


def test_rejects_offset_with_seconds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    timezone = _install_tzif(monkeypatch, tmp_path, _tzif((30,), footer=b"XXX-0:00:30"))
    with pytest.raises(ValueError, match=r"minute|seconds"):
        cron_to_utc("0 8 * * *", timezone)


def test_rejects_malformed_tzif(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    timezone = _install_tzif(monkeypatch, tmp_path, b"TZif2broken")
    with pytest.raises(ValueError):
        cron_to_utc("0 8 * * *", timezone)


@pytest.mark.parametrize(
    ("cron", "timezone"),
    [
        ("5 2 * * 0,1,6", "Asia/Ho_Chi_Minh"),
        ("0 0 * * *", "Asia/Kathmandu"),
        ("30 23 * * 6", "Etc/GMT+5"),
    ],
)
def test_croniter_next_occurrences_match_local_wall_time(cron: str, timezone: str) -> None:
    from zoneinfo import ZoneInfo

    from croniter import croniter

    base = datetime(2099, 12, 1, tzinfo=UTC)
    local = croniter(cron, base.astimezone(ZoneInfo(timezone)))
    utc = croniter(cron_to_utc(cron, timezone), base)
    # Includes year rollover, February, and 2100's non-leap Gregorian century.
    for _ in range(200):
        assert local.get_next(datetime).astimezone(UTC) == utc.get_next(datetime)


def test_converted_schedule_matches_local_dates_across_gregorian_cycle() -> None:
    """Catch wrong signs, weekday rollover, and leap/month boundary assumptions."""
    from zoneinfo import ZoneInfo

    result = cron_to_utc("5 2 * * 0,1,6", "Asia/Ho_Chi_Minh")
    minute, hour, _, _, weekdays = result.split()
    utc_weekdays = {int(day) for day in weekdays.split(",")}
    zone = ZoneInfo("Asia/Ho_Chi_Minh")
    day = datetime(2400, 1, 1, 2, 5, tzinfo=zone)
    end = datetime(2800, 1, 1, 2, 5, tzinfo=zone)
    while day < end:
        utc = day.astimezone(UTC)
        assert (utc.hour, utc.minute) == (int(hour), int(minute))
        assert ((utc.weekday() + 1) % 7 in utc_weekdays) == ((day.weekday() + 1) % 7 in {0, 1, 6})
        day += timedelta(days=1)
