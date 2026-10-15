# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""B2 (schema theo tenant): sao lưu/khôi phục/xóa tenant bằng schema, gồm cả schema view api."""
import hashlib, subprocess
from common import *  # noqa
from psycopg import sql

NM, ROWS, S = 30, 1000, 3
m = mig()
if not m.execute("SELECT 1 FROM xt_meta.catalog_version WHERE tenant_seq = %s", (100 + S,)).fetchone():
    t = time.perf_counter(); apply_spec(m, gen_catalog(NM, 30), lay("B2", S), 100 + S); print(f"cấp phát B2 tenant {S} (30 model): {time.perf_counter()-t:.2f}s")
x = su()
x.execute("SET session_replication_role = replica")
for i in range(NM):
    x.execute(sql.SQL("INSERT INTO {} (tenant_id, truong_0, truong_1) SELECT %s, 'k' || g, g FROM generate_series(1, %s) g ON CONFLICT DO NOTHING").format(
        sql.Identifier(f"xt_t{S}", f"model_{i:02d}")), (tid(S), ROWS))
x.execute("RESET session_replication_role")
x.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION xt_api_owner").format(sql.Identifier(f"api_s{S}")))
x.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO xt_api_owner").format(sql.Identifier(f"xt_t{S}")))
x.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO xt_api_owner").format(sql.Identifier(f"xt_t{S}")))
x.execute("SET ROLE xt_api_owner")
for i in range(NM):
    x.execute(sql.SQL("CREATE OR REPLACE VIEW {} AS SELECT * FROM {}").format(sql.Identifier(f"api_s{S}", f"model_{i:02d}"), sql.Identifier(f"xt_t{S}", f"model_{i:02d}")))
x.execute("RESET ROLE")

def digest():
    h = hashlib.sha256(); n = 0
    for i in range(NM):
        for r in x.execute(sql.SQL("SELECT id, truong_0 FROM {} ORDER BY id").format(sql.Identifier(f"xt_t{S}", f"model_{i:02d}"))):
            h.update(repr(r).encode()); n += 1
    return n, h.hexdigest()[:12]
before = digest()
t = time.perf_counter()
dump = subprocess.run(["docker", "exec", CONTAINER, "pg_dump", "-U", "xt_owner", "-Fc", "-n", f"xt_t{S}", "-n", f"api_s{S}", "xt"], capture_output=True, check=True).stdout
td = time.perf_counter() - t
t = time.perf_counter(); x.execute(sql.SQL("DROP SCHEMA {}, {} CASCADE").format(sql.Identifier(f"api_s{S}"), sql.Identifier(f"xt_t{S}"))); tdrop = time.perf_counter() - t
t = time.perf_counter()
subprocess.run(["docker", "exec", "-i", CONTAINER, "pg_restore", "-U", "xt_owner", "-d", "xt", "--exit-on-error"], input=dump, capture_output=True, check=True)
tr = time.perf_counter() - t
after = digest()
views = x.execute("SELECT count(*) FROM pg_views WHERE schemaname = %s", (f"api_s{S}",)).fetchone()[0]
pol = x.execute("SELECT count(*) FROM pg_policies WHERE schemaname = %s", (f"xt_t{S}",)).fetchone()[0]
print(f"B2 pg_dump -n xt_t{S} -n api_s{S}: {td:.2f}s, {len(dump)/1e6:.1f} MB | DROP SCHEMA … CASCADE (xóa tenant): {tdrop*1000:.0f} ms | "
      f"restore {tr:.2f}s | dữ liệu {before} -> {after} {'KHỚP' if before == after else 'LỆCH'} | view api={views}, policy={pol}")
