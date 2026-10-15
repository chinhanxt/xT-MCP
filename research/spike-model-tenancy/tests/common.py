# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Tiện ích chung cho spike tenancy: kết nối, reset DB, layout A/B1/B2, sinh catalog."""
import contextlib, copy, json, pathlib, statistics, subprocess, sys, time, uuid
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg_pool import ConnectionPool
from xtmodel import compiler as C, planner as P
from xtmodel.spec import Catalog

PORT = 15450
CONTAINER = "xt-spike-model2-pg"
DSN = f"host=localhost port={PORT} dbname=xt"
MIG = DSN + " user=xt_migrator password=migrator"
APP = DSN + " user=xt_app password=app"
SU = DSN + " user=xt_owner password=owner"


def tid(seq: int) -> uuid.UUID:
    return uuid.UUID(int=0x7E000000 + seq)


def reset():
    q = lambda *a: subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", "xt_owner", *a],
                                  check=True, capture_output=True, text=True)
    q("-d", "postgres", "-c", "drop database if exists xt with (force)")
    for r in ("xt_pgrst", "xt_web", "xt_bi", "xt_api_owner", "xt_app", "xt_workflow", "xt_migrator", "xt_audit_owner"):
        q("-d", "postgres", "-c", f"drop role if exists {r}")
    q("-d", "postgres", "-c", "create database xt")
    subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-v", "ON_ERROR_STOP=1", "-q", "-U", "xt_owner", "-d", "xt"],
                   input=(ROOT / "xtmodel" / "bootstrap.sql").read_text(), check=True, capture_output=True, text=True)


def mig():
    return psycopg.connect(MIG, autocommit=True)


def su():
    return psycopg.connect(SU, autocommit=True)


def lay(variant: str, seq: int) -> C.Layout:
    if variant == "A":
        return C.Layout()
    if variant == "B1":
        return C.Layout(table_prefix=f"t{seq}__", index_prefix=f"t{seq}_")
    if variant == "B2":
        return C.Layout(schema=f"xt_t{seq}")
    raise ValueError(variant)


@contextlib.contextmanager
def using(layout: C.Layout):
    tok = C.LAYOUT.set(layout)
    try:
        yield layout
    finally:
        C.LAYOUT.reset(tok)


def add_tenants(m, n):
    for s in range(1, n + 1):
        m.execute("INSERT INTO xt_meta.tenant (seq, id, name) OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                  (s, tid(s), f"Công ty {s}"))


def apply_spec(m, spec: dict, layout: C.Layout, tenant_seq: int, confirm=(), defer_post=False, lock_timeout="3s"):
    """Plan + apply một spec dưới một layout; trả (plan, kết quả)."""
    with using(layout):
        if layout.schema != C.SCHEMA:
            m.execute(psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(layout.schema)))
            m.execute(psycopg.sql.SQL("GRANT USAGE ON SCHEMA {} TO xt_app, xt_workflow").format(psycopg.sql.Identifier(layout.schema)))
        cat = Catalog.model_validate(spec)
        p = P.plan(P.load_current(m, tenant_seq), cat, m)
        r = P.apply(m, cat, p.hash, set(confirm) | set(p.to_json()["destructive_op_ids"]), "agent:spike", "user:spike",
                    lock_timeout=lock_timeout, tenant_seq=tenant_seq, defer_post=defer_post)
        return p, r


def gen_catalog(n_models: int, n_fields: int, version: int = 1) -> dict:
    TYPES = ["text", "number", "money", "date", "datetime", "bool", "enum", "user", "file", "json"]
    models = []
    for i in range(n_models):
        fields = []
        for j in range(n_fields):
            t = TYPES[j % len(TYPES)]
            f = {"id": f"f{j}", "name": f"truong_{j}", "type": t, "label": f"Trường {j}"}
            if t == "enum":
                f["options"] = [{"value": f"v{k}", "label": f"Giá trị {k}"} for k in range(5)]
            if t == "money":
                f["min"] = 0
            if j == 0:
                f.update(required=True, unique=True)
            fields.append(f)
        if i > 0:
            fields[-1] = {"id": f"f{n_fields-1}", "name": "cha", "type": "ref", "ref": f"model_{i-1:02d}", "label": "Cha"}
        models.append({"id": f"p{i:02d}", "name": f"model_{i:02d}", "label": f"Model {i}", "fields": fields,
                       "indexes": [{"id": "i1", "fields": ["truong_1", "truong_3"]}],
                       "permissions": {"staff": {"actions": ["create", "read"], "scope": "own"},
                                       "accountant": {"actions": ["read"], "scope": "all"}}})
    return {"version": version, "models": models}


def med(fn, n=15):
    ts = []
    for _ in range(n):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    return statistics.median(ts) * 1000


def ctx(conn, seq, actor=None, roles="accountant"):
    conn.execute("SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), set_config('xt.roles', %s, true)",
                 (str(tid(seq)), str(actor or uuid.UUID(int=seq)), roles))
