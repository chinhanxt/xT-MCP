# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""BI/DuckDB: truy cập SQL trực tiếp của tenant (A vs B), xuất Parquet, truy vấn xuyên tenant. Chạy sau t6."""
import duckdb, os, shutil
from common import *  # noqa
from psycopg import sql

NM = 30
x = su()
# Chuẩn bị: view chạy bằng quyền chủ xt_api_owner (NOBYPASSRLS), policy SELECT + tenant áp cho xt_api_owner.
def fix(table, view):
    for p in x.execute("SELECT policyname FROM pg_policies WHERE schemaname='xt_data' AND tablename=%s AND cmd IN ('SELECT','ALL')", (table,)).fetchall():
        x.execute(sql.SQL("ALTER POLICY {} ON {} TO xt_app, xt_workflow, xt_api_owner").format(sql.Identifier(p[0]), sql.Identifier("xt_data", table)))
    x.execute(sql.SQL("ALTER VIEW {} SET (security_invoker = false)").format(view))
missing = x.execute("SELECT to_regclass('api_t7.model_00') IS NULL").fetchone()[0]
print("api_t7.* sau khi t6 DROP TABLE t7__* CASCADE + pg_restore -t: ", "MẤT (view phụ thuộc bị CASCADE xóa, -t không dump schema api_t7)" if missing else "còn")
if missing:
    x.execute("SET ROLE xt_api_owner")
    for i in range(NM):
        x.execute(sql.SQL("CREATE VIEW {} AS SELECT * FROM {}").format(sql.Identifier("api_t7", f"model_{i:02d}"), sql.Identifier("xt_data", f"t7__model_{i:02d}")))
        x.execute(sql.SQL("GRANT SELECT ON {} TO xt_web").format(sql.Identifier("api_t7", f"model_{i:02d}")))
    x.execute("RESET ROLE")
for i in range(NM):
    fix(f"model_{i:02d}", sql.Identifier("api", f"model_{i:02d}"))
    for s in (7, 8):
        fix(f"t{s}__model_{i:02d}", sql.Identifier(f"api_t{s}", f"model_{i:02d}"))
x.execute("""DO $$ BEGIN CREATE ROLE xt_bi_a LOGIN PASSWORD 'bi'; CREATE ROLE xt_bi_t7 LOGIN PASSWORD 'bi';
             EXCEPTION WHEN duplicate_object THEN NULL; END $$""")
x.execute("GRANT USAGE ON SCHEMA api, xt TO xt_bi_a; GRANT SELECT ON ALL TABLES IN SCHEMA api TO xt_bi_a")
x.execute("GRANT USAGE ON SCHEMA api_t7, xt TO xt_bi_t7; GRANT SELECT ON ALL TABLES IN SCHEMA api_t7 TO xt_bi_t7")
for r in ("xt_bi_a", "xt_bi_t7"):   # ngữ cảnh mặc định khi đăng nhập
    x.execute(sql.SQL("ALTER ROLE {} SET xt.tenant_id = {}").format(sql.Identifier(r), sql.Literal(str(tid(7)))))
    x.execute(sql.SQL("ALTER ROLE {} SET xt.roles = 'accountant'").format(sql.Identifier(r)))

d = duckdb.connect()
d.execute("LOAD postgres_scanner")
def att(name, user):
    d.execute(f"ATTACH 'host=localhost port={PORT} dbname=xt user={user} password=bi' AS {name} (TYPE postgres, READ_ONLY)")
print("### Tenant 7 dùng DuckDB truy cập thẳng Postgres")
att("pa", "xt_bi_a"); att("pb", "xt_bi_t7")
q = lambda s: d.execute(s).fetchall()
print("  A  (role xt_bi_a,  bảng chung):   đếm api.model_00 =", q("SELECT count(*) FROM pa.api.model_00")[0][0])
print("  (DuckDB mở kết nối riêng cho mỗi lần quét nên SET qua postgres_execute không bám; tấn công dưới đây dùng client SQL bất kỳ)")
for role, rel in (("xt_bi_a", "api.model_00"), ("xt_bi_t7", "api_t7.model_00"), ("xt_bi_t7", "api_t8.model_00")):
    with psycopg.connect(DSN + f" user={role} password=bi", autocommit=True) as c:
        try:
            c.execute(sql.SQL("SET xt.tenant_id = {}").format(sql.Literal(str(tid(8)))))
            r = c.execute(f"SELECT count(*), min(tenant_id::text) FROM {rel}").fetchone()
            print(f"  {role:9s} tự `SET xt.tenant_id = T8` rồi đọc {rel:16s} -> {r[0]} dòng, tenant {r[1]}" + ("  <- RÒ tenant 8" if r[1] and r[1].endswith("08") else ""))
        except psycopg.Error as e:
            print(f"  {role:9s} tự `SET xt.tenant_id = T8` rồi đọc {rel:16s} -> {type(e).__name__}: {e.diag.message_primary}")
print("\n### Xuất snapshot Parquet của tenant 7 (30 model)")
out = "/tmp/xt-spike-model2/parquet"; shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
d.execute("DETACH pa"); d.execute("DETACH pb"); att("pb", "xt_bi_t7")
t = time.perf_counter()
for i in range(NM):
    d.execute(f"COPY (SELECT * FROM pb.api_t7.model_{i:02d}) TO '{out}/b_model_{i:02d}.parquet' (FORMAT parquet)")
tb = time.perf_counter() - t
d.execute(f"ATTACH 'host=localhost port={PORT} dbname=xt user=xt_migrator password=migrator' AS pm (TYPE postgres, READ_ONLY)")
t = time.perf_counter()
for i in range(NM):
    d.execute(f"COPY (SELECT * FROM pm.xt_data.model_{i:02d} WHERE tenant_id = '{tid(7)}') TO '{out}/a_model_{i:02d}.parquet' (FORMAT parquet)")
ta = time.perf_counter() - t
sz = lambda p: sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out) if f.startswith(p))
print(f"  B qua role tenant (api_t7): {tb:.2f}s, {sz('b_')/1e6:.2f} MB | A qua migrator + WHERE tenant_id (control plane): {ta:.2f}s, {sz('a_')/1e6:.2f} MB")
print("  đọc lại Parquet:", q(f"SELECT count(*) FROM read_parquet('{out}/b_model_*.parquet')")[0][0], "dòng")

print("\n### Truy vấn xuyên tenant (control plane, migrator BYPASSRLS): tổng truong_2 theo tenant trên model_00")
t = time.perf_counter(); ra = q("SELECT count(*) FROM (SELECT tenant_id, sum(truong_2) FROM pm.xt_data.model_00 GROUP BY 1)"); ta = time.perf_counter() - t
union = " UNION ALL ".join(f"SELECT {s} AS seq, truong_2 FROM pm.xt_data.t{s}__model_00" for s in range(1, 51))
t = time.perf_counter(); rb = q(f"SELECT count(*) FROM (SELECT seq, sum(truong_2) FROM ({union}) GROUP BY 1)"); tb = time.perf_counter() - t
print(f"  A: 1 bảng GROUP BY -> {ra[0][0]} tenant, {ta*1000:.0f} ms | B1: UNION ALL 50 bảng -> {rb[0][0]} tenant, {tb*1000:.0f} ms (truy vấn sinh tự động; model khác nhau giữa tenant thì không UNION được)")
