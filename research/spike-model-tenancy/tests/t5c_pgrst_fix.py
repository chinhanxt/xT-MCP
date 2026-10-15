# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Sửa lỗ hổng t5b: xt_web KHÔNG là thành viên xt_app; view security_invoker=false, chủ xt_api_owner (NOBYPASSRLS),
policy SELECT + tenant áp cho xt_api_owner. Kiểm lại qua HTTP và SET ROLE."""
from common import *  # noqa
from psycopg import sql
import importlib.util, sys
spec_ = importlib.util.spec_from_file_location("t5", str(ROOT / "tests" / "t5_postgrest.py"))
with su() as x:
    x.execute("REVOKE xt_app FROM xt_web")
    x.execute("GRANT USAGE ON SCHEMA xt TO xt_web")   # chỉ để gọi xt.pgrst_pre()
    for s in (7, 8):
        t = sql.Identifier("xt_data", f"t{s}__model_00")
        x.execute(sql.SQL("ALTER POLICY p_p00_tenant ON {} TO xt_app, xt_workflow, xt_api_owner").format(t))
        x.execute(sql.SQL("ALTER POLICY p_p00_read ON {} TO xt_app, xt_workflow, xt_api_owner").format(t))
        x.execute(sql.SQL("ALTER VIEW {} SET (security_invoker = false)").format(sql.Identifier(f"api_t{s}", "model_00")))
    x.execute("NOTIFY pgrst, 'reload schema'")
    x.execute("ALTER ROLE xt_web LOGIN PASSWORD 'web'")
time.sleep(1.5)
import base64, hashlib, hmac, json, urllib.request, urllib.error
SECRET = "xt-spike-postgrest-secret-0123456789abcdef"
def jwt(seq):
    b = lambda o: base64.urlsafe_b64encode(json.dumps(o, separators=(",", ":")).encode()).rstrip(b"=")
    h, p = b({"alg": "HS256"}), b({"role": "xt_web", "aud": "xt-data", "sub": str(uuid.UUID(int=seq)), "tenant_id": str(tid(seq)),
                                    "tenant_seq": seq, "roles": ["accountant"], "exp": int(time.time()) + 600})
    return (h + b"." + p + b"." + base64.urlsafe_b64encode(hmac.new(SECRET.encode(), h + b"." + p, hashlib.sha256).digest()).rstrip(b"=")).decode()
def get(path, tok, prof):
    r = urllib.request.Request("http://localhost:15452" + path, headers={"Authorization": "Bearer " + tok, "Accept-Profile": prof})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.loads(x.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
import uuid
print("HTTP T7 + api_t7 ->", get("/model_00?select=truong_0", jwt(7), "api_t7"))
print("HTTP T8 + api_t8 ->", get("/model_00?select=truong_0", jwt(8), "api_t8"))
try:
    with psycopg.connect(DSN + " user=xt_web password=web") as c:
        with c.transaction():
            ctx(c, 7)
            print("xt_web ngữ cảnh T7 đọc api_t8.model_00 ->", c.execute("SELECT count(*) FROM api_t8.model_00").fetchone()[0], "dòng; api_t7.model_00 ->", c.execute("SELECT count(*) FROM api_t7.model_00").fetchone()[0])
        for q in ("SELECT count(*) FROM xt_data.t7__model_00", "SET ROLE xt_workflow", "SET ROLE xt_app"):
            try:
                with c.transaction():
                    c.execute(q)
                print(f"xt_web {q} -> ĐƯỢC (lỗi!)")
            except psycopg.Error as e:
                print(f"xt_web {q:42s} -> {type(e).__name__}: {e.diag.message_primary}")
finally:
    with su() as x:
        x.execute("ALTER ROLE xt_web NOLOGIN")
