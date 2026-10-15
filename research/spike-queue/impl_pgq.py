# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
import asyncio, json, os, sys
from datetime import timedelta
sys.path.insert(0, os.path.dirname(__file__))
import psycopg
from pgqueuer import PgQueuer
from pgqueuer.adapters.drivers.psycopg import PsycopgDriver
from pgqueuer.core.executors import DatabaseRetryEntrypointExecutor
from pgqueuer.queries import Queries
from common import *

NAME = "pgqueuer"

def install(db):
    async def f():
        async with await psycopg.AsyncConnection.connect(dsn(db, "postgres"), autocommit=True) as c:
            await Queries(PsycopgDriver(c)).install()
    asyncio.run(f())

async def enqueue(conn, p, delay_s=0, dedupe=None):
    # PsycopgDriver bat buoc autocommit=True -> outbox phai dung `async with conn.transaction()` tuong minh (xem scenarios)
    q = Queries(PsycopgDriver(conn))
    await q.enqueue("job", json.dumps(p).encode(), 0, timedelta(seconds=delay_s) if delay_s else None, dedupe, None)

async def cancel(db, dedupe):
    r = admin(db, "SELECT id FROM pgqueuer WHERE dedupe_key=%s AND status='queued'", dedupe)
    if not r: return 0
    async with await psycopg.AsyncConnection.connect(dsn(db), autocommit=True) as c:
        await Queries(PsycopgDriver(c)).mark_job_as_cancelled([r[0][0]])
    return 1

def status(db): return dict(admin(db, "SELECT status::text, count(*) FROM pgqueuer GROUP BY 1"))

def job_state(db, key):
    r = admin(db, "SELECT status::text, attempts, NULL FROM pgqueuer WHERE convert_from(payload,'UTF8')::jsonb->>'job_key'=%s", key)
    return r[0] if r else None   # job thanh cong bi xoa khoi bang; that bai (on_failure=hold) o lai status=failed

def remaining(db): return admin(db, "SELECT count(*) FROM pgqueuer")[0][0]

async def worker(name, conc):
    async with await psycopg.AsyncConnection.connect(dsn(DB()), autocommit=True) as c:
        pgq = PgQueuer.from_psycopg_connection(c)
        @pgq.entrypoint("job", on_failure="hold", executor_factory=lambda prm: DatabaseRetryEntrypointExecutor(
                parameters=prm, max_attempts=3, initial_delay=timedelta(seconds=1)))
        async def job(j): await handler(json.loads(j.payload))
        @pgq.schedule("cron_every5s", "* * * * * */5")
        async def cron_every5s(schedule):
            async with await psycopg.AsyncConnection.connect(dsn(DB())) as c2:
                await c2.execute("INSERT INTO attempt_log(job_key, ts_py, pid) VALUES ('cron', %s, %s)", (time.time(), os.getpid())); await c2.commit()
        await pgq.run(batch_size=conc, max_concurrent_tasks=2 * conc,  # lib bat buoc >= 2x batch
                         heartbeat_timeout=timedelta(seconds=6), dequeue_timeout=timedelta(seconds=5))

if __name__ == "__main__" and sys.argv[1] == "worker":
    asyncio.run(worker(sys.argv[2], int(sys.argv[3])))
