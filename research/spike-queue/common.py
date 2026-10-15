# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 xT contributors
"""Ha tang dung chung cho spike hang doi: DB, bang nghiep vu co RLS, handler mau."""
import asyncio, os, subprocess, time
import psycopg

HOST, PORT = "localhost", 15460
def dsn(db, user="xt_app"): return f"postgresql://{user}:pw@{HOST}:{PORT}/{db}"

def admin(db, sql, *args):
    with psycopg.connect(dsn(db, "postgres"), autocommit=True) as c:
        cur = c.execute(sql, args or None)
        return cur.fetchall() if cur.description else None

def create_db(db):
    with psycopg.connect(dsn("postgres", "postgres"), autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        c.execute(f"CREATE DATABASE {db}")
        if not c.execute("select 1 from pg_roles where rolname='xt_app'").fetchone():
            c.execute("CREATE ROLE xt_app LOGIN PASSWORD 'pw' NOSUPERUSER NOBYPASSRLS")

BIZ_SQL = """
CREATE TABLE orders(id bigserial PRIMARY KEY, tenant_id uuid NOT NULL, note text);
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_iso ON orders USING (tenant_id = NULLIF(current_setting('xt.tenant_id', true), '')::uuid);
CREATE TABLE effects(job_key text PRIMARY KEY, tenant_id uuid, runs int NOT NULL DEFAULT 1,
  seen_rows int, seen_tenants text, seen_noctx int, last_pid int);
CREATE TABLE attempt_log(id bigserial PRIMARY KEY, job_key text, ts_py double precision, pid int);
"""
def grant_all(db):
    admin(db, "GRANT ALL ON ALL TABLES IN SCHEMA public TO xt_app; GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO xt_app; "
              "GRANT ALL ON ALL FUNCTIONS IN SCHEMA public TO xt_app")

def setup_biz(db): admin(db, BIZ_SQL)

async def handler(p):
    """p: dict(job_key, tenant_id, mode, sleep). Chay duoi xt_app (NOBYPASSRLS)."""
    mode, key, tid = p["mode"], p["job_key"], p["tenant_id"]
    if mode == "noop": return
    async with await psycopg.AsyncConnection.connect(dsn(DB())) as c:
        await c.execute("INSERT INTO attempt_log(job_key, ts_py, pid) VALUES (%s,%s,%s)", (key, time.time(), os.getpid()))
        await c.commit()
        if mode == "stamp": return
        if mode == "fail": raise RuntimeError(f"boom {key}")
        if mode == "slow": await asyncio.sleep(p.get("sleep", 10))
        async with c.transaction():
            await c.execute("SELECT set_config('xt.tenant_id', %s, true)", (tid,))   # = SET LOCAL
            rows = (await (await c.execute("SELECT count(*), string_agg(DISTINCT tenant_id::text, ',') FROM orders")).fetchone())
        async with c.transaction():   # khong dat ngu canh tenant -> phai thay 0 dong
            noctx = (await (await c.execute("SELECT count(*) FROM orders")).fetchone())[0]
        await c.execute("INSERT INTO effects(job_key,tenant_id,seen_rows,seen_tenants,seen_noctx,last_pid) VALUES (%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (job_key) DO UPDATE SET runs=effects.runs+1, last_pid=EXCLUDED.last_pid",
                        (key, tid, rows[0], rows[1], noctx, os.getpid()))
        await c.commit()

def DB(): return os.environ["XT_SPIKE_DB"]

def spawn_worker(impl_file, db, name, conc=1):
    env = dict(os.environ, XT_SPIKE_DB=db)
    return subprocess.Popen([os.sys.executable, impl_file, "worker", name, str(conc)], env=env,
                            stdout=subprocess.DEVNULL, stderr=open(f"/tmp/xt-spike-queue/{name}.log", "w"))
