#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm cấu hình client `xt-desk-agent` trên Keycloak thật (qua Admin REST + cổng APISIX) và tạo/xóa user test.

  desk_oidc_e2e.py check            # in PASS/FAIL, exit 1 nếu có FAIL
  desk_oidc_e2e.py create-user      # in JSON {username,password}; user KHÔNG có role approver
  desk_oidc_e2e.py delete-user NAME
"""
import json, os, re, secrets, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent
env = {}
for line in (DEPLOY / ".env").read_text().splitlines():
    m = re.match(r"([A-Z0-9_]+)=(.*)", line)
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"')
env.update({k: v for k, v in os.environ.items() if k.startswith(("XT_", "KC_", "GW"))})
REALM = env.get("XT_REALM", "xt")
KC = env.get("KC_ADMIN_URL") or f"http://127.0.0.1:{env.get('KC_HTTP_PORT', '18080')}"
GW = env.get("GW", "127.0.0.1:9080")
AUTH = env.get("XT_DOMAIN_AUTH", "xt-auth.freepro.online")
DESK = f"https://{env.get('XT_DOMAIN_DESK', 'xt.freepro.online')}"
CID = env.get("XT_DESK_AGENT_CLIENT_ID", "xt-desk-agent")
DEV = [u for u in env.get("XT_DESK_AGENT_DEV_REDIRECTS", "http://localhost:5173/auth/callback,http://127.0.0.1:5173/auth/callback").split(",") if u]
EXPECTED_REDIRECTS = sorted([f"{DESK}/auth/callback", *DEV])
APPROVER_ROLES = {"dept_head", "director", "chief_accountant", "payment_accountant", "hr", "hr_pii", "it_admin", "admin"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def http(method, url, body=None, headers=None, form=False):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as r:
            raw = r.read()
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def admin_token():
    s, _, b = http("POST", f"{KC}/realms/master/protocol/openid-connect/token",
                   {"grant_type": "password", "client_id": "admin-cli", "username": env.get("KC_BOOTSTRAP_ADMIN_USERNAME", "admin"),
                    "password": env["KC_BOOTSTRAP_ADMIN_PASSWORD"]}, form=True)
    return json.loads(b)["access_token"]


def adm(method, path, body=None):
    s, h, b = http(method, f"{KC}/admin/realms/{REALM}{path}", body, {"Authorization": "Bearer " + admin_token()})
    return s, (json.loads(b) if b and b[:1] in b"[{" else b.decode()), h


def gw(path, params=None, method="GET", body=None):
    ip = f"203.0.113.{secrets.randbelow(250) + 1}"
    url = f"http://{GW}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    return http(method, url, body, {"Host": AUTH, "CF-Connecting-IP": ip}, form=body is not None)


fails = 0


def check(ok, msg):
    global fails
    print(("  PASS  " if ok else "  FAIL  ") + msg)
    fails += 0 if ok else 1


def cmd_check():
    s, cl, _ = adm("GET", f"/clients?clientId={CID}")
    c = cl[0]
    cid = c["id"]
    print(f"== client {CID} (cấu hình Admin REST) ==")
    check(c["publicClient"] is True, "publicClient=true (không secret)")
    check(c.get("serviceAccountsEnabled") is False, "serviceAccountsEnabled=false (không service account)")
    check(c.get("standardFlowEnabled") is True and not c.get("implicitFlowEnabled") and not c.get("directAccessGrantsEnabled"),
          "chỉ authorization-code (implicit/ROPC tắt)")
    check(c["attributes"].get("pkce.code.challenge.method") == "S256", "pkce.code.challenge.method=S256")
    check(sorted(c["redirectUris"]) == EXPECTED_REDIRECTS and not any("*" in u for u in c["redirectUris"]),
          f"redirectUris đúng danh sách tài liệu, không wildcard: {sorted(c['redirectUris'])}")
    s, d, _ = adm("GET", f"/clients/{cid}/default-client-scopes")
    s, o, _ = adm("GET", f"/clients/{cid}/optional-client-scopes")
    check("mcp-xt" in [x["name"] for x in o] and "mcp-xt" not in [x["name"] for x in d], "mcp-xt là OPTIONAL scope (không default)")
    s, sa, _ = adm("GET", f"/clients/{cid}/service-account-user")
    check(s in (400, 404), f"không có service-account-user (HTTP {s})")

    print("== hành vi thật qua gateway (Host xt-auth) ==")
    base = f"/realms/{REALM}/protocol/openid-connect"
    ok_cb = f"{DESK}/auth/callback"
    common = {"client_id": CID, "response_type": "code", "scope": "openid mcp-xt", "state": "s", "redirect_uri": ok_cb}
    chal = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    s, h, b = gw(base + "/auth", {**common, "code_challenge": chal, "code_challenge_method": "S256"})
    check(s == 200, f"S256 + redirect đúng -> trang login (HTTP {s})")
    s, h, b = gw(base + "/auth", common)
    loc = h.get("Location", "")
    check((s in (302, 303) and "error=invalid_request" in loc and "code_challenge" in urllib.parse.unquote(loc).lower()) or s == 400,
          f"thiếu code_challenge bị từ chối (HTTP {s}, {urllib.parse.unquote(loc)[:120]})")
    s, h, b = gw(base + "/auth", {**common, "code_challenge": chal, "code_challenge_method": "plain"})
    loc = h.get("Location", "")
    check((s in (302, 303) and "error=" in loc) or s == 400, f"code_challenge_method=plain bị từ chối (HTTP {s}, {urllib.parse.unquote(loc)[:120]})")
    for bad in ("https://evil.example/auth/callback", f"{DESK}/auth/callback/../x", f"{DESK}.evil.com/auth/callback", "https://xt.freepro.online/other"):
        s, h, b = gw(base + "/auth", {**common, "redirect_uri": bad, "code_challenge": chal, "code_challenge_method": "S256"})
        check(s == 400 and b"redirect_uri" in b.lower(), f"redirect_uri lạ bị từ chối: {bad} (HTTP {s}, không redirect)")
    s, h, b = gw(base + "/token", {"grant_type": "client_credentials", "client_id": CID}, method="POST", body={"grant_type": "client_credentials", "client_id": CID})
    check(s in (400, 401), f"client_credentials bị từ chối (HTTP {s})")


def cmd_create_user():
    name = "e2e-" + secrets.token_hex(3)
    pw = secrets.token_urlsafe(18)
    s, r, h = adm("POST", "/users", {"username": name, "email": f"{name}@example.test", "firstName": "E2E", "lastName": "Desk",
                                      "enabled": True, "emailVerified": True, "requiredActions": [],
                                      "credentials": [{"type": "password", "value": pw, "temporary": False}]})
    assert s == 201, (s, r)
    uid = h["Location"].rsplit("/", 1)[1]
    s, roles, _ = adm("GET", f"/users/{uid}/role-mappings/realm")
    names = {x["name"] for x in roles}
    assert not (names & APPROVER_ROLES), names
    print(json.dumps({"username": name, "password": pw, "id": uid, "realm_roles": sorted(names)}))


def cmd_delete_user(name):
    s, u, _ = adm("GET", f"/users?username={name}&exact=true")
    for x in u:
        adm("DELETE", f"/users/{x['id']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["check"]:
        cmd_check()
        print(f"FAIL={fails}")
        sys.exit(1 if fails else 0)
    elif a[:1] == ["create-user"]:
        cmd_create_user()
    elif a[:1] == ["delete-user"]:
        cmd_delete_user(a[1])
    else:
        sys.exit(__doc__)
