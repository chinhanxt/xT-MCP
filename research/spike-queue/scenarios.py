# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
"""Chay: python scenarios.py <hand|proc|pgq> [s1 s2 ...]. Can Postgres o cong 15460."""
import asyncio, importlib, json, os, signal, statistics, sys, time, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
impl_name = sys.argv[1]
DBN = {"hand": "q_hand", "proc": "q_proc", "pgq": "q_pgq"}[impl_name]
os.environ["XT_SPIKE_DB"] = DBN
import psycopg
from common import *
M = importlib.import_module(f"impl_{impl_name}")
FILE = M.__file__
TA, TB = str(uuid.uuid4()), str(uuid.uuid4())
def P(key, tid, mode="ok", **kw): return dict(job_key=key, tenant_id=tid, mode=mode, **kw)
def out(*a): print(f"[{impl_name}]", *a, flush=True)
async def conn(): return await psycopg.AsyncConnection.connect(dsn(DBN), autocommit=True)
def q(sql, *a): return admin(DBN, sql, *a)
def wait(cond, timeout=30, step=0.1):
    t = time.time()
    while time.time() - t < timeout:
        r = cond()
        if r: return time.time() - t
        time.sleep(step)
    return None
def reset():
    create_db(DBN); setup_biz(DBN); M.install(DBN); grant_all(DBN)
def workers(n, conc=1, tag="w"):
    return [spawn_worker(FILE, DBN, f"{tag}{i}", conc) for i in range(n)]
def stop(ws):
    for w in ws:
        if w.poll() is None: w.send_signal(signal.SIGKILL)
        w.wait()

def s1():
    out("S1 outbox + RLS: enqueue cung transaction voi ghi nghiep vu")
    async def f():
        async with await conn() as c:
            for t, n in ((TA, 2), (TB, 3)):
                async with c.transaction():
                    await c.execute("SELECT set_config('xt.tenant_id', %s, true)", (t,))
                    for i in range(n): await c.execute("INSERT INTO orders(tenant_id,note) VALUES (%s,'x')", (t,))
            async with c.transaction():
                await c.execute("SELECT set_config('xt.tenant_id', %s, true)", (TA,))
                await c.execute("INSERT INTO orders(tenant_id,note) VALUES (%s,'commit')", (TA,))
                await M.enqueue(c, P("s1-commit-A", TA))
                await M.enqueue(c, P("s1-commit-B", TB))
            try:
                async with c.transaction():
                    await c.execute("SELECT set_config('xt.tenant_id', %s, true)", (TA,))
                    await c.execute("INSERT INTO orders(tenant_id,note) VALUES (%s,'rollback')", (TA,))
                    await M.enqueue(c, P("s1-rollback", TA))
                    raise RuntimeError("business error -> rollback")
            except RuntimeError: pass
    asyncio.run(f())
    out("sau commit/rollback, trang thai job:", {k: M.job_state(DBN, k) for k in ("s1-commit-A", "s1-commit-B", "s1-rollback")})
    out("orders 'rollback' ton tai:", q("SELECT count(*) FROM orders WHERE note='rollback'")[0][0])
    ws = workers(1)
    ok = wait(lambda: q("SELECT count(*) FROM effects")[0][0] >= 2, 20)
    time.sleep(1.5); stop(ws)
    for r in q("SELECT job_key, tenant_id::text, seen_rows, seen_tenants, seen_noctx FROM effects ORDER BY job_key"):
        out("effect", r, "| tenant dung:", r[3] == r[1])
    out("job s1-rollback co chay:", q("SELECT count(*) FROM effects WHERE job_key='s1-rollback'")[0][0], "(ky vong 0); thoi gian toi khi xong:", ok)

def s2():
    out("S2 retry/backoff/dead-letter (max 3 lan)")
    async def f():
        async with await conn() as c:
            async with c.transaction(): await M.enqueue(c, P("s2-fail", TA, "fail"))
    asyncio.run(f()); ws = workers(1)
    wait(lambda: len(q("SELECT 1 FROM attempt_log WHERE job_key='s2-fail'")) >= 3, 40); time.sleep(8); stop(ws)
    ts = [r[0] for r in q("SELECT ts_py FROM attempt_log WHERE job_key='s2-fail' ORDER BY id")]
    out("so lan chay:", len(ts), "khoang cach giua cac lan (s):", [round(b - a, 2) for a, b in zip(ts, ts[1:])])
    out("trang thai cuoi (dead-letter):", M.job_state(DBN, "s2-fail"))

def s3():
    out("S3 kill -9 worker giua job (lease/heartbeat 6s)")
    async def f():
        async with await conn() as c:
            async with c.transaction(): await M.enqueue(c, P("s3-crash", TA, "slow", sleep=4))
    asyncio.run(f()); w1 = workers(1, tag="crash")
    wait(lambda: q("SELECT 1 FROM attempt_log WHERE job_key='s3-crash'"), 20)
    pid1 = q("SELECT pid FROM attempt_log WHERE job_key='s3-crash'")[0][0]
    t_kill = time.time(); stop(w1); out("da kill -9 pid", pid1, "-- state ngay sau kill:", M.job_state(DBN, "s3-crash"))
    w2 = workers(1, tag="rescue")
    d = wait(lambda: q("SELECT 1 FROM effects WHERE job_key='s3-crash'"), 60); stop(w2)
    rows = q("SELECT pid, round((ts_py-%s)::numeric,1) FROM attempt_log WHERE job_key='s3-crash' ORDER BY id", t_kill)
    out("hoi phuc sau", round(time.time() - t_kill, 1) if d else None, "s tu luc kill (gom ~1.5s khoi dong worker moi); attempt_log (pid, s sau kill):", rows)
    out("effects:", q("SELECT job_key, runs, last_pid FROM effects WHERE job_key='s3-crash'"))

