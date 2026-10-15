# SPDX-License-Identifier: Apache-2.0
"""Cấu hình realm xt-hitl qua Admin REST API: CIBA (poll), client step-up (PKCE + LoA + AMR), TOTP đã biết."""
import json, sys, httpx
from common import KC, REALM, CIBA_CLIENT, CIBA_SECRET, AGENT_CLIENT, AGENT_SECRET, TOTP_RAW, CONFIRM_CB, DESK_CB

c = httpx.Client(base_url=KC, timeout=30)
tok = c.post("/realms/master/protocol/openid-connect/token", data=dict(
    grant_type="password", client_id="admin-cli", username="admin", password="admin")).json()["access_token"]
c.headers["Authorization"] = f"Bearer {tok}"
A = f"/admin/realms/{REALM}"

def ok(r, *allowed):
    if r.status_code not in (200, 201, 204, 409, *allowed):
        sys.exit(f"FAIL {r.request.method} {r.request.url} -> {r.status_code} {r.text}")
    return r

# 1. Realm + chính sách CIBA (poll mode, user hint = login_hint)
ok(c.post("/admin/realms", json=dict(realm=REALM, enabled=True, sslRequired="none",
    accessTokenLifespan=300,
    otpPolicyType="totp", otpPolicyAlgorithm="HmacSHA1", otpPolicyDigits=6, otpPolicyPeriod=30,
    attributes={"cibaBackchannelTokenDeliveryMode": "poll", "cibaExpiresIn": "120",
                "cibaInterval": "2", "cibaAuthRequestedUserHint": "login_hint",
                "acr.loa.map": json.dumps({"urn:xt:acr:pwd": 1, "urn:xt:acr:otp": 2})})))

# 2. Users: alice (người duyệt, có TOTP secret đã biết), bob (người đề nghị)
otp_cred = dict(type="otp", userLabel="xt-spike",
    secretData=json.dumps({"value": TOTP_RAW}),
    credentialData=json.dumps({"subType": "totp", "digits": 6, "counter": 0, "period": 30,
                               "algorithm": "HmacSHA1"}))
ok(c.post(f"{A}/users", json=dict(username="alice", email="alice@example.com", emailVerified=True,
    firstName="Alice", lastName="Nguyen", enabled=True,
    credentials=[dict(type="password", value="alice-pw", temporary=False), otp_cred])))
ok(c.post(f"{A}/users", json=dict(username="bob", email="bob@example.com", emailVerified=True,
    firstName="Bob", lastName="Tran", enabled=True,
    credentials=[dict(type="password", value="bob-pw", temporary=False)])))
alice = c.get(f"{A}/users", params={"username": "alice", "exact": "true"}).json()[0]
print("alice credentials:", [x["type"] for x in c.get(f"{A}/users/{alice['id']}/credentials").json()])

# 3. Flow step-up theo Level of Authentication: LoA1 = password, LoA2 = OTP (max-age 0 => luôn hỏi lại)
FLOW = "xt-stepup"
ok(c.post(f"{A}/authentication/flows", json=dict(alias=FLOW, providerId="basic-flow",
    topLevel=True, builtIn=False, description="xT confirm: pwd + OTP tươi")))
def sub(parent, alias):
    ok(c.post(f"{A}/authentication/flows/{parent}/executions/flow",
              json=dict(alias=alias, type="basic-flow", provider="registration-page-form")))
def exe(parent, provider):
    ok(c.post(f"{A}/authentication/flows/{parent}/executions/execution", json=dict(provider=provider)))
def execs(): return c.get(f"{A}/authentication/flows/{FLOW}/executions").json()
def setreq(match, req):
    for e in execs():
        if match(e):
            e["requirement"] = req
            ok(c.put(f"{A}/authentication/flows/{FLOW}/executions", json=e)); return e
    sys.exit(f"no exec for {req}")

