# SPDX-License-Identifier: Apache-2.0
"""Negative tests: token sai aud / sai iss / hết hạn / không token -> 401. Cần Keycloak + server đang chạy."""
import time, httpx
KC="http://localhost:18080"; MCP="http://localhost:18000/mcp"
a=httpx.Client(base_url=KC,timeout=30)
tok=a.post("/realms/master/protocol/openid-connect/token",data=dict(grant_type="password",client_id="admin-cli",username="admin",password="admin")).json()
adm={"Authorization":"Bearer "+tok["access_token"]}
def mk(cid, attrs=None, drop_scope=None):
    a.post("/admin/realms/xt/clients",headers=adm,json=dict(clientId=cid,publicClient=True,directAccessGrantsEnabled=True,attributes=attrs or {}))
    uid=a.get("/admin/realms/xt/clients",headers=adm,params={"clientId":cid}).json()[0]["id"]
    if drop_scope:
        sid=next(s["id"] for s in a.get("/admin/realms/xt/client-scopes",headers=adm).json() if s["name"]==drop_scope)
        a.delete(f"/admin/realms/xt/clients/{uid}/default-client-scopes/{sid}",headers=adm)
def pw(cid): return a.post("/realms/xt/protocol/openid-connect/token",data=dict(grant_type="password",client_id=cid,username="alice",password="alice",scope="openid")).json()["access_token"]
def call(t):
    r=httpx.post(MCP,headers={"Authorization":"Bearer "+t,"Accept":"application/json, text/event-stream"},json={"jsonrpc":"2.0","id":1,"method":"tools/list"})
    return r.status_code
mk("t-ok"); mk("t-noaud",drop_scope="mcp-xt"); mk("t-short",attrs={"access.token.lifespan":"2"})
print("valid token         ->", call(pw("t-ok")), "(tools/list không init: 200 hoặc lỗi JSON-RPC đều = qua auth)")
print("no audience         ->", call(pw("t-noaud")))
print("master-realm token  ->", call(tok["access_token"]))
short=pw("t-short"); time.sleep(4)
print("expired             ->", call(short))
print("garbage             ->", call("abc.def.ghi"))
print("no token            ->", httpx.post(MCP,json={}).status_code)
