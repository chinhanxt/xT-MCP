# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Vá cho A: login SQL trực tiếp gắn cứng tenant theo session_user (không đổi được bằng SET/SET ROLE)."""
from common import *  # noqa
from psycopg import sql
x = su()
x.execute("""
  CREATE TABLE IF NOT EXISTS xt_meta.login_tenant (login name PRIMARY KEY, tenant_id uuid NOT NULL);
  ALTER TABLE xt_meta.login_tenant OWNER TO xt_migrator;
    CREATE OR REPLACE FUNCTION xt.login_tenant() RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog AS $$ SELECT l.tenant_id FROM xt_meta.login_tenant l WHERE l.login = session_user $$;
  ALTER FUNCTION xt.login_tenant() OWNER TO xt_migrator;
  CREATE OR REPLACE FUNCTION xt.tenant() RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT coalesce(xt.login_tenant(), nullif(current_setting('xt.tenant_id', true), '')::uuid) $$;
""")
x.execute("INSERT INTO xt_meta.login_tenant VALUES ('xt_bi_a', %s) ON CONFLICT DO NOTHING", (tid(7),))
with psycopg.connect(DSN + " user=xt_bi_a password=bi", autocommit=True) as c:
    c.execute(sql.SQL("SET xt.tenant_id = {}").format(sql.Literal(str(tid(8)))))
    print("xt_bi_a (gắn T7 theo session_user) SET xt.tenant_id=T8 rồi đọc api.model_00 ->",
          c.execute("SELECT count(*), min(tenant_id::text) FROM api.model_00").fetchone())
with psycopg.connect(APP) as c, c.transaction():
    ctx(c, 7, roles="accountant")
    t = time.perf_counter()
    for _ in range(200):
        c.execute("SELECT count(*) FROM xt_data.model_00").fetchone()
    print(f"xt_app (không gắn) vẫn dùng GUC: đếm model_00 T7 = {c.execute('SELECT count(*) FROM xt_data.model_00').fetchone()[0]}, "
          f"{(time.perf_counter()-t)/200*1000:.2f} ms/truy vấn")
    print("plan:", [r[0] for r in c.execute("EXPLAIN (COSTS OFF) SELECT count(*) FROM xt_data.model_00").fetchall()][:4])
