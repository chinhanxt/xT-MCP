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
