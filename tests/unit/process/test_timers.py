# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""G-P2: timer start định kỳ, poller idempotent theo kỳ (sqlite; Postgres thật ở integration)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.unit.process.conftest import MakeRt
from tests.unit.process.support import Clock, Services, load_pack
from xt.adapters.db.process_timers import DbApiTimerFireStore
from xt.application.process.memory import InMemoryTimerFireStore
from xt.application.process.runtime import ProcessRuntime
from xt.application.process.timers import TimerDefinition, TimerPoller
from xt.domain.process.errors import DecisionError
from xt.domain.process.schedule import Schedule
from xt.domain.process.spec import TimerTrigger, parse_spec

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
NOT_BEFORE = datetime(2026, 10, 1, tzinfo=HCM)


def at(y: int, m: int, d: int, h: int, mi: int = 0, tz: ZoneInfo = HCM) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=tz)


_OPEN: list[sqlite3.Connection] = []


@pytest.fixture(autouse=True)
def _close_connections() -> Iterator[None]:
    yield
    while _OPEN:
        _OPEN.pop().close()


def sqlite_conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=30, isolation_level="DEFERRED", check_same_thread=False)
    _OPEN.append(c)
    return c


def trigger(spec: dict[str, Any]) -> TimerTrigger:
    t = parse_spec(spec).trigger
    assert isinstance(t, TimerTrigger)
    return t


@pytest.fixture
def digest(make_rt: MakeRt) -> ProcessRuntime:
    return make_rt("ban_tin_tuan")


def poller(
    conn: Any,
    rt: ProcessRuntime,
    dialect: Any = "sqlite",
    key: str = "weekly_digest",
    not_before: datetime = NOT_BEFORE,
) -> TimerPoller:
    store = DbApiTimerFireStore(conn, dialect)
    store.ensure_schema()
    return TimerPoller(store, [TimerDefinition(key, rt, not_before)])


def rows(conn: sqlite3.Connection) -> list[Any]:
    return conn.execute(
        "SELECT period_key, status, instance_id IS NOT NULL FROM wf_timer_fire ORDER BY period_key"
    ).fetchall()


def test_schedule_weekly_monday_0800_hcm() -> None:
    s = Schedule(trigger(load_pack("ban_tin_tuan")))
    occ = s.latest_at_or_before(at(2026, 10, 19, 8, 5))
    assert occ is not None and occ.isoformat() == "2026-10-19T08:00:00+07:00"
    prev = s.latest_at_or_before(at(2026, 10, 19, 7, 59))
    assert prev is not None and prev.isoformat() == "2026-10-12T08:00:00+07:00"
    nxt = s.next_after(occ)
    assert nxt is not None and nxt.isoformat() == "2026-10-26T08:00:00+07:00"
    ctx = s.fire_context(occ)
    assert (ctx["period_start_date"], ctx["period_end_date"], ctx["fire_date"]) == (
        "2026-10-12", "2026-10-18", "2026-10-19",
    )  # fmt: skip


