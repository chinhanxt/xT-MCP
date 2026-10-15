# Spike: MCP server Python + Streamable HTTP + OAuth 2.1 (Keycloak)

Ngày chạy: 2026-10-15. Mọi kết quả dưới đây lấy từ lần chạy thật trong `/tmp/xt-spike-auth`; code/script lưu ở `research/spike-mcp-auth/`.

## 1. Phiên bản thực tế

| Thành phần | Phiên bản |
|---|---|
| `mcp` (SDK chính thức, PyPI) | **2.3.0**, giấy phép MIT |
| `fastmcp` (PrefectHQ) | **4.1.0**, giấy phép Apache-2.0 |
| Keycloak (`quay.io/keycloak/keycloak:latest`) | **26.8.1** (Quarkus 3.40.1, JDK 21), `start-dev` |
| PyJWT 2.15.1, httpx 0.28.1, uvicorn 0.54.0, Python 3.13 | |
| MCP Inspector (`npx @modelcontextprotocol/inspector`) | 2.11.0 |

## 2. SDK chính thức `mcp` 2.3.0: đọc source

Lưu ý: bản 2.x đổi API so với 1.x. `FastMCP` cũ nay là `MCPServer`.

- `from mcp.server.mcpserver import MCPServer` (ctor có `token_verifier=`, `auth=`, `extensions=`, `middleware=`).
- `from mcp.server.auth.settings import AuthSettings` (`issuer_url`, `resource_server_url`, `required_scopes`, `validate_token_resource`, `client_registration_options`...).
- `from mcp.server.auth.provider import TokenVerifier, AccessToken`. `TokenVerifier` là `Protocol` với `async verify_token(token) -> AccessToken | None`. `AccessToken` có `token, client_id, scopes, expires_at, resource, subject, claims`.
- `from mcp.server.auth.middleware.auth_context import get_access_token` (lấy token trong tool).
- Protected resource metadata (RFC 9728): `mcp.shared.auth.ProtectedResourceMetadata`, `mcp.server.auth.handlers.metadata.ProtectedResourceMetadataHandler`. Route tự sinh `/.well-known/oauth-protected-resource/<path của resource_server_url>`.
- Streamable HTTP: `MCPServer.streamable_http_app(streamable_http_path="/mcp", json_response=, stateless_http=, event_store=, ...)` và `run_streamable_http_async`. Client: `from mcp.client.streamable_http import streamable_http_client` (nhận `http_client=httpx.AsyncClient`, header Bearer đặt trên client này).
- Stateless: tham số `stateless_http=True` (đã chạy thật bên dưới).
- MCP Apps: **có sẵn trong core** `mcp.server.apps` (`Apps`, `EXTENSION_ID="io.modelcontextprotocol/ui"`, `APP_MIME_TYPE="text/html;profile=mcp-app"`); dùng `@apps.tool(resource_uri="ui://...")`, `apps.add_html_resource(...)`, `MCPServer(extensions=[apps])`, `client_supports_apps(ctx)`. Chưa chạy thử trong spike này (chỉ đọc source).
- Cờ `validate_token_resource`: nếu để `None` mà có `resource_server_url` thì SDK cảnh báo và hành vi như False; verifier tự kiểm `aud`.

## 3. So sánh `mcp` vs `fastmcp`

| Tiêu chí | `mcp` 2.3.0 | `fastmcp` 4.1.0 |
|---|---|---|
| Giấy phép | MIT | Apache-2.0 (khớp dự án) |
| Provider Keycloak | Không; tự viết `TokenVerifier` (~25 dòng, bên dưới) | **Có** `fastmcp.server.auth.providers.keycloak.KeycloakAuthProvider` (cần Keycloak >= 26.6; docstring nói liên quan fix DCR) |
| Lớp auth khác | `AuthSettings`, `TokenVerifier`, `OAuthAuthorizationServerProvider` | `RemoteAuthProvider`, `JWTVerifier`, `OIDCProxy` (`oidc_proxy.py`), `OAuthProxy`, `introspection.py`, `cimd.py`, auth0/azure/google/github/... |
| Protected resource metadata | Có, tự sinh | Có, tự sinh |
| MCP Apps (`ui://`) | Có trong core (`mcp.server.apps`) | Có (`fastmcp/apps/`, `prefab_synthesis`) |
| Stateless HTTP | `stateless_http=True` | `mcp.run(..., stateless_http=True)` (đã chạy) |
| Mức phụ thuộc | Ít | Nhiều hơn (authlib, ...) |
| Rủi ro | API v2 mới, đã đổi tên so với 1.x | Bề mặt API lớn, đổi nhanh |

