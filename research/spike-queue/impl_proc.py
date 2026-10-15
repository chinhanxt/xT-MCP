# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
import asyncio, datetime, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import procrastinate, psycopg
from procrastinate import RetryStrategy
from common import *

NAME = "procrastinate"

def make_app(db, user="xt_app"):
    return procrastinate.App(connector=procrastinate.PsycopgConnector(conninfo=dsn(db, user)),
                             worker_defaults={"update_heartbeat_interval": 2, "stalled_worker_timeout": 6})

app = make_app(os.environ.get("XT_SPIKE_DB", "q_proc"))

@app.task(name="job", retry=RetryStrategy(max_attempts=3, wait=1, exponential_wait=1))
async def job(**p): await handler(p)

@app.task(name="cron_probe")
async def cron_probe(timestamp: int): 
    async with await psycopg.AsyncConnection.connect(dsn(DB())) as c:
        await c.execute("INSERT INTO attempt_log(job_key, ts_py, pid) VALUES ('cron', %s, %s)", (time.time(), os.getpid())); await c.commit()

@app.periodic(cron="* * * * * */5")   # 6 truong: giay o cuoi
@app.task(name="cron_every5s")
async def cron_every5s(timestamp: int): await cron_probe(timestamp)

@app.periodic(cron="* * * * * */2")
@app.task(name="recover_stalled")      # procrastinate KHONG tu nhat lai job cua worker chet: phai tu viet task nay
async def recover_stalled(timestamp: int):
    for j in await app.job_manager.get_stalled_jobs():
        await app.job_manager.retry_job_by_id_async(j.id, datetime.datetime.now(datetime.timezone.utc))

def install(db):
    async def f():
        a = make_app(db, "postgres")
        async with a.open_async(): await a.schema_manager.apply_schema_async()
    asyncio.run(f())

async def enqueue(conn, p, delay_s=0, dedupe=None):
    opts = dict(connection=conn, queueing_lock=dedupe)
    if delay_s: opts["schedule_in"] = {"seconds": delay_s}
    await job.configure(**opts).defer_async(**p)

async def cancel(db, dedupe):
    r = admin(db, "SELECT id FROM procrastinate_jobs WHERE queueing_lock=%s AND status='todo'", dedupe)
    if not r: return 0
    async with app.open_async(): return int(await app.job_manager.cancel_job_by_id_async(r[0][0]))

def status(db): return dict(admin(db, "SELECT status::text, count(*) FROM procrastinate_jobs GROUP BY 1"))

def job_state(db, key):
    r = admin(db, "SELECT status::text, attempts, (SELECT e.type::text||coalesce(' ' ,'') FROM procrastinate_events e WHERE e.job_id=j.id ORDER BY e.id DESC LIMIT 1) "
                  "FROM procrastinate_jobs j WHERE args->>'job_key'=%s", key)
    return r[0] if r else None

def remaining(db): return admin(db, "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo','doing') AND task_name='job'")[0][0]

if __name__ == "__main__" and sys.argv[1] == "worker":
    app.run_worker(name=sys.argv[2], concurrency=int(sys.argv[3]), wait=True, install_signal_handlers=False)
