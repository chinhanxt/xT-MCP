# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
"""Hang doi Postgres SKIP LOCKED tu viet (~100 dong). Hoc PATTERN tu sklite (khong chep ma)."""
import asyncio, json, random, time, uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import psycopg
from croniter import croniter
from psycopg.rows import dict_row

SCHEMA = """
CREATE TABLE xt_jobs(id bigserial PRIMARY KEY, tenant_id uuid, kind text NOT NULL, payload jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','done','dead','cancelled')),
  attempts int NOT NULL DEFAULT 0, max_attempts int NOT NULL DEFAULT 5, run_at timestamptz NOT NULL DEFAULT now(),
  locked_by text, lease_until timestamptz, dedupe_key text, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX xt_jobs_ready ON xt_jobs(run_at) WHERE status='queued';
CREATE INDEX xt_jobs_lease ON xt_jobs(lease_until) WHERE status='running';
CREATE UNIQUE INDEX xt_jobs_dedupe ON xt_jobs(dedupe_key) WHERE dedupe_key IS NOT NULL AND status IN ('queued','running');
CREATE FUNCTION xt_jobs_notify() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_notify('xt_jobs', NEW.kind); RETURN NEW; END $$;
CREATE TRIGGER xt_jobs_ins AFTER INSERT ON xt_jobs FOR EACH ROW EXECUTE FUNCTION xt_jobs_notify();
CREATE TABLE xt_schedules(name text PRIMARY KEY, cron text NOT NULL, tz text NOT NULL, kind text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}', next_run timestamptz NOT NULL);
"""

async def enqueue(conn, kind, payload, tenant_id=None, delay_s=0, dedupe_key=None, max_attempts=5):
    """Chay tren connection/transaction CUA CALLER -> outbox: rollback thi job mat."""
    await conn.execute("INSERT INTO xt_jobs(kind,payload,tenant_id,run_at,dedupe_key,max_attempts) "
                       "VALUES (%s,%s,%s,now()+make_interval(secs=>%s),%s,%s) ON CONFLICT DO NOTHING",
                       (kind, json.dumps(payload), tenant_id, delay_s, dedupe_key, max_attempts))

async def cancel(conn, dedupe_key):
    cur = await conn.execute("UPDATE xt_jobs SET status='cancelled',updated_at=now() WHERE dedupe_key=%s AND status='queued'", (dedupe_key,))
    return cur.rowcount

def next_fire(cron, tz, after=None):  # cron theo mui gio dia phuong, tra ve UTC
    z = ZoneInfo(tz); base = (after or datetime.now(timezone.utc)).astimezone(z)
    return croniter(cron, base).get_next(datetime).astimezone(timezone.utc)

async def upsert_schedule(conn, name, cron, tz, kind, payload=None):
    await conn.execute("INSERT INTO xt_schedules VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (name) DO UPDATE SET cron=EXCLUDED.cron,tz=EXCLUDED.tz,next_run=EXCLUDED.next_run",
                       (name, cron, tz, kind, json.dumps(payload or {}), next_fire(cron, tz)))

