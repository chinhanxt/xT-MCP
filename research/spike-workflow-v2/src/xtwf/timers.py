# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Timer start (G-P2): lịch rrule theo múi giờ IANA + poller idempotent theo kỳ.

Thiết kế:
- Kỳ được định danh bằng `period_key` = thời điểm kích hoạt theo giờ địa phương (ISO, có offset), ví dụ
  `2026-10-19T08:00:00+07:00`. Khóa duy nhất là (process_key, period_key) — KHÔNG gồm version, nên kích hoạt
  version mới giữa kỳ không làm chạy lại kỳ đó.
- Mỗi lần quét, trong MỘT transaction: tính kỳ đến hạn gần nhất ≤ now (và ≥ not_before của định nghĩa) ->
  `INSERT ... ON CONFLICT DO NOTHING` vào `wf_timer_fire` -> chỉ khi chèn được (rowcount=1) mới tạo instance
  (ghi wf_instance cùng transaction) -> commit. Tạo instance lỗi -> rollback, kỳ chưa bị đánh dấu, lần quét sau thử lại.
- Nhiều worker chạy song song: chỉ một transaction chèn được dòng kỳ (khóa duy nhất), các worker khác rowcount=0.
- catchup=latest: worker chết nhiều kỳ -> chỉ chạy kỳ gần nhất; catchup=none: kỳ trễ quá `grace` bị ghi `skipped`.
- BPMN có timer start event `timeDate = xt_fire_at`: nếu poller tạo instance sớm, engine vẫn chờ tới đúng giờ.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr

from .engine import SafeEnvironment
from .spec import TimerTrigger, duration_seconds

DDL = [
    """CREATE TABLE IF NOT EXISTS wf_timer_fire (
        process_key TEXT NOT NULL, period_key TEXT NOT NULL, status TEXT NOT NULL,
        version INTEGER NOT NULL, instance_id TEXT, fired_at TEXT NOT NULL,
        PRIMARY KEY (process_key, period_key))""",
    """CREATE TABLE IF NOT EXISTS wf_instance (
        id TEXT PRIMARY KEY, process_key TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL)""",
]


EPOCH = datetime(2026, 1, 5, 0, 0)


class Schedule:
    def __init__(self, trig: TimerTrigger, anchor: datetime):
        self.trig = trig
        self.tz = ZoneInfo(trig.tz)
        # dtstart là MỐC CỐ ĐỊNH (thứ Hai 05/01/2026 00:00 giờ địa phương), không phụ thuộc ngày kích hoạt/phiên bản:
        # INTERVAL>1 giữ đúng pha khi kích hoạt version mới. `anchor` chỉ còn dùng cho not_before.
        self.dtstart = EPOCH
        self.rule = rrulestr(trig.rrule + ";BYSECOND=0", dtstart=self.dtstart)

    def _aware(self, naive):
        # giờ địa phương -> aware; giờ rơi vào khoảng DST nhảy được chuẩn hóa qua UTC
        return naive.replace(tzinfo=self.tz).astimezone(timezone.utc).astimezone(self.tz)

    def latest_at_or_before(self, now: datetime):
        n = self.rule.before(now.astimezone(self.tz).replace(tzinfo=None), inc=True)
        return self._aware(n) if n else None

    def previous(self, occ: datetime):
        n = self.rule.before(occ.astimezone(self.tz).replace(tzinfo=None), inc=False)
        return self._aware(n) if n else None

    def next_after(self, t: datetime):
        n = self.rule.after(t.astimezone(self.tz).replace(tzinfo=None), inc=False)
        return self._aware(n) if n else None

    def fire_context(self, occ: datetime) -> dict:
        prev = self.previous(occ)
        return {"fire_at": occ.isoformat(), "fire_date": occ.date().isoformat(),
                "period_start": prev.isoformat() if prev else None,
                "period_start_date": prev.date().isoformat() if prev else None,
                "period_end_date": (occ.date() - timedelta(days=1)).isoformat()}