if not any(e.get("displayName") == "xt-auth" for e in execs()):
    exe(FLOW, "auth-cookie")
    sub(FLOW, "xt-auth")
    sub("xt-auth", "xt-loa1"); sub("xt-auth", "xt-loa2")
    exe("xt-loa1", "conditional-level-of-authentication"); exe("xt-loa1", "auth-username-password-form")
    exe("xt-loa2", "conditional-level-of-authentication"); exe("xt-loa2", "auth-otp-form")
    setreq(lambda e: e.get("providerId") == "auth-cookie", "ALTERNATIVE")
    setreq(lambda e: e.get("displayName") == "xt-auth", "ALTERNATIVE")
    setreq(lambda e: e.get("displayName") == "xt-loa1", "CONDITIONAL")
    setreq(lambda e: e.get("displayName") == "xt-loa2", "CONDITIONAL")
    for e in execs():
        if e.get("providerId") in ("conditional-level-of-authentication", "auth-username-password-form", "auth-otp-form"):
            e["requirement"] = "REQUIRED"; ok(c.put(f"{A}/authentication/flows/{FLOW}/executions", json=e))
    loas = [e for e in execs() if e.get("providerId") == "conditional-level-of-authentication"]
    for lvl, e in zip((1, 2), loas):
        ok(c.post(f"{A}/authentication/executions/{e['id']}/config", json=dict(alias=f"xt-loa{lvl}-cond",
            config={"loa-condition-level": str(lvl), "loa-max-age": "36000" if lvl == 1 else "0"})))
    # Authentication Reference cho AMR mapper
    for e in execs():
        ref = {"auth-username-password-form": "pwd", "auth-otp-form": "otp"}.get(e.get("providerId"))
        if ref:
            ok(c.post(f"{A}/authentication/executions/{e['id']}/config", json=dict(alias=f"xt-amr-{ref}",
                config={"default.reference.value": ref, "default.reference.maxAge": "0"})))
flow_id = next(f["id"] for f in c.get(f"{A}/authentication/flows").json() if f["alias"] == FLOW)
print("flow", FLOW, json.dumps([(e["level"], e.get("displayName"), e["requirement"]) for e in execs()], ensure_ascii=False))

# 4. Clients
amr_mapper = dict(name="amr", protocol="openid-connect", protocolMapper="oidc-amr-mapper",
    config={"id.token.claim": "true", "access.token.claim": "true", "introspection.token.claim": "true"})
clients = [
  # Backend xT: client duy nhất được bật CIBA; KHÔNG có password/standard/service-account grant
  dict(clientId=CIBA_CLIENT, secret=CIBA_SECRET, publicClient=False, standardFlowEnabled=False,
       directAccessGrantsEnabled=False, serviceAccountsEnabled=False,
       attributes={"oidc.ciba.grant.enabled": "true"}, protocolMappers=[amr_mapper]),
  # Agent (MCP client): token của agent, kể cả token mang sub=alice (worst case ủy quyền)
  dict(clientId=AGENT_CLIENT, secret=AGENT_SECRET, publicClient=False, standardFlowEnabled=False,
       directAccessGrantsEnabled=True, serviceAccountsEnabled=True, protocolMappers=[amr_mapper]),
  # Desk: phiên đăng nhập thường ngày của người dùng (browser flow mặc định)
  dict(clientId="xt-desk", publicClient=True, standardFlowEnabled=True, directAccessGrantsEnabled=False,
       redirectUris=[DESK_CB], attributes={"pkce.code.challenge.method": "S256"}, protocolMappers=[amr_mapper]),
  # Confirm: client public RIÊNG, PKCE S256 bắt buộc, flow step-up, ACR tối thiểu = otp
  dict(clientId="xt-confirm", publicClient=True, standardFlowEnabled=True, directAccessGrantsEnabled=False,
       redirectUris=[CONFIRM_CB], attributes={"pkce.code.challenge.method": "S256",
       "minimum.acr.value": "urn:xt:acr:otp"},
       authenticationFlowBindingOverrides={"browser": flow_id}, protocolMappers=[amr_mapper]),
]
for cl in clients:
    ok(c.post(f"{A}/clients", json=cl))
    print("client", cl["clientId"], "OK")

# 5. Cùng gắn reference cho browser flow mặc định (để AMR của Desk cũng có giá trị so sánh)
for e in c.get(f"{A}/authentication/flows/browser/executions").json():
    ref = {"auth-username-password-form": "pwd", "auth-otp-form": "otp"}.get(e.get("providerId"))
    if ref and not e.get("authenticationConfig"):
        ok(c.post(f"{A}/authentication/executions/{e['id']}/config", json=dict(alias=f"browser-amr-{ref}",
            config={"default.reference.value": ref, "default.reference.maxAge": "0"})))
print("setup done")
