# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lịch timer start (G-P2): rrule rút gọn + múi giờ IANA, kỳ kích hoạt và biến kỳ.

- Kỳ định danh bằng `period_key` = thời điểm kích hoạt theo giờ địa phương (ISO, có offset),
  ví dụ `2026-10-19T08:00:00+07:00`. Khóa duy nhất (process_key, period_key) KHÔNG gồm version.
- `dtstart` là mốc cố định (thứ Hai 05/01/2026 00:00 giờ địa phương): `INTERVAL>1` giữ đúng pha
  khi kích hoạt version mới ở ngày khác.
- DST: giờ không tồn tại được chuẩn hóa qua UTC; so sánh kỳ luôn theo timestamp UTC (PEP 495
  coi giờ mập mờ có `fold` khác zone là "không bằng").
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rrulestr

from xt.domain.process.errors import DecisionError
from xt.domain.process.expr import evaluate
from xt.domain.process.spec import TimerTrigger

EPOCH = datetime(2026, 1, 5, 0, 0)


class Schedule:
    def __init__(self, trig: TimerTrigger):
        self.trig = trig
        self.tz = ZoneInfo(trig.tz)
        rule = rrulestr(trig.rrule + ";BYSECOND=0", dtstart=EPOCH)
        if not isinstance(rule, rrule):  # pragma: no cover - validate_rrule chỉ cho 1 RRULE
            raise DecisionError("rrule không hợp lệ")
        self.rule = rule

    def _aware(self, naive: datetime) -> datetime:
        return naive.replace(tzinfo=self.tz).astimezone(UTC).astimezone(self.tz)

    def _local(self, t: datetime) -> datetime:
        if t.tzinfo is None:
            raise DecisionError("thời điểm phải có múi giờ")
        return t.astimezone(self.tz).replace(tzinfo=None)

    def latest_at_or_before(self, now: datetime) -> datetime | None:
        n = self.rule.before(self._local(now), inc=True)
        return self._aware(n) if n else None

    def previous(self, occ: datetime) -> datetime | None:
        n = self.rule.before(self._local(occ), inc=False)
        return self._aware(n) if n else None

    def next_after(self, t: datetime) -> datetime | None:
        n = self.rule.after(self._local(t), inc=False)
        return self._aware(n) if n else None

    def fire_context(self, occ: datetime) -> dict[str, Any]:
        """Biến kỳ cho `trigger.input`: fire_at, fire_date, period_start(_date), period_end_date."""
        prev = self.previous(occ)
        return {
            "fire_at": occ.isoformat(),
            "fire_date": occ.date().isoformat(),
            "period_start": prev.isoformat() if prev else None,
            "period_start_date": prev.date().isoformat() if prev else None,
            "period_end_date": (occ.date() - timedelta(days=1)).isoformat(),
        }


def validate_fire(trig: TimerTrigger, fire: Mapping[str, Any]) -> None:
    """Chống giả mạo kỳ: `fire_at` ISO có múi giờ, đúng một lần kích hoạt, period_key == fire_at."""
    raw = fire.get("fire_at") if isinstance(fire, Mapping) else None
    try:
        fa = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        fa = None
    if fa is None:
        raise DecisionError("fire_at không phải thời điểm ISO hợp lệ", code="invalid_request")
    if fa.tzinfo is None or fire.get("period_key") != raw:
        raise DecisionError(
            "fire không hợp lệ (thiếu múi giờ hoặc period_key khác fire_at)", code="invalid_request"
        )
    occ = Schedule(trig).latest_at_or_before(fa)
    if occ is None or occ.timestamp() != fa.timestamp():
        raise DecisionError(
            f"fire_at {raw} không phải một lần kích hoạt của lịch {trig.rrule}",
            code="invalid_request",
        )


def timer_inputs(trig: TimerTrigger, ctx: Mapping[str, Any]) -> dict[str, Any]:
    """Tính input của instance từ biến kỳ bằng engine biểu thức an toàn."""
    return {k: evaluate(e, dict(ctx)) for k, e in trig.input.items()}