Đã kiểm chứng `fastmcp` bằng `server_fastmcp.py` (port 18001): 401 kèm `WWW-Authenticate: Bearer scope="openid", resource_metadata="http://localhost:18001/.well-known/oauth-protected-resource/mcp"`, PRM trả đúng, `initialize` với token Keycloak thật trả 200. Không chạy full e2e cho fastmcp.

**Khuyến nghị:** dùng **SDK chính thức `mcp`** cho lõi xT (protocol bám sát spec, ít phụ thuộc, Apps nằm sẵn trong core, verifier tự viết chỉ ~25 dòng nên kiểm soát được `iss/aud/exp`). Giữ `fastmcp` làm phương án dự phòng: nếu cần OIDCProxy (IdP không hỗ trợ DCR) hoặc muốn `KeycloakAuthProvider` dựng sẵn. Cả hai đều OSI (MIT/Apache-2.0). Đây là đánh giá từ spike nhỏ, chưa so sánh hiệu năng.

## 4. Keycloak setup (script, không click tay)

Chạy Keycloak:
```bash
docker run -d --name xt-kc -p 18080:8080 -e KC_BOOTSTRAP_ADMIN_USERNAME=admin -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin \
  quay.io/keycloak/keycloak:latest start-dev
```
Script `kc_setup.py` (Admin REST API; idempotent; cũng export realm vào `realm-xt-export.json`):
```python
# SPDX-License-Identifier: Apache-2.0
"""Cấu hình Keycloak cho xT spike qua Admin REST API (không click tay). Idempotent."""
import json, sys, httpx

KC = "http://localhost:18080"
MCP_URL = "http://localhost:18000/mcp"   # resource identifier = audience
REALM = "xt"
c = httpx.Client(base_url=KC, timeout=30)

def admin_token():
    r = c.post("/realms/master/protocol/openid-connect/token", data=dict(
        grant_type="password", client_id="admin-cli", username="admin", password="admin"))
    r.raise_for_status(); return r.json()["access_token"]

c.headers["Authorization"] = f"Bearer {admin_token()}"
def ok(r, *allowed):
    if r.status_code not in (200, 201, 204, 409, *allowed):
        sys.exit(f"FAIL {r.request.method} {r.request.url} -> {r.status_code} {r.text}")
    return r

# 1. realm
ok(c.post("/admin/realms", json=dict(realm=REALM, enabled=True, sslRequired="none",
    accessTokenLifespan=900)))
# 2. user
ok(c.post(f"/admin/realms/{REALM}/users", json=dict(
    username="alice", email="alice@example.com", emailVerified=True,
    firstName="Alice", lastName="Nguyen", enabled=True,
    credentials=[dict(type="password", value="alice", temporary=False)])))
# 3. client scope mcp-xt + audience mapper
ok(c.post(f"/admin/realms/{REALM}/client-scopes", json=dict(
    name="mcp-xt", protocol="openid-connect",
    attributes={"include.in.token.scope": "true", "display.on.consent.screen": "false"})))
scopes = c.get(f"/admin/realms/{REALM}/client-scopes").json()
sid = next(s["id"] for s in scopes if s["name"] == "mcp-xt")
ok(c.post(f"/admin/realms/{REALM}/client-scopes/{sid}/protocol-mappers/models", json=dict(
    name="mcp-audience", protocol="openid-connect", protocolMapper="oidc-audience-mapper",
    config={"included.custom.audience": MCP_URL, "id.token.claim": "false",
            "access.token.claim": "true", "introspection.token.claim": "true"})))
# 4. mcp-xt thành realm default scope => mọi client (kể cả DCR) tự nhận
ok(c.put(f"/admin/realms/{REALM}/default-default-client-scopes/{sid}"))
print("realm/user/scope OK; scope id", sid)

# 5. DCR anonymous: Trusted Hosts policy. ChatGPT/Claude đăng ký từ IP cloud không biết trước =>
#    tắt host-sending-registration-request-must-match, giữ client-uris-must-match + whitelist domain redirect.
comps = c.get(f"/admin/realms/{REALM}/components",
              params={"type": "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"}).json()
th = next(x for x in comps if x["providerId"] == "trusted-hosts" and x["subType"] == "anonymous")
th["config"] = {"host-sending-registration-request-must-match": ["false"],
                "client-uris-must-match": ["true"],
                "trusted-hosts": ["chatgpt.com", "claude.ai", "localhost", "127.0.0.1"]}
ok(c.put(f"/admin/realms/{REALM}/components/{th['id']}", json=th))
print("trusted-hosts policy updated")

# 6. export realm JSON (partial export: client scopes, users không gồm credential)
r = c.post(f"/admin/realms/{REALM}/partial-export", params={"exportClients": "true", "exportGroupsAndRoles": "true"})
ok(r)
json.dump(r.json(), open("realm-xt-export.json", "w"), indent=2)
print("exported realm-xt-export.json")
```