def test_poller_idempotent_per_period(
    tmp_path: Path, digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    conn = sqlite_conn(tmp_path / "t.db")
    p = poller(conn, digest)
    clock.set(at(2026, 10, 19, 8, 5))
    r1 = p.poll(at(2026, 10, 19, 8, 5))
    assert r1[0]["result"] == "fired" and r1[0]["period_key"] == "2026-10-19T08:00:00+07:00"
    assert p.poll(at(2026, 10, 19, 8, 6))[0]["result"] == "already"
    other = poller(sqlite_conn(tmp_path / "t.db"), digest)  # worker thứ hai, connection khác
    assert other.poll(at(2026, 10, 19, 23, 0))[0]["result"] == "already"
    assert services.calls == [("weekly_rollup", "2026-10-12", "2026-10-18")]
    assert services.notes == ["Tuần 2026-10-12..2026-10-18"]
    clock.set(at(2026, 10, 26, 8, 1))
    assert p.poll(at(2026, 10, 26, 8, 1))[0]["result"] == "fired"
    assert rows(conn) == [
        ("2026-10-19T08:00:00+07:00", "fired", 1), ("2026-10-26T08:00:00+07:00", "fired", 1)
    ]  # fmt: skip
    assert conn.execute("SELECT count(*) FROM wf_instance").fetchone()[0] == 2


def test_poller_concurrent_workers_sqlite(
    tmp_path: Path, digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    poller(sqlite_conn(tmp_path / "c.db"), digest)
    clock.set(at(2026, 10, 19, 8, 2))
    results: list[dict[str, Any]] = []
    errs: list[BaseException] = []

    def work() -> None:
        try:
            results.extend(
                poller(sqlite_conn(tmp_path / "c.db"), digest).poll(at(2026, 10, 19, 8, 2))
            )
        except Exception as e:  # pragma: no cover
            errs.append(e)

    ts = [threading.Thread(target=work) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs
    assert sorted(r["result"] for r in results) == ["already"] * 7 + ["fired"]
    assert len(services.calls) == 1


def test_poller_catchup_latest_after_downtime(
    tmp_path: Path, digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    conn = sqlite_conn(tmp_path / "l.db")
    p = poller(conn, digest, not_before=datetime(2026, 9, 1, tzinfo=HCM))
    clock.set(at(2026, 10, 20, 9))
    r = p.poll(at(2026, 10, 20, 9))  # worker chết từ đầu tháng 9
    assert r[0]["result"] == "fired" and r[0]["period_key"] == "2026-10-19T08:00:00+07:00"
    assert len(rows(conn)) == 1 and len(services.calls) == 1


def test_poller_catchup_none_skips_late_period(
    tmp_path: Path, make_rt: MakeRt, services: Services, clock: Clock
) -> None:
    spec = load_pack("ban_tin_tuan")
    spec["trigger"]["catchup"] = "none"
    conn = sqlite_conn(tmp_path / "n.db")
    p = poller(conn, make_rt(spec))
    assert p.poll(at(2026, 10, 19, 10, 30))[0]["result"] == "skipped"  # trễ 2h30 > grace PT1H
    assert p.poll(at(2026, 10, 19, 10, 31))[0]["result"] == "already"
    assert services.calls == [] and rows(conn) == [("2026-10-19T08:00:00+07:00", "skipped", 0)]


def test_poller_failure_rolls_back_and_retries(
    tmp_path: Path, digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    conn = sqlite_conn(tmp_path / "f.db")
    p = poller(conn, digest)
    ok = services["weekly_rollup"]

    def down(**kw: Any) -> None:
        raise ConnectionError("db báo cáo tạm thời không truy cập được")

    services["weekly_rollup"] = down
    clock.set(at(2026, 10, 19, 8, 1))
    r = p.poll(at(2026, 10, 19, 8, 1))
    assert r[0]["result"] == "error" and "không truy cập được" in r[0]["error"]
    assert rows(conn) == [] and conn.execute("SELECT count(*) FROM wf_instance").fetchone()[0] == 0
    services["weekly_rollup"] = ok
    assert p.poll(at(2026, 10, 19, 8, 2))[0]["result"] == "fired"


def test_new_version_does_not_refire_same_period(
    tmp_path: Path, make_rt: MakeRt, services: Services, clock: Clock
) -> None:
    conn = sqlite_conn(tmp_path / "v.db")
    clock.set(at(2026, 10, 19, 8, 1))
    assert (
        poller(conn, make_rt("ban_tin_tuan")).poll(at(2026, 10, 19, 8, 1))[0]["result"] == "fired"
    )
    v3 = load_pack("ban_tin_tuan")
    v3["version"] = 3
    assert poller(conn, make_rt(v3)).poll(at(2026, 10, 19, 9, 0))[0]["result"] == "already"
    assert len(services.calls) == 1


def test_timer_start_event_waits_until_fire_time(
    digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    clock.set(at(2026, 10, 19, 7, 50))  # poller chạy sớm 10 phút
    fire = {"fire_at": "2026-10-19T08:00:00+07:00", "period_key": "2026-10-19T08:00:00+07:00"}
    i = digest.start(
        {"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""}, fire=fire
    )
    i = digest.load(i.to_state())
    i.tick()
    assert i.status() == "running" and services.calls == []
    assert i.wf.next_due() == datetime.fromisoformat(fire["fire_at"])  # để hẹn đánh thức
    clock.advance(minutes=11)
    i.tick()
    assert i.status() == "completed" and len(services.calls) == 1


def test_timer_process_cannot_be_started_manually(digest: ProcessRuntime) -> None:
    data = {"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""}
    with pytest.raises(DecisionError, match="poller"):
        digest.start(data, "NV-0001")
    with pytest.raises(DecisionError, match="poller"):
        digest.start(data)


def test_dst_gap_and_overlap_fire_once(
    tmp_path: Path, make_rt: MakeRt, services: Services, clock: Clock
) -> None:
    spec = load_pack("ban_tin_tuan")
    spec["trigger"].update(rrule="FREQ=DAILY;BYHOUR=2;BYMINUTE=30", tz="Europe/Berlin")
    rt = make_rt(spec)
    ber = ZoneInfo("Europe/Berlin")
    gap = Schedule(trigger(spec)).latest_at_or_before(datetime(2026, 3, 29, 12, tzinfo=ber))
    assert gap is not None and gap.isoformat() == "2026-03-29T03:30:00+02:00"  # 02:30 không tồn tại
    conn = sqlite_conn(tmp_path / "d.db")
    p = poller(conn, rt, not_before=datetime(2026, 10, 20, tzinfo=ber))
    clock.set(datetime(2026, 10, 25, 3, 0, tzinfo=ber))
    first = p.poll(datetime(2026, 10, 25, 2, 40, fold=1, tzinfo=ber))  # 02:40 lần hai (giờ đông)
    again = p.poll(datetime(2026, 10, 25, 4, 0, tzinfo=ber))
    assert first[0]["result"] == "fired", first
    assert again[0]["result"] == "already"
    assert len(rows(conn)) == 1


def test_in_memory_timer_store_same_semantics(
    digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    store = InMemoryTimerFireStore()
    p = TimerPoller(store, [TimerDefinition("weekly_digest", digest, NOT_BEFORE)])
    clock.set(at(2026, 10, 19, 8, 5))
    assert p.poll(at(2026, 10, 19, 8, 5))[0]["result"] == "fired"
    assert p.poll(at(2026, 10, 19, 8, 6))[0]["result"] == "already"
    (fire,) = store.fires.values()
    assert fire["status"] == "fired" and fire["instance_id"] in store.instances
    with pytest.raises(ValueError, match="múi giờ"):
        p.poll(datetime(2026, 10, 19, 8, 6))


def test_poller_starts_through_process_service(
    digest: ProcessRuntime, services: Services, clock: Clock
) -> None:
    """Lớp ghép: instance tạo qua `ProcessService.start(fire=...)` (InstanceStore thật), dòng kỳ
    nối instance; kỳ đã giành -> `already`, không tạo thêm."""
    from xt.application.process.memory import InMemoryInstanceStore
    from xt.application.process.service import InMemoryProcessCatalog, ProcessService

    catalog = InMemoryProcessCatalog()
    catalog.add(digest)
    instances = InMemoryInstanceStore()
    svc = ProcessService(catalog, instances)
    store = InMemoryTimerFireStore()
    pid = digest.compiled.process_id
    p = TimerPoller(store, [TimerDefinition(pid, digest, NOT_BEFORE)], svc)
    clock.set(at(2026, 10, 19, 8, 5))
    first = p.poll(at(2026, 10, 19, 8, 5))
    assert first[0]["result"] == "fired", first
    assert p.poll(at(2026, 10, 19, 8, 7))[0]["result"] == "already"
    (fire,) = store.fires.values()
    assert fire["instance_id"] == first[0]["instance_id"]
    assert store.instances == {}  # không đi đường `tx.save_instance`
    rec = instances.get_for_update(first[0]["instance_id"])
    assert rec is not None and rec.process_id == pid
    with pytest.raises(DecisionError):  # quy trình định kỳ không nhận người khởi tạo
        svc.start(pid, {}, "u-1", fire={"fire_at": "x", "period_key": "x"})
