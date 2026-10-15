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