Output thật:
```
realm/user/scope OK; scope id 6c58ec4c-848c-4557-b38c-1a0670e00c4d
trusted-hosts policy updated
exported realm-xt-export.json
```
`realm-xt-export.json` là partial-export (không có user/credential, secrets bị mask). Chứa client scope `mcp-xt` và `mcp-xt` nằm trong `defaultDefaultClientScopes`.

### DCR và PKCE: kết quả quan sát
- Discovery `/.well-known/openid-configuration` của realm có `registration_endpoint` và `code_challenge_methods_supported: ['plain','S256']`.
- **DCR ẩn danh mặc định bị chặn** từ máy ngoài: `403 {"error":"insufficient_scope","error_description":"Policy 'Trusted Hosts' rejected request to client-registration service. Details: Host not trusted."}` (request đến từ IP gateway Docker 172.17.0.1).
- Policy anonymous mặc định: Trusted Hosts (`host-sending-registration-request-must-match=true`, `client-uris-must-match=true`), Consent Required, Full Scope Disabled, Max Clients 200, Allowed Client Scopes (`allow-default-scopes=true`), Allowed Protocol Mapper Types.
- Sau khi script đổi Trusted Hosts (`host-sending...=false`, `client-uris-must-match=true`, whitelist `chatgpt.com, claude.ai, localhost, 127.0.0.1`):
  - DCR redirect ChatGPT: `HTTP/1.1 201 Created`.
  - DCR redirect `https://evil.example/cb`: `403 ... URI doesn't match any trusted host or trusted domain`.
- Client DCR ẩn danh là public client (`token_endpoint_auth_method":"none"`), PKCE S256 dùng được. Dòng `token_endpoint_auth_methods_supported` trong discovery KHÔNG liệt kê `none`, nhưng DCR với `none` vẫn thành công và đổi code lấy token chỉ với `client_id` + `code_verifier`.
- Do policy Consent Required, user luôn thấy trang consent của Keycloak sau khi đăng nhập (đã xử lý trong e2e).

## 5. MCP server (SDK chính thức)

