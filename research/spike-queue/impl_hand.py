# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(__file__))
import psycopg
from common import *
import hand_queue as hq

NAME = "hand"
def install(db):
    admin(db, hq.SCHEMA)

async def enqueue(conn, p, delay_s=0, dedupe=None):
    await hq.enqueue(conn, "job", p, tenant_id=p["tenant_id"], delay_s=delay_s, dedupe_key=dedupe, max_attempts=3)

async def cancel(db, dedupe):
    async with await psycopg.AsyncConnection.connect(dsn(db), autocommit=True) as c: return await hq.cancel(c, dedupe)

def status(db):
    return dict(admin(db, "SELECT status, count(*) FROM xt_jobs GROUP BY 1"))

def job_state(db, key):
    r = admin(db, "SELECT status, attempts, last_error FROM xt_jobs WHERE payload->>'job_key'=%s", key)
    return r[0] if r else None

def remaining(db): return admin(db, "SELECT count(*) FROM xt_jobs WHERE status IN ('queued','running')")[0][0]

async def worker(name, conc):
    # lease 6s de test kill -9 nhanh; backoff co so 1s
    w = hq.Worker(dsn(DB()), {"job": handler}, name=name, lease_s=6, backoff_base=1.0, conc=conc)
    await w.run()

if __name__ == "__main__" and sys.argv[1] == "worker":
    asyncio.run(worker(sys.argv[2], int(sys.argv[3])))
