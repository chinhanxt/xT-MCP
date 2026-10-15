# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, pathlib, sys
import _paths
import psycopg
from xtmodel.spec import Catalog
from xtmodel import planner as P
from xtmodel.compiler import compile_catalog
from _paths import PACKS_ROOT as ROOT
PACKS = ["de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"]
from seedlib import load_catalog as load
DSN = "host=localhost port=15440 dbname=xt user=xt_migrator password=migrator"
m = psycopg.connect(DSN, autocommit=True)
for i in range(1, 4):
    cat = load(PACKS[:i], i)
    steps = compile_catalog(cat)
    print(f"=== catalog v{i}: packs={PACKS[:i]} models={len(cat.models)} DDL statements={len(steps)}")
    p = P.plan(P.load_current(m), cat, m, rehearse=True)
    print(p.render_vi())
    res = P.apply(m, cat, p.hash, set(), proposed_by="agent:claude", approved_by="user:director")
    print("APPLY:", {k: res[k] for k in ("version", "plan_hash", "tx_steps")}, "post:", len(res["post"]), "post_ok:", all(x["ok"] for x in res["post"]))
for r in m.execute("select table_name from information_schema.tables where table_schema='xt_data' order by 1"):
    pass
print("tables:", [r[0] for r in m.execute("select tablename from pg_tables where schemaname='xt_data' order by 1")])
print("rls forced:", m.execute("select count(*), count(*) filter (where relrowsecurity and relforcerowsecurity) from pg_class c join pg_namespace n on n.oid=relnamespace where nspname='xt_data' and relkind='r'").fetchone())
print("policies:", m.execute("select count(*) from pg_policies where schemaname='xt_data'").fetchone()[0])