`server.py`:
```python
# SPDX-License-Identifier: Apache-2.0
"""xT spike: MCP server (official SDK mcp 2.x) + Keycloak JWT verify qua JWKS."""
import os
import jwt  # PyJWT
from jwt import PyJWKClient
import anyio
from pydantic import AnyHttpUrl
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer

ISSUER = os.environ.get("XT_ISSUER", "http://localhost:18080/realms/xt")
RESOURCE = os.environ.get("XT_RESOURCE", "http://localhost:18000/mcp")  # = audience
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
PORT = int(os.environ.get("XT_PORT", "18000"))


class KeycloakVerifier:
    """TokenVerifier: chữ ký (JWKS), exp, iss, aud đều được kiểm tra."""

    def __init__(self) -> None:
        self._jwks = PyJWKClient(JWKS_URL, cache_keys=True, lifespan=300)

    def _decode(self, token: str) -> dict:
        key = self._jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=["RS256", "ES256", "PS256"],
            audience=RESOURCE, issuer=ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except Exception as e:  # sai chữ ký / hết hạn / sai iss / sai aud / không lấy được JWKS
            print(f"[auth] reject: {type(e).__name__}: {e}", flush=True)
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("azp", ""),
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            resource=RESOURCE,          # đã verify aud ở trên
            subject=claims.get("sub"),
            claims=claims,
        )


mcp = MCPServer(
    "xt-spike",
    token_verifier=KeycloakVerifier(),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER),
        resource_server_url=AnyHttpUrl(RESOURCE),
        required_scopes=["openid"],
        validate_token_resource=False,  # verifier tự check aud
    ),
)


@mcp.tool()
def whoami() -> dict:
    """Trả về claims của access token hiện tại."""
    at = get_access_token()
    return {"subject": at.subject, "client_id": at.client_id, "scopes": at.scopes, "claims": at.claims}


if __name__ == "__main__":
    import uvicorn
    app = mcp.streamable_http_app(stateless_http=True, json_response=True, host="0.0.0.0")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
```

Chạy: `.venv/bin/python src/server.py` (port 18000).

## 6. Test

### (a) Không token -> 401
```
$ curl -s -i -X POST localhost:18000/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize",...}'
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer error="invalid_token", error_description="Authentication required", resource_metadata="http://localhost:18000/.well-known/oauth-protected-resource/mcp"

{"error": "invalid_token", "error_description": "Authentication required"}
```

### (b) Discovery chain
```
$ curl -s localhost:18000/.well-known/oauth-protected-resource/mcp
{"resource":"http://localhost:18000/mcp","authorization_servers":["http://localhost:18080/realms/xt"],"scopes_supported":["openid"],"bearer_methods_supported":["header"]} [200]
$ curl -s localhost:18000/.well-known/oauth-protected-resource      -> 404 (chỉ có bản có path /mcp)
$ curl .../.well-known/oauth-authorization-server (trên MCP server) -> 404 (RS-only, đúng thiết kế)
```
Tiếp theo e2e lấy `http://localhost:18080/realms/xt/.well-known/openid-configuration` -> authorization/token/registration endpoint (xem output (c)).

### (c) DCR + auth-code PKCE + MCP client
DCR bằng curl (`dcr.sh`):
```bash
#!/usr/bin/env bash
# Usage: dcr.sh <name> <redirect_uri>   -> in JSON kết quả DCR
curl -s -i -X POST http://localhost:18080/realms/xt/clients-registrations/openid-connect \
  -H 'Content-Type: application/json' \
  -d "{\"client_name\":\"$1\",\"redirect_uris\":[\"$2\"],\"grant_types\":[\"authorization_code\",\"refresh_token\"],\"response_types\":[\"code\"],\"token_endpoint_auth_method\":\"none\"}"
```

