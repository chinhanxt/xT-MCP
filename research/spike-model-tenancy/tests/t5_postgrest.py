# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""PostgREST thật: A = schema `api` (view trên bảng chung) vs B = schema `api_t<seq>` + Accept-Profile.
Chạy sau t3_bloat (50 tenant x 30 model B1 + 30 model A)."""
import base64, hashlib, hmac, json, re, subprocess, urllib.request, urllib.error
from common import *  # noqa
from psycopg import sql

N, NM = 50, 30
SECRET = "xt-spike-postgrest-secret-0123456789abcdef"
IMG = "postgrest/postgrest:latest"


def jwt(seq, roles=("accountant",)):
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).rstrip(b"=")
    h, p = b({"alg": "HS256", "typ": "JWT"}), b({"role": "xt_web", "aud": "xt-data", "sub": str(uuid.UUID(int=seq)),
                                               "tenant_id": str(tid(seq)), "tenant_seq": seq, "roles": list(roles),
                                               "exp": int(time.time()) + 3600})
    sig = base64.urlsafe_b64encode(hmac.new(SECRET.encode(), h + b"." + p, hashlib.sha256).digest()).rstrip(b"=")
    return (h + b"." + p + b"." + sig).decode()


def get(port, path, token=None, profile=None):
    req = urllib.request.Request(f"http://localhost:{port}{path}")
    if token: req.add_header("Authorization", "Bearer " + token)
    if profile: req.add_header("Accept-Profile", profile)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read()
        try: body = json.loads(body)
        except Exception: pass
        return e.code, body

import uuid
with su() as x:
    x.execute("""
      DO $$ BEGIN
        CREATE ROLE xt_pgrst LOGIN PASSWORD 'pgrst' NOINHERIT;
        CREATE ROLE xt_web NOLOGIN;
        CREATE ROLE xt_api_owner NOLOGIN NOBYPASSRLS;
      EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      GRANT xt_web TO xt_pgrst;
      GRANT xt_app TO xt_web;              -- policy TO xt_app áp cho thành viên; quyền SELECT bảng gốc cho view security_invoker
      GRANT CREATE ON DATABASE xt TO xt_api_owner;
      GRANT USAGE ON SCHEMA xt_data, xt TO xt_api_owner;
    """)
    # pre-request: đặt GUC từ claim + chặn profile khác tenant trong token
    x.execute("""
      CREATE OR REPLACE FUNCTION xt.pgrst_pre() RETURNS void LANGUAGE plpgsql AS $f$
      DECLARE c json := current_setting('request.jwt.claims', true)::json; s text := current_schema();
      BEGIN
        IF s LIKE 'api\\_t%' AND s <> 'api_t' || (c->>'tenant_seq') THEN
          RAISE EXCEPTION 'schema % không thuộc tenant trong token', s USING ERRCODE = '42501';
        END IF;
        PERFORM set_config('xt.tenant_id', c->>'tenant_id', true), set_config('xt.actor_id', c->>'sub', true),
                set_config('xt.roles', (SELECT string_agg(r, ',') FROM json_array_elements_text(c->'roles') r), true);
      END $f$;
      GRANT EXECUTE ON FUNCTION xt.pgrst_pre() TO xt_web;
    """)
m = mig()
with su() as x:
    for s in (7, 8):   # vài dòng mẫu
        for v, t in (("A", "model_00"), ("B1", f"t{s}__model_00")):
            x.execute(sql.SQL("INSERT INTO {} (tenant_id, truong_0, truong_1) VALUES (%s, %s, 1), (%s, %s, 2)").format(sql.Identifier("xt_data", t)),
                      (tid(s), f"{v}-T{s}-a", tid(s), f"{v}-T{s}-b"))

def make_views(schema, tables, cur):
    cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION xt_api_owner").format(sql.Identifier(schema)))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO xt_web").format(sql.Identifier(schema)))
    cur.execute("SET ROLE xt_api_owner")
    for view, base in tables:
        cur.execute(sql.SQL("CREATE OR REPLACE VIEW {} WITH (security_invoker = true) AS SELECT * FROM {}").format(
            sql.Identifier(schema, view), sql.Identifier("xt_data", base)))
        cur.execute(sql.SQL("GRANT SELECT ON {} TO xt_web").format(sql.Identifier(schema, view)))
    cur.execute("RESET ROLE")

t = time.perf_counter()
with su() as x:
    x.execute("GRANT SELECT ON ALL TABLES IN SCHEMA xt_data TO xt_api_owner")
    make_views("api", [(f"model_{i:02d}", f"model_{i:02d}") for i in range(NM)], x)
    for s in range(1, N):   # tenant 50 để thêm lúc chạy
        make_views(f"api_t{s}", [(f"model_{i:02d}", f"t{s}__model_{i:02d}") for i in range(NM)], x)
    x.execute("ALTER ROLE xt_pgrst SET pgrst.db_schemas = %s" % psycopg.sql.Literal(",".join(f"api_t{s}" for s in range(1, N))).as_string())
print(f"tạo view: A 30 view schema api; B {(N-1)*NM} view trong {N-1} schema api_t<seq> ({time.perf_counter()-t:.1f}s)")

def run(name, port, schemas):
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--network", "host",
                    "-e", "PGRST_DB_URI=postgres://xt_pgrst:pgrst@localhost:15450/xt", "-e", f"PGRST_DB_SCHEMAS={schemas}",
                    "-e", f"PGRST_JWT_SECRET={SECRET}", "-e", "PGRST_JWT_AUD=xt-data", "-e", "PGRST_DB_PRE_REQUEST=xt.pgrst_pre",
                    "-e", f"PGRST_SERVER_PORT={port}", "-e", "PGRST_DB_CONFIG=" + ("true" if name.endswith("b") else "false"),
                    "-e", "PGRST_LOG_LEVEL=info", IMG], check=True, capture_output=True)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://localhost:{port}/", timeout=1)
        except urllib.error.HTTPError:
            break
        except Exception:
            time.sleep(0.2); continue
        break
    time.sleep(1.5)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    return [l for l in (logs.stdout + logs.stderr).splitlines() if "Schema cache" in l or "Loaded" in l or "Config" in l.lower() and "reload" in l.lower()]

la = run("xt-spike-pgrst-a", 15451, "api")
lb = run("xt-spike-pgrst-b", 15452, "api_t1")  # db-config=true => pgrst.db_schemas trong DB ghi đè
def cache_ms(lines):
    ms = [l for l in lines if "Schema cache loaded" in l]
    return ms[-1].split("Schema cache loaded")[-1].strip() if ms else lines[-3:]
print("PostgREST 16.4 | A schema cache:", cache_ms(la), "| B schema cache:", cache_ms(lb))

print("\n### A: /model_00 trên schema api (bảng chung)")
for label, tok in (("token T7", jwt(7)), ("token T8", jwt(8)), ("không token", None)):
    st, body = get(15451, "/model_00?select=truong_0", tok)
    print(f"  {label:12s} -> {st} {body if st != 200 else [r['truong_0'] for r in body]}")
print("\n### B: Accept-Profile api_t<seq>")
for label, tok, prof in (("T7 + api_t7", jwt(7), "api_t7"), ("T8 + api_t8", jwt(8), "api_t8"), ("T7 + api_t8 (đổi profile)", jwt(7), "api_t8"),
                         ("T7 không header (mặc định api_t1)", jwt(7), None), ("T7 + api_t50 (chưa có)", jwt(7), "api_t50")):
    st, body = get(15452, "/model_00?select=truong_0", tok, prof)
    print(f"  {label:34s} -> {st} {[r['truong_0'] for r in body] if st == 200 else (body.get('code'), body.get('message')) if isinstance(body, dict) else body}")

print("\n### B: thêm tenant 50 lúc chạy")
t = time.perf_counter()
from xtmodel.spec import Catalog
apply_spec(m, gen_catalog(NM, 30), lay("B1", 50), 50) if not m.execute("SELECT 1 FROM xt_meta.catalog_version WHERE tenant_seq=50").fetchone() else None
with su() as x:
    x.execute(sql.SQL("INSERT INTO xt_data.t50__model_00 (tenant_id, truong_0) VALUES (%s, 'B1-T50-a')"), (tid(50),))
    make_views("api_t50", [(f"model_{i:02d}", f"t50__model_{i:02d}") for i in range(NM)], x)
    x.execute("ALTER ROLE xt_pgrst SET pgrst.db_schemas = %s" % psycopg.sql.Literal(",".join(f"api_t{s}" for s in range(1, N + 1))).as_string())
    print("  trước reload:", get(15452, "/model_00?select=truong_0", jwt(50), "api_t50")[0])
    t0 = time.perf_counter()
    x.execute("NOTIFY pgrst, 'reload config'"); x.execute("NOTIFY pgrst, 'reload schema'")
for _ in range(200):
    st, body = get(15452, "/model_00?select=truong_0", jwt(50), "api_t50")
    if st == 200: break
    time.sleep(0.05)
print(f"  sau NOTIFY reload config+schema: {st} {body} sau {(time.perf_counter()-t0)*1000:.0f} ms")
logs = subprocess.run(["docker", "logs", "xt-spike-pgrst-b"], capture_output=True, text=True)
print("  log B:", [l[-90:] for l in (logs.stdout + logs.stderr).splitlines() if "cache" in l.lower() or "config" in l.lower()][-4:])
print("  độ dài db_schemas:", len(",".join(f"api_t{s}" for s in range(1, N + 1))), "ký tự cho 50 tenant")
