# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
import asyncio, time, asyncpg, sys
from datetime import timedelta
from pgqueuer import PgQueuer
from pgqueuer.core.executors import DatabaseRetryEntrypointExecutor
from pgqueuer.queries import Queries
sys.path.insert(0,'/home/chinhan/xT/research/spike-queue')
from common import dsn
async def main(d):
    wc = await asyncpg.connect(dsn("q_pgq")); pgq = PgQueuer.from_asyncpg_connection(wc); ts=[]
    @pgq.entrypoint("job", on_failure="hold", executor_factory=lambda p: DatabaseRetryEntrypointExecutor(parameters=p, max_attempts=3, initial_delay=timedelta(seconds=d)))
    async def job(j): ts.append(time.time()); raise RuntimeError("x")
    t = asyncio.create_task(pgq.run(dequeue_timeout=timedelta(seconds=30)))
    await asyncio.sleep(1)
    q = Queries.from_asyncpg_connection(await asyncpg.connect(dsn("q_pgq"))); await q.enqueue("job", b"x", 0)
    await asyncio.sleep(25); print("initial_delay", d, "gaps", [round(b-a,2) for a,b in zip(ts,ts[1:])], "runs", len(ts))
    pgq.shutdown.set(); await asyncio.sleep(.5); t.cancel()
asyncio.run(main(float(sys.argv[1])))