`e2e.py` làm cả chuỗi: 401 -> PRM -> openid-configuration -> DCR -> authorize (PKCE S256 + `resource`) -> đăng nhập form + consent -> đổi code -> gọi MCP bằng `streamable_http_client` + `ClientSession`:
```python
# SPDX-License-Identifier: Apache-2.0
"""E2E: discovery -> DCR -> auth-code+PKCE (script login form) -> token -> MCP client gọi whoami."""
import asyncio, base64, hashlib, json, os, re, secrets, sys, html
from urllib.parse import urlparse, parse_qs, urlencode
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MCP = "http://localhost:18000/mcp"
REDIRECT = os.environ.get("REDIRECT", "https://chatgpt.com/connector_platform_oauth_redirect")
USE_RESOURCE = os.environ.get("USE_RESOURCE", "1") == "1"
SCOPE = os.environ.get("SCOPE", "openid")

async def main():
    h = httpx.Client(timeout=30)
    # 1. 401 -> PRM -> AS metadata
    r = h.post(MCP, json={}, headers={"Accept": "application/json, text/event-stream"})
    prm_url = re.search(r'resource_metadata="([^"]+)"', r.headers["www-authenticate"]).group(1)
    prm = h.get(prm_url).json(); print("PRM:", prm)
    iss = prm["authorization_servers"][0]
    md = h.get(f"{iss}/.well-known/openid-configuration").json()
    print("AS endpoints:", md["authorization_endpoint"], md["token_endpoint"], md["registration_endpoint"])
    # 2. DCR
    reg = h.post(md["registration_endpoint"], json=dict(client_name="e2e", redirect_uris=[REDIRECT],
        grant_types=["authorization_code", "refresh_token"], response_types=["code"],
        token_endpoint_auth_method="none")).json()
    cid = reg["client_id"]; print("DCR client_id:", cid)
    # 3. auth code + PKCE
    ver = secrets.token_urlsafe(48)
    chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
    q = dict(response_type="code", client_id=cid, redirect_uri=REDIRECT, scope=SCOPE, state="s1",
             code_challenge=chal, code_challenge_method="S256")
    if USE_RESOURCE: q["resource"] = prm["resource"]
    r = h.get(md["authorization_endpoint"] + "?" + urlencode(q), follow_redirects=True)
    form = re.search(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"', r.text)
    if not form: sys.exit("no login form:\n" + r.text[:1500])
    # KC 26.8 gắn cookie Secure kể cả trên http://localhost: trình duyệt chấp nhận, cookiejar Python thì không gửi lại
    ck = "; ".join(f"{c.name}={c.value}" for c in h.cookies.jar)
    r = h.post(html.unescape(form.group(1)), data=dict(username="alice", password="alice", credentialId=""), headers={"Cookie": ck})
    # Consent: policy "Consent Required" của DCR anonymous => KC hiện trang OAUTH_GRANT (ChatGPT/Claude sẽ hiện trang này cho user)
    cookies = lambda: "; ".join(f"{c.name}={c.value}" for c in h.cookies.jar)
    if "required-action" in r.headers.get("location", ""):
        page = h.get(r.headers["location"], headers={"Cookie": cookies()})
        print("CONSENT page:", re.findall(r'<title>([^<]*)', page.text), "scopes shown:", re.findall(r'kc-grant-scope[^>]*>([^<]*)|<span[^>]*>([^<]*)</span>\s*</li>', page.text)[:6])
        act = "http://localhost:18080" + html.unescape(re.search(r'<form[^>]*action="([^"]+)"', page.text).group(1))
        r = h.post(act, data=dict(accept="Yes"), headers={"Cookie": cookies()})
    loc = r.headers.get("location", "")
    if not loc.startswith(REDIRECT): sys.exit(f"unexpected {r.status_code} {loc}\n{r.text[:1500]}")
    cb = parse_qs(urlparse(loc).query); print("callback:", {k: v[0][:30] for k, v in cb.items()})
    assert cb["state"][0] == "s1"
    # 4. token
    tk = h.post(md["token_endpoint"], data=dict(grant_type="authorization_code", code=cb["code"][0],
        redirect_uri=REDIRECT, client_id=cid, code_verifier=ver)).json()
    if "access_token" not in tk: sys.exit(f"token error {tk}")
    at = tk["access_token"]
    pl = json.loads(base64.urlsafe_b64decode(at.split(".")[1] + "=="))
    print("token aud/iss/scope/azp:", pl.get("aud"), pl["iss"], pl.get("scope"), pl.get("azp"))
    # 5. MCP client
    hc = httpx.AsyncClient(headers={"Authorization": f"Bearer {at}"}, timeout=30)
    async with streamable_http_client(MCP, http_client=hc) as (rd, wr, *_):
        async with ClientSession(rd, wr) as s:
            await s.initialize()
            print("tools:", [t.name for t in (await s.list_tools()).tools])
            res = await s.call_tool("whoami", {})
            print("whoami:", json.dumps(res.structured_content or res.content[0].text, indent=1)[:1800])

asyncio.run(main())
```

