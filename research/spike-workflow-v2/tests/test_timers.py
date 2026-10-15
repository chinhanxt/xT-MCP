# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""G-P2: timer start định kỳ, poller idempotent theo kỳ (sqlite + Postgres thật nếu có XT_PG_DSN)."""
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from conftest import load_pack
from xtwf.runtime import DecisionError
from xtwf.spec import parse_spec
from xtwf.timers import Schedule, TimerPoller

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
NOT_BEFORE = datetime(2026, 10, 1, tzinfo=HCM)


def at(y, m, d, h, mi=0, tz=HCM):
    return datetime(y, m, d, h, mi, tzinfo=tz)


def sqlite_conn(path):
    c = sqlite3.connect(path, timeout=30, isolation_level="DEFERRED", check_same_thread=False)
    return c


@pytest.fixture
def digest(make_rt):
    return make_rt("ban_tin_tuan")


def poller(conn, rt, ph="?", version_key="weekly_digest", not_before=NOT_BEFORE):
    p = TimerPoller(conn, [{"process_key": version_key, "runtime": rt, "not_before": not_before}], ph=ph)
    p.ensure_schema()
    return p


def rows(conn):
    return conn.execute("SELECT period_key, status, instance_id IS NOT NULL FROM wf_timer_fire ORDER BY period_key").fetchall()


def test_schedule_weekly_monday_0800_hcm():
    trig = parse_spec(load_pack("ban_tin_tuan")).trigger
    s = Schedule(trig, NOT_BEFORE)
    occ = s.latest_at_or_before(at(2026, 10, 19, 8, 5))
    assert occ.isoformat() == "2026-10-19T08:00:00+07:00"
    assert s.latest_at_or_before(at(2026, 10, 19, 7, 59)).isoformat() == "2026-10-12T08:00:00+07:00"
    assert s.next_after(occ).isoformat() == "2026-10-26T08:00:00+07:00"
    ctx = s.fire_context(occ)
    assert (ctx["period_start_date"], ctx["period_end_date"], ctx["fire_date"]) == ("2026-10-12", "2026-10-18", "2026-10-19")


def test_poller_idempotent_per_period(tmp_path, digest, services, clock):
    conn = sqlite_conn(tmp_path / "t.db")
    p = poller(conn, digest)
    clock.set(at(2026, 10, 19, 8, 5))
    r1 = p.poll(at(2026, 10, 19, 8, 5))
    assert r1[0]["result"] == "fired" and r1[0]["period_key"] == "2026-10-19T08:00:00+07:00"
    assert p.poll(at(2026, 10, 19, 8, 6))[0]["result"] == "already"
    other = poller(sqlite_conn(tmp_path / "t.db"), digest)               # worker thứ hai, connection khác
    assert other.poll(at(2026, 10, 19, 23, 0))[0]["result"] == "already"
    assert services.calls == [("weekly_rollup", "2026-10-12", "2026-10-18")]
    assert services.notes == ["Tuần 2026-10-12..2026-10-18"]
    clock.set(at(2026, 10, 26, 8, 1))
    assert p.poll(at(2026, 10, 26, 8, 1))[0]["result"] == "fired"
    assert rows(conn) == [("2026-10-19T08:00:00+07:00", "fired", 1), ("2026-10-26T08:00:00+07:00", "fired", 1)]
    assert conn.execute("SELECT count(*) FROM wf_instance").fetchone()[0] == 2


def test_poller_concurrent_workers_sqlite(tmp_path, digest, services, clock):
    poller(sqlite_conn(tmp_path / "c.db"), digest)
    clock.set(at(2026, 10, 19, 8, 2))
    results, errs = [], []

    def work():
        try:
            results.extend(poller(sqlite_conn(tmp_path / "c.db"), digest).poll(at(2026, 10, 19, 8, 2)))
        except Exception as e:      # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs
    assert sorted(r["result"] for r in results) == ["already"] * 7 + ["fired"]
    assert len(services.calls) == 1


def test_poller_catchup_latest_after_downtime(tmp_path, digest, services, clock):
    conn = sqlite_conn(tmp_path / "l.db")
    p = poller(conn, digest, not_before=datetime(2026, 9, 1, tzinfo=HCM))
    clock.set(at(2026, 10, 20, 9))
    r = p.poll(at(2026, 10, 20, 9))                                      # worker chết từ đầu tháng 9
    assert r[0]["result"] == "fired" and r[0]["period_key"] == "2026-10-19T08:00:00+07:00"
    assert len(rows(conn)) == 1 and len(services.calls) == 1


def test_poller_catchup_none_skips_late_period(tmp_path, make_rt, services, clock):
    spec = load_pack("ban_tin_tuan")
    spec["trigger"]["catchup"] = "none"
    rt = make_rt(spec)
    conn = sqlite_conn(tmp_path / "n.db")
    p = poller(conn, rt)
    assert p.poll(at(2026, 10, 19, 10, 30))[0]["result"] == "skipped"     # trễ 2h30 > grace PT1H
    assert p.poll(at(2026, 10, 19, 10, 31))[0]["result"] == "already"
    assert services.calls == [] and rows(conn) == [("2026-10-19T08:00:00+07:00", "skipped", 0)]