def validate_fire(trig: TimerTrigger, fire) -> None:
    """Chống giả mạo kỳ: fire_at phải là ISO có múi giờ, đúng một lần kích hoạt của lịch, và period_key == fire_at."""
    from .runtime import DecisionError
    try:
        fa = datetime.fromisoformat(fire["fire_at"])
    except (TypeError, ValueError, KeyError):
        raise DecisionError("fire_at không phải thời điểm ISO hợp lệ") from None
    if fa.tzinfo is None or fire.get("period_key") != fire["fire_at"]:
        raise DecisionError("fire không hợp lệ (thiếu múi giờ hoặc period_key khác fire_at)")
    occ = Schedule(trig, fa).latest_at_or_before(fa)
    if occ is None or occ.timestamp() != fa.timestamp():   # so sánh theo UTC: PEP 495 coi giờ mập mờ (fold) khác zone là "không bằng"
        raise DecisionError(f"fire_at {fire['fire_at']} không phải một lần kích hoạt của lịch {trig.rrule}")


def eval_inputs(trig: TimerTrigger, ctx: dict) -> dict:
    env = SafeEnvironment({})
    return {k: env.evaluate(e, dict(ctx)) for k, e in trig.input.items()}


class TimerPoller:
    """`definitions`: list of dict(process_key, version, runtime, not_before). `conn`: DB-API (sqlite3 hoặc psycopg)."""

    def __init__(self, conn, definitions, ph="?"):
        self.conn, self.defs, self.ph = conn, definitions, ph

    def _q(self, sql):
        return sql.replace("?", self.ph)

    def ensure_schema(self):
        cur = self.conn.cursor()
        for d in DDL:
            cur.execute(d)
        self.conn.commit()

    def poll(self, now: datetime) -> list[dict]:
        results = []
        for d in self.defs:
            rt = d["runtime"]
            trig = rt.c.spec.trigger
            try:
                sch = Schedule(trig, d["not_before"])
                occ = sch.latest_at_or_before(now)
            except Exception as e:  # noqa: BLE001
                results.append({"process_key": d.get("process_key"), "result": "error", "error": f"{type(e).__name__}: {e}"[:300]})
                continue
            if occ is None or occ.timestamp() < d["not_before"].timestamp():
                continue
            pk = occ.isoformat()
            late = (now - occ).total_seconds() > duration_seconds(trig.grace)
            status = "skipped" if (late and trig.catchup == "none") else "fired"
            cur = self.conn.cursor()
            try:
                cur.execute(self._q("INSERT INTO wf_timer_fire(process_key, period_key, status, version, fired_at) VALUES (?,?,?,?,?) "
                                    "ON CONFLICT (process_key, period_key) DO NOTHING"),
                            (d["process_key"], pk, status, rt.c.manifest["version"], now.isoformat()))
                if cur.rowcount != 1:
                    self.conn.rollback()
                    results.append({"process_key": d["process_key"], "period_key": pk, "result": "already"})
                    continue
                iid = None
                if status == "fired":
                    ctx = sch.fire_context(occ)
                    inst = rt.start(eval_inputs(trig, ctx), fire={"fire_at": ctx["fire_at"], "period_key": pk})
                    iid = inst.meta["id"]
                    import json
                    cur.execute(self._q("INSERT INTO wf_instance(id, process_key, version, state) VALUES (?,?,?,?)"),
                                (iid, d["process_key"], rt.c.manifest["version"], json.dumps(inst.to_state(), ensure_ascii=False)))
                    cur.execute(self._q("UPDATE wf_timer_fire SET instance_id=? WHERE process_key=? AND period_key=?"), (iid, d["process_key"], pk))
                self.conn.commit()
                results.append({"process_key": d["process_key"], "period_key": pk, "result": status, "instance_id": iid})
            except Exception as e:  # noqa: BLE001  một định nghĩa lỗi không chặn các định nghĩa khác
                self.conn.rollback()
                results.append({"process_key": d["process_key"], "period_key": pk, "result": "error", "error": f"{type(e).__name__}: {e}"[:300]})
        return results