Output thật (rút gọn phần claims):
```
PRM: {'resource': 'http://localhost:18000/mcp', 'authorization_servers': ['http://localhost:18080/realms/xt'], 'scopes_supported': ['openid'], 'bearer_methods_supported': ['header']}
AS endpoints: http://localhost:18080/realms/xt/protocol/openid-connect/auth http://localhost:18080/realms/xt/protocol/openid-connect/token http://localhost:18080/realms/xt/clients-registrations/openid-connect
DCR client_id: 40e140bf-89f9-4b85-9854-b196440769fb
CONSENT page: ['Sign in to xt'] scopes shown: User profile / User roles / Email address
callback: {'state': 's1', 'session_state': '...', 'iss': 'http://localhost:18080/realms/', 'code': '...'}
token aud/iss/scope/azp: http://localhost:18000/mcp http://localhost:18080/realms/xt openid profile mcp-xt email 40e140bf-...
tools: ['whoami']
whoami: {"subject": "77c2f82a-740c-45f0-87eb-2b28af520074", "client_id": "40e140bf-...", "scopes": ["openid","profile","mcp-xt","email"], "claims": {"iss": "http://localhost:18080/realms/xt", "aud": "http://localhost:18000/mcp", "sub": "77c2f82a-...", "azp": "40e140bf-...", "typ": "Bearer", ... } }
```
- Chạy lại với `USE_RESOURCE=0` (không gửi tham số `resource`): token vẫn có `aud=http://localhost:18000/mcp` (nhờ audience mapper ở realm default scope) và tool vẫn trả claims.
- Chạy với `REDIRECT=https://claude.ai/api/mcp/auth_callback`: DCR thành công, token và `whoami` đều chạy.
- Lưu ý: tôi KHÔNG thử tính năng nhận `resource` của Keycloak (RFC 8707) một cách riêng. Chỉ biết audience đúng nhờ mapper, không phụ thuộc `resource`.

### Negative tests (`negative.py`)
```python
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
```

Output thật:
```
valid token         -> 200
no audience         -> 401
master-realm token  -> 401
expired             -> 401
garbage             -> 401
no token            -> 401
```
Log server tương ứng: `InvalidAudienceError: Audience doesn't match`, `PyJWKClientError: Unable to find a signing key...` (token realm khác), `ExpiredSignatureError`, `DecodeError`.

### (d) MCP Inspector CLI 2.11.0
```
$ npx -y @modelcontextprotocol/inspector --cli http://localhost:18000/mcp --transport http --header "Authorization: Bearer $TOK" --method tools/list
{"tools":[{"name":"whoami","description":"Trả về claims của access token hiện tại.","inputSchema":{"type":"object","properties":{},"title":"whoamiArguments"}}]}   (đã định dạng lại)
$ ... --method tools/call --tool-name whoami
{"content":[{"type":"text","text":"{\"subject\": \"77c2f82a-...\", \"client_id\": \"t-ok\", \"scopes\": [\"openid\",\"profile\",\"mcp-xt\",\"email\"], \"claims\": {... \"aud\": [\"http://localhost:18000/mcp\",\"account\"] ...
$ (không token) ... --method tools/list
{"error":{"code":"auth_required","message":"Interactive OAuth requires a TTY on stdin or stderr (or MCP_AUTO_OPEN_ENABLED=true). For CI/non-interactive runs use --stored-auth-only."}}
```
Luồng OAuth tương tác của Inspector (trình duyệt) chưa chạy; chỉ test với Bearer token.

## 7. Redirect URI của ChatGPT / Claude (kiểm bằng web search; chưa đối chiếu trực tiếp tài liệu OpenAI)

- ChatGPT: `https://chatgpt.com/connector_platform_oauth_redirect`. Nhiều nguồn bên thứ ba (Qlik, Microsoft Sentinel, Pricefx, Jama, PR gamelib-mcp) ghi URL cố định này là giá trị hiện hành; các hướng dẫn cũ dùng dạng theo-connector `https://chatgpt.com/connector/oauth/<id>`. Qlik nói ChatGPT có thể sinh callback riêng cho mỗi connector mới, nên coi giá trị hiển thị trong phần OAuth nâng cao của ChatGPT là chuẩn. **Trạng thái: chưa xác minh từ nguồn chính thức OpenAI.**
- Claude (web, Desktop, mobile): `https://claude.ai/api/mcp/auth_callback`. Tài liệu Anthropic "Authentication for connectors" (claude.com/docs/connectors/building/authentication) nêu hỗ trợ `oauth_dcr` và `oauth_cimd`; kết quả tìm kiếm chỉ cho tóm tắt, chưa đọc trọn trang callback. Claude Code dùng loopback `http://localhost:<port>/callback` / `http://127.0.0.1:<port>/callback` (nguồn bên thứ ba). **Trạng thái: URL khớp nhiều nguồn, chưa đọc trọn trang chính thức.**
- Trong spike, Keycloak chấp nhận DCR với cả hai URL trên (sau khi whitelist `chatgpt.com`, `claude.ai` trong Trusted Hosts). Chưa thử với ChatGPT/Claude thật (cần URL công khai HTTPS).

