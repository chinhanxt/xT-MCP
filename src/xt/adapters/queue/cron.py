# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Conservative conversion of prospective wall-clock schedules to UTC cron.

Supported: five fields; a single numeric minute and hour; unrestricted day of
month/month; and either ``*`` or a comma-separated numeric weekday list (0/7 is
Sunday). Calendar constraints and richer expressions need a timezone-aware
scheduler and are rejected rather than approximated.

The installed TZif v2+ data must prove a constant offset from conversion time
onward: a fixed-offset POSIX footer, matching the current offset and every
future transition. This is an unbounded proof under that version of tzdata,
not seasonal sampling. Historical offsets are outside this API's contract.
Recreate schedules after tzdata updates: future civil-time legislation cannot
be predicted. TZif layout: https://www.rfc-editor.org/rfc/rfc9636.html#section-3
"""

from __future__ import annotations

import re
import struct
import zoneinfo
from datetime import UTC, datetime
from importlib import resources
from io import BytesIO
from pathlib import Path

_NUMBER = re.compile(r"[0-9]{1,2}")
_FIXED_TZ = re.compile(
    rb"(?:[A-Za-z]{3,}|<[A-Za-z0-9+-]{3,}>)([+-]?)([0-9]{1,3})"
    rb"(?::([0-9]{1,2})(?::([0-9]{1,2}))?)?"
)
_HEADER = struct.Struct(">4sc15x6I")


def cron_to_utc(cron: str, timezone: str) -> str:
    """Return an equivalent future UTC cron, or raise ``ValueError``.

    In particular, ``0 8 * * 1`` in ``Asia/Ho_Chi_Minh`` becomes
    ``0 1 * * 1``. Fixed offsets containing seconds cannot be represented.
    """
    if not isinstance(cron, str):
        raise ValueError("cron must be a five-field string")
    fields = cron.split()
    if len(fields) != 5:
        raise ValueError("cron must have exactly five fields")
    minute_text, hour_text, month_day, month, weekday = fields
    minute = _number(minute_text, 59)
    hour = _number(hour_text, 23)
    if month_day != "*" or month != "*":
        raise ValueError("day-of-month and month must be unrestricted (*)")
    weekdays = None if weekday == "*" else {_number(day, 7) % 7 for day in weekday.split(",")}

    offset = _fixed_future_offset(timezone)
    day_shift, utc_minutes = divmod(hour * 60 + minute - offset, 24 * 60)
    utc_hour, utc_minute = divmod(utc_minutes, 60)
    utc_weekday = (
        "*"
        if weekdays is None
        else ",".join(str(day) for day in sorted({(day + day_shift) % 7 for day in weekdays}))
    )
    return f"{utc_minute} {utc_hour} * * {utc_weekday}"


def _number(value: str, maximum: int) -> int:
    if not _NUMBER.fullmatch(value) or not 0 <= int(value) <= maximum:
        raise ValueError(f"unsupported cron field: {value!r}")
    return int(value)


def _read_tzif(key: str) -> bytes:
    # Match ZoneInfo key restrictions before treating a key as a relative path.
    if (
        not isinstance(key, str)
        or not key
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ValueError("timezone must be a normalized IANA timezone key")
    for root in zoneinfo.TZPATH:
        path = Path(root).joinpath(*key.split("/"))
        try:
            return path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError(f"cannot read timezone data for {key!r}") from exc
    try:
        return resources.files("tzdata.zoneinfo").joinpath(*key.split("/")).read_bytes()
    except (ImportError, OSError) as exc:
        raise ValueError(f"unknown IANA timezone: {key!r}") from exc


def _block(data: bytes, start: int, width: int) -> tuple[int, int, int, int]:
    """Return data start, end, transition count and type count for a TZif block."""
    magic, version, utc_count, std_count, leaps, times, types, chars = _HEADER.unpack_from(
        data, start
    )
    if magic != b"TZif" or version not in {b"2", b"3", b"4"} or not 0 < types <= 256:
        raise ValueError("timezone needs valid TZif v2+ data")
    if leaps:
        raise ValueError("leap-second timezone data is unsupported by UTC cron")
    body = start + _HEADER.size
    end = body + times * (width + 1) + types * 6 + chars + leaps * (width + 4)
    end += std_count + utc_count
    if end > len(data):
        raise ValueError("truncated timezone data")
    return body, end, times, types


def _fixed_future_offset(key: str) -> int:
    data = _read_tzif(key)
    try:
        # Read both blocks, but only the 64-bit block defines future transitions.
        _, first_end, _, _ = _block(data, 0, 4)
        body, end, count, type_count = _block(data, first_end, 8)
        footer = data[end:]
        if not footer.startswith(b"\n") or not footer.endswith(b"\n"):
            raise ValueError("missing timezone future rules")
        rule = _FIXED_TZ.fullmatch(footer[1:-1])
        if rule is None:
            raise ValueError("timezone must have fixed future offset rules; DST is unsupported")
        sign, hours, minutes, seconds = rule.groups()
        h, m, s = int(hours), int(minutes or b"0"), int(seconds or b"0")
        if h > 24 or m > 59 or s > 59:
            raise ValueError("invalid fixed timezone offset")
        # POSIX TZ offsets have the opposite sign to UTC offsets.
        offset = (h * 3600 + m * 60 + s) * (1 if sign == b"-" else -1)
        if offset % 60:
            raise ValueError("timezone offset must be an exact number of minutes")
        zone = zoneinfo.ZoneInfo.from_file(BytesIO(data), key=key)
        now = datetime.now(UTC)
        current = now.astimezone(zone).utcoffset()
        if current is None or current.total_seconds() != offset:
            raise ValueError("timezone offset changes in the future")

        indices = body + count * 8
        types_start = indices + count
        for i in range(count):
            timestamp = struct.unpack_from(">q", data, body + i * 8)[0]
            type_index = data[indices + i]
            if type_index >= type_count:
                raise ValueError("invalid timezone transition type")
            transition_offset = struct.unpack_from(">i", data, types_start + type_index * 6)[0]
            if timestamp >= now.timestamp() and transition_offset != offset:
                raise ValueError("timezone offset changes in the future")
        return offset // 60
    except (struct.error, AssertionError, IndexError, OverflowError) as exc:
        raise ValueError("invalid or unsupported timezone data") from exc