def test_poller_failure_rolls_back_and_retries(tmp_path, digest, services, clock):
    conn = sqlite_conn(tmp_path / "f.db")
    p = poller(conn, digest)
    ok = services["weekly_rollup"]

    def down(**kw):
        raise ConnectionError("db báo cáo tạm thời không truy cập được")
    services["weekly_rollup"] = down
    clock.set(at(2026, 10, 19, 8, 1))
    r = p.poll(at(2026, 10, 19, 8, 1))
    assert r[0]["result"] == "error" and "không truy cập được" in r[0]["error"]
    assert rows(conn) == [] and conn.execute("SELECT count(*) FROM wf_instance").fetchone()[0] == 0
    services["weekly_rollup"] = ok
    assert p.poll(at(2026, 10, 19, 8, 2))[0]["result"] == "fired"


def test_new_version_does_not_refire_same_period(tmp_path, make_rt, services, clock):
    conn = sqlite_conn(tmp_path / "v.db")
    clock.set(at(2026, 10, 19, 8, 1))
    assert poller(conn, make_rt("ban_tin_tuan")).poll(at(2026, 10, 19, 8, 1))[0]["result"] == "fired"
    v3 = load_pack("ban_tin_tuan")
    v3["version"] = 3
    assert poller(conn, make_rt(v3)).poll(at(2026, 10, 19, 9, 0))[0]["result"] == "already"
    assert len(services.calls) == 1


def test_timer_start_event_waits_until_fire_time(digest, services, clock):
    clock.set(at(2026, 10, 19, 7, 50))                                    # poller chạy sớm 10 phút
    i = digest.start({"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""},
                     fire={"fire_at": "2026-10-19T08:00:00+07:00", "period_key": "2026-10-19T08:00:00+07:00"})
    i = digest.load(i.to_state())
    i.tick()
    assert i.status() == "running" and services.calls == []
    clock.advance(minutes=11)
    i.tick()
    assert i.status() == "completed" and len(services.calls) == 1


def test_timer_process_cannot_be_started_manually(digest):
    with pytest.raises(DecisionError, match="poller"):
        digest.start({"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""}, "NV-0001")
    with pytest.raises(DecisionError, match="poller"):
        digest.start({"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""})


def test_dst_gap_and_overlap_fire_once(tmp_path, make_rt, services, clock):
    spec = load_pack("ban_tin_tuan")
    spec["trigger"].update(rrule="FREQ=DAILY;BYHOUR=2;BYMINUTE=30", tz="Europe/Berlin")
    rt = make_rt(spec)
    ber = ZoneInfo("Europe/Berlin")
    s = Schedule(rt.c.spec.trigger, datetime(2026, 3, 1, tzinfo=ber))
    gap = s.latest_at_or_before(datetime(2026, 3, 29, 12, tzinfo=ber))
    assert gap.isoformat() == "2026-03-29T03:30:00+02:00"                 # 02:30 không tồn tại -> chuẩn hóa
    conn = sqlite_conn(tmp_path / "d.db")
    p = poller(conn, rt, not_before=datetime(2026, 10, 20, tzinfo=ber))
    clock.set(datetime(2026, 10, 25, 3, 0, tzinfo=ber))
    first = p.poll(datetime(2026, 10, 25, 2, 40, fold=1, tzinfo=ber))    # 02:40 lần thứ hai (giờ mùa đông)
    again = p.poll(datetime(2026, 10, 25, 4, 0, tzinfo=ber))
    assert first[0]["result"] == "fired", first
    assert again[0]["result"] == "already"
    assert len(rows(conn)) == 1


@pytest.mark.skipif(not os.environ.get("XT_PG_DSN"), reason="cần XT_PG_DSN (Postgres thật)")
def test_poller_concurrent_workers_postgres(digest, services, clock):
    import psycopg
    dsn = os.environ["XT_PG_DSN"]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS wf_timer_fire, wf_instance")
    poller(psycopg.connect(dsn), digest, ph="%s")
    clock.set(at(2026, 10, 19, 8, 3))
    barrier, results, errs = threading.Barrier(8), [], []

    def work():
        try:
            p = poller(psycopg.connect(dsn), digest, ph="%s")
            barrier.wait()
            results.extend(p.poll(at(2026, 10, 19, 8, 3)))
        except Exception as e:      # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs, errs
    assert sorted(r["result"] for r in results) == ["already"] * 7 + ["fired"]
    with psycopg.connect(dsn) as c:
        assert c.execute("SELECT count(*) FROM wf_instance").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM wf_timer_fire").fetchone()[0] == 1