## 8. Pitfalls đã gặp / quan sát

1. **Audience**: Access token mặc định của Keycloak KHÔNG có `aud` = URL MCP. Cần client scope + `oidc-audience-mapper` (`included.custom.audience` = đúng `resource_server_url`) và gán vào **realm default client scopes** thì client DCR mới tự nhận. Token không có scope này bị server từ chối (`InvalidAudienceError`, 401). `aud` có thể là list (`[mcp, "account"]`), PyJWT xử lý được.
2. **DCR ẩn danh bị Trusted Hosts chặn mặc định** (403 `Host not trusted`). ChatGPT/Claude gọi từ IP cloud không biết trước nên phải đặt `host-sending-registration-request-must-match=false` và giữ `client-uris-must-match=true` với whitelist domain redirect. Không xoá hẳn policy này, vì sẽ cho phép đăng ký redirect URI bất kỳ (đã kiểm: `evil.example` bị 403 khi policy còn).
3. **Consent Required** (policy anonymous) khiến user luôn thấy trang consent. Cần biết khi viết script test hoặc demo.
4. **Cookie `Secure` trên http**: Keycloak 26.8.1 đặt `Secure; SameSite=None` ngay cả trên `http://localhost`. Trình duyệt chấp nhận localhost, nhưng cookiejar Python (httpx) không gửi lại, gây `LOGIN_ERROR cookie_not_found` / HTTP 400. Script e2e phải tự chuyển cookie vào header `Cookie`. Production dùng HTTPS nên không gặp.
5. **`issuer`**: JWT `iss` phải đúng chuỗi với URL mà server cấu hình (`http://localhost:18080/realms/xt`). Khi đặt Keycloak sau reverse proxy cần `KC_HOSTNAME` đúng, nếu không `iss` sẽ lệch và verify fail (không test trong spike).
6. Callback của Keycloak có tham số `iss` (RFC 9207) vì `authorization_response_iss_parameter_supported: true`.
7. MCP server chỉ là Resource Server nên `/.well-known/oauth-authorization-server` trên MCP server trả 404; client phải đi qua PRM -> `authorization_servers` -> Keycloak. PRM chỉ có tại đường dẫn có hậu tố `/mcp`; `/.well-known/oauth-protected-resource` (không hậu tố) trả 404. Proxy/APISIX phía trước phải forward cả đường dẫn well-known đó.
8. `PyJWKClient` là đồng bộ (blocking I/O) nên bọc `anyio.to_thread.run_sync`. Khi JWKS không tới được, verifier trả None -> 401.
9. Bản `mcp` 2.x: `FastMCP` đổi thành `MCPServer`, client dùng `streamable_http_client` (không phải `streamablehttp_client` của 1.x) và `httpx2` nội bộ; tài liệu/ví dụ cũ trên mạng sẽ sai tên.
10. Inspector CLI không tự chạy OAuth khi không có TTY; dùng `--header "Authorization: Bearer ..."`.
11. Chưa kiểm: Claude "CIMD" (Client ID Metadata Document) với Keycloak; refresh token; `scope` negotiation (`scopes_supported` chỉ `openid`); chạy sau HTTPS/APISIX; MCP Apps chạy thật.

## 9. File đính kèm (`research/spike-mcp-auth/`)
`kc_setup.py`, `server.py`, `server_fastmcp.py`, `e2e.py`, `negative.py`, `dcr.sh`, `realm-xt-export.json`, `server.log.txt`.

Dọn dẹp: container `xt-kc` đã `docker rm -f`; server Python đã dừng.
