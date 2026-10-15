# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Catalog bloat: A (30 model dùng chung) vs B1 (50 tenant x 30 model x 30 trường)."""
import subprocess
from common import *  # noqa
from psycopg import sql

N, NM, NF = 50, 30, 30
reset()
m = mig(); add_tenants(m, N)
spec = gen_catalog(NM, NF)

def stats(label):
    with su() as x:
        r = x.execute("""SELECT
          (SELECT count(*) FROM pg_class), (SELECT count(*) FROM pg_class WHERE relkind='r' AND relnamespace::regnamespace::text LIKE 'xt_%%'),
          (SELECT count(*) FROM pg_class WHERE relkind='i'), (SELECT count(*) FROM pg_attribute), (SELECT count(*) FROM pg_constraint),
          (SELECT count(*) FROM pg_policy), (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal),
          (SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid))) FROM pg_class c WHERE c.relnamespace = 'pg_catalog'::regnamespace AND c.relkind='r'),
          pg_size_pretty(pg_database_size('xt'))""").fetchone()
    t = time.perf_counter()
    out = subprocess.run(["docker", "exec", CONTAINER, "pg_dump", "-U", "xt_owner", "-s", "xt"], capture_output=True, check=True).stdout
    td = time.perf_counter() - t
    print(f"{label:24s} pg_class={r[0]:6d} bảng_xt={r[1]:5d} index={r[2]:6d} pg_attribute={r[3]:7d} constraint={r[4]:6d} policy={r[5]:5d} "
          f"trigger={r[6]:5d} | catalog {r[7]}, DB {r[8]} | pg_dump -s {td:.1f}s ({len(out)/1e6:.1f} MB)")

stats("trống (bootstrap)")
t = time.perf_counter(); apply_spec(m, spec, lay("A", 0), 0); tA = time.perf_counter() - t
stats(f"A: {NM} model")
t = time.perf_counter()
for s in range(1, N + 1):
    apply_spec(m, spec, lay("B1", s), s)
tB = time.perf_counter() - t
stats(f"+B1: {N}x{NM} model")
print(f"Thời gian cấp phát: A {tA:.1f}s | B1 {tB:.1f}s ({tB/N:.2f}s/tenant)")