def s4():
    out("S4 job tre (SLA timer), huy khi task xong som")
    ws = workers(1)
    time.sleep(2)
    async def f():
        async with await conn() as c:
            t0 = time.time()
            async with c.transaction():
                await M.enqueue(c, P("s4-delay", TA, "stamp"), delay_s=3)
                await M.enqueue(c, P("s4-cancel", TA, "stamp"), delay_s=3, dedupe="s4-cancel")
                await M.enqueue(c, P("s4-pt2m", TA, "stamp"), delay_s=120)
            return t0
    t0 = asyncio.run(f())
    n = asyncio.run(M.cancel(DBN, "s4-cancel")); out("cancel s4-cancel ->", n)
    wait(lambda: q("SELECT 1 FROM attempt_log WHERE job_key='s4-delay'"), 15); time.sleep(2.5)
    r = q("SELECT ts_py FROM attempt_log WHERE job_key='s4-delay'")
    out("job tre 3s chay sau", round(r[0][0] - t0, 2) if r else None, "s; s4-cancel da chay:", q("SELECT count(*) FROM attempt_log WHERE job_key='s4-cancel'")[0][0], "; s4-pt2m (PT2M) da chay:", q("SELECT count(*) FROM attempt_log WHERE job_key='s4-pt2m'")[0][0])
    out("trang thai:", {k: M.job_state(DBN, k) for k in ("s4-delay", "s4-cancel", "s4-pt2m")})
    stop(ws)

def s5():
    out("S5 cron/periodic: 2 worker, cron moi 5 giay, 22 giay")
    if impl_name == "hand":
        async def f():
            import hand_queue as hq
            async with await conn() as c:
                await hq.upsert_schedule(c, "every5s", "* * * * * */5", "Asia/Ho_Chi_Minh", "job", P("cron", None, "stamp"))
        asyncio.run(f())
    ws = workers(2); time.sleep(22); stop(ws)
    ts = [r[0] for r in q("SELECT ts_py FROM attempt_log WHERE job_key='cron' ORDER BY ts_py")]
    out("so lan cron bat:", len(ts), "(ky vong 4-5, KHONG nhan doi); khoang cach:", [round(b - a, 2) for a, b in zip(ts, ts[1:])], "pid:", sorted(set(r[0] for r in q("SELECT pid FROM attempt_log WHERE job_key='cron'"))))
    from croniter import croniter
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    lib = croniter("0 8 * * 1", now).get_next(datetime)  # cach thu vien dung: UTC
    import hand_queue as hq
    out("'0 8 * * 1' theo thu vien (UTC):", lib.isoformat(), "| theo mui gio HCM (hand):", hq.next_fire("0 8 * * 1", "Asia/Ho_Chi_Minh", now).isoformat(), "| cron UTC tuong duong '0 1 * * 1':", croniter("0 1 * * 1", now).get_next(datetime).isoformat())

def s7():
    N = 10000
    out(f"S7 thong luong {N} job noop, 2 worker (conc=1 va conc=8)")
    for conc in (1, 8):
        reset()
        async def f():
            async with await conn() as c:
                t = time.time()
                for i in range(N):
                    async with c.transaction(): await M.enqueue(c, P(f"t{i}", TA, "noop"))
                return time.time() - t
        te = asyncio.run(f()); out(f"enqueue {N} job (1 job/transaction): {te:.1f}s = {N/te:.0f} job/s")
        t0 = time.time(); ws = workers(2, conc)
        first = wait(lambda: M.remaining(DBN) < N, 60)
        t1 = time.time(); wait(lambda: M.remaining(DBN) == 0, 300, 0.2); t2 = time.time(); stop(ws)
        out(f"conc={conc}: job dau xong sau {first and round(t1-t0,1)}s (gom khoi dong); xong het sau {t2-t0:.1f}s; toc do on dinh ~{N/(t2-t1):.0f} job/s")

def s7b():
    out("S7b do tre LISTEN/NOTIFY: worker ranh, polling 5s, 20 job cach nhau 0.7s")
    ws = workers(1); time.sleep(3)
    async def f():
        async with await conn() as c:
            for i in range(20):
                t0 = time.time()
                async with c.transaction(): await M.enqueue(c, P(f"lat{i}", TA, "stamp"))
                await asyncio.sleep(0.7)
                yield_ts.append(t0)
    yield_ts = []
    asyncio.run(f()); time.sleep(1); stop(ws)
    lat = [r[0] - t0 for r, t0 in zip(q("SELECT ts_py FROM attempt_log WHERE job_key LIKE 'lat%' ORDER BY id"), yield_ts)]
    lat = sorted(l for l in lat)
    out(f"n={len(lat)} median={statistics.median(lat)*1000:.0f}ms p95={lat[int(len(lat)*0.95)-1]*1000:.0f}ms max={max(lat)*1000:.0f}ms")

def s8():
    out("S8 trang thai hang doi:", M.status(DBN))

if __name__ == "__main__":
    which = sys.argv[2:] or ["s1", "s2", "s3", "s4", "s5", "s7b", "s8"]
    for name in which:
        if name != "s7": reset()
        globals()[name]()
