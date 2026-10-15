# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
import asyncio, time, psycopg, sys
sys.path.insert(0,'/home/chinhan/xT/research/spike-queue')
from datetime import timedelta
from pgqueuer import PgQueuer
from pgqueuer.adapters.drivers.psycopg import PsycopgDriver
from pgqueuer.queries import Queries
from common import dsn
async def main():
    wc = await psycopg.AsyncConnection.connect(dsn("q_pgq"), autocommit=True)
    pgq = PgQueuer.from_psycopg_connection(wc)
    got = []
    @pgq.entrypoint("job")
    async def job(j): got.append(time.time())
    t = asyncio.create_task(pgq.run(dequeue_timeout=timedelta(seconds=5)))
    await asyncio.sleep(2)
    ec = await psycopg.AsyncConnection.connect(dsn("q_pgq"), autocommit=True)
    q = Queries(PsycopgDriver(ec)); lats=[]
    for i in range(10):
        t0=time.time(); await q.enqueue("job", b"x", 0); await asyncio.sleep(2.5); lats.append((got[-1]-t0) if got else None); got.clear()
    print([round(l*1000) for l in lats])
    pgq.shutdown.set(); await asyncio.sleep(0.5); t.cancel()
asyncio.run(main())