class Worker:
    def __init__(self, dsn, handlers, name=None, lease_s=30, backoff_base=2.0, poll_s=5.0, conc=1):
        self.dsn, self.h, self.name = dsn, handlers, name or f"w-{uuid.uuid4().hex[:6]}"
        self.lease, self.base, self.poll, self.sem = lease_s, backoff_base, poll_s, asyncio.Semaphore(conc)
        self.conc, self.wake = conc, asyncio.Event()

    async def claim(self, c, n):
        # job het lease (worker chet) bi nhat lai; qua max_attempts -> dead (poison pill)
        await c.execute("UPDATE xt_jobs SET status='dead',last_error='lease expired, max attempts',locked_by=NULL WHERE status='running' AND lease_until<now() AND attempts>=max_attempts")
        cur = await c.execute("""WITH k AS (SELECT id FROM xt_jobs WHERE (status='queued' AND run_at<=now()) OR (status='running' AND lease_until<now())
              ORDER BY run_at,id FOR UPDATE SKIP LOCKED LIMIT %s)
            UPDATE xt_jobs j SET status='running',attempts=attempts+1,locked_by=%s,lease_until=now()+make_interval(secs=>%s),updated_at=now()
            FROM k WHERE j.id=k.id RETURNING j.*""", (n, self.name, self.lease))
        return await cur.fetchall()

    async def finish(self, c, j, err=None):  # fencing: chi chu lease hien tai (locked_by+attempts) moi ghi duoc
        w = "WHERE id=%s AND locked_by=%s AND attempts=%s AND status='running'"; k = (j["id"], self.name, j["attempts"])
        if err is None: await c.execute("UPDATE xt_jobs SET status='done',lease_until=NULL,updated_at=now() " + w, k)
        elif j["attempts"] >= j["max_attempts"]: await c.execute("UPDATE xt_jobs SET status='dead',last_error=%s,updated_at=now() " + w, (err,) + k)
        else:
            d = min(3600, self.base * 2 ** (j["attempts"] - 1)) * random.uniform(0.8, 1.2)
            await c.execute("UPDATE xt_jobs SET status='queued',run_at=now()+make_interval(secs=>%s),last_error=%s,locked_by=NULL,updated_at=now() " + w, (d, err) + k)

    async def run_one(self, pool, j):
        async def beat():
            while True:
                await asyncio.sleep(self.lease / 3)
                await pool.execute("UPDATE xt_jobs SET lease_until=now()+make_interval(secs=>%s) WHERE id=%s AND locked_by=%s", (self.lease, j["id"], self.name))
        hb = asyncio.create_task(beat())
        try:
            await self.h[j["kind"]](j["payload"]); err = None
        except Exception as e: err = repr(e)
        finally: hb.cancel()
        await pool.finish(self, j, err)

    async def tick_schedules(self, c):  # nhieu replica an toan: SKIP LOCKED + dedupe_key theo moc gio
        async with c.transaction():
            for s in await (await c.execute("SELECT * FROM xt_schedules WHERE next_run<=now() FOR UPDATE SKIP LOCKED")).fetchall():
                await enqueue(c, s["kind"], s["payload"], dedupe_key=f"{s['name']}@{s['next_run'].isoformat()}")
                await c.execute("UPDATE xt_schedules SET next_run=%s WHERE name=%s", (next_fire(s["cron"], s["tz"], s["next_run"]), s["name"]))

    async def run(self, stop: asyncio.Event | None = None):
        stop = stop or asyncio.Event()
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row) as lc, \
                   await psycopg.AsyncConnection.connect(self.dsn, autocommit=True, row_factory=dict_row) as cc:
            await lc.execute("LISTEN xt_jobs")
            async def listen():
                async for _ in lc.notifies(): self.wake.set()
            lt = asyncio.create_task(listen()); inflight = set()
            class P:  # tien ich: ghi tren connection chung, tuan tu hoa bang lock
                lock = asyncio.Lock()
                async def execute(s, *a):
                    async with s.lock: return await cc.execute(*a)
                async def finish(s, w, j, err):
                    async with s.lock: await w.finish(cc, j, err)
            pool = P(); last_tick = 0.0
            while not stop.is_set():
                async with pool.lock:
                    if time.monotonic() - last_tick >= 1.0: await self.tick_schedules(cc); last_tick = time.monotonic()
                    free = self.conc - len(inflight)
                    jobs = await self.claim(cc, free) if free > 0 else []
                for j in jobs:
                    t = asyncio.create_task(self.run_one(pool, j)); inflight.add(t); t.add_done_callback(inflight.discard); t.add_done_callback(lambda _: self.wake.set())
                if not jobs:
                    self.wake.clear()
                    async with pool.lock:  # ngu den job hen gio ke tiep, toi da poll_s
                        nxt = (await (await cc.execute("SELECT extract(epoch FROM min(CASE status WHEN 'queued' THEN run_at ELSE lease_until END)-now()) AS d FROM xt_jobs WHERE status IN ('queued','running')")).fetchone())["d"]
                    try: await asyncio.wait_for(self.wake.wait(), min(self.poll, max(0.01, float(nxt))) if nxt is not None else self.poll)
                    except asyncio.TimeoutError: pass
            lt.cancel()
