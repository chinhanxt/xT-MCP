#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Realm-as-code cho Keycloak (Admin REST, stdlib, idempotent).

    python3 deploy/keycloak/provision.py            # tạo/cập nhật realm
    XT_PROFILE=dev python3 deploy/keycloak/provision.py   # profile dev: thêm redirect loopback cho xt-desk-agent
    python3 deploy/keycloak/provision.py --export   # + xuất deploy/keycloak/realm-xt.json (đã bỏ secret)

Cấu hình từ deploy/.env + môi trường. Bí mật còn trống được sinh vào deploy/.env (không commit).
Nguồn quyết định: architecture-v2.md D9/D10, docs/identity.md, research/spike-hitl, research/spike-mcp-auth.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

DEPLOY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DEPLOY / "scripts"))
from envfile import ensure_env  # noqa: E402

# Role nghiệp vụ lấy từ packages/*/policies/iam.json (subjects "role:<tên>")
ROLES = {
    "employee": "Nhân viên",
    "dept_head": "Trưởng phòng (duyệt cấp 1)",
    "director": "Giám đốc (duyệt vượt ngưỡng)",
    "chief_accountant": "Kế toán trưởng",
    "payment_accountant": "Kế toán thanh toán",
    "hr": "Nhân sự",
    "hr_pii": "Nhân sự được xem dữ liệu cá nhân nhạy cảm",
    "it_admin": "Quản trị IT",
    "admin": "Quản trị xT",
    "agent": "Tác tử (service account của connector/agent)",
}
STEPUP_FLOW = "xt-stepup"
AMR_MAX_AGE = "300"
ACR_LOA = {"urn:xt:acr:pwd": 1, "urn:xt:acr:otp": 2}
# Redirect URI connector theo research/connector-requirements.md. Không wildcard: ChatGPT dùng URI cố định
# khi AS trả `iss` (RFC 9207, đã bật). Ép bởi client policy + schema ở gateway.
CONNECTOR_REDIRECTS = [
    "https://chatgpt.com/connector_platform_oauth_redirect",
    "https://claude.ai/api/mcp/auth_callback",
    "http://127.0.0.1:<port>/callback",
]


class KC:
    def __init__(self, base: str, user: str, password: str) -> None:
        self.base, self.user, self.password = base.rstrip("/"), user, password
        self.token = ""

    def login(self) -> None:
        data = urllib.parse.urlencode(
            {"grant_type": "password", "client_id": "admin-cli", "username": self.user, "password": self.password}
        ).encode()
        req = urllib.request.Request(f"{self.base}/realms/master/protocol/openid-connect/token", data=data)
        with urllib.request.urlopen(req, timeout=30) as r:
            self.token = json.load(r)["access_token"]

    def wait_ready(self, timeout: int = 180) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                urllib.request.urlopen(f"{self.base}/realms/master", timeout=5).read()
                self.login()
                return
            except (urllib.error.URLError, OSError, KeyError):
                time.sleep(3)
        sys.exit(f"Keycloak {self.base} không sẵn sàng sau {timeout}s")

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = ()) -> Any:
        for attempt in (0, 1):
            req = urllib.request.Request(
                f"{self.base}/admin{path}",
                method=method,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self.login()
                    continue
                if e.code in ok:
                    return None
                sys.exit(f"FAIL {method} {path} -> {e.code} {e.read().decode()[:500]}")
        return None

    def get(self, path: str, **kw: Any) -> Any:
        return self.call("GET", path, **kw)


def env_list(env: dict[str, str], key: str, default: str = "") -> list[str]:
    return [x.strip() for x in env.get(key, default).replace(" ", ",").split(",") if x.strip()]


DEV_AGENT_REDIRECTS = "http://localhost:5173/auth/callback,http://127.0.0.1:5173/auth/callback"


def is_dev_profile(env: dict[str, str]) -> bool:
    """Chỉ `XT_PROFILE=dev` (hoặc cờ `--dev`) mới là dev. Mặc định/giá trị lạ = production (không loopback)."""
    return env.get("XT_PROFILE", "").strip().lower() == "dev" or "--dev" in sys.argv[1:]


def desk_agent_redirects(env: dict[str, str], desk: str) -> list[str]:
    """Redirect URI của xt-desk-agent. Production: chỉ `{desk}/auth/callback`, KHÔNG loopback (docs/decisions.md, Tier PII).
    Dev: thêm localhost/127.0.0.1 (XT_DESK_AGENT_DEV_REDIRECTS, mặc định vite :5173)."""
    uris = [f"{desk}/auth/callback"]
    if is_dev_profile(env):
        uris += [u for u in env_list(env, "XT_DESK_AGENT_DEV_REDIRECTS", DEV_AGENT_REDIRECTS) if u not in uris]
    return uris


def cli_client_rep(env: dict[str, str]) -> dict[str, Any]:
    """Client headless cho người/agent chạy curl (docs/decisions.md, Token headless): PUBLIC + auth-code + PKCE S256,
    redirect loopback RFC 8252 (Keycloak bỏ qua cổng với 127.0.0.1/[::1]). Không secret, không client_credentials
    (service account không đại diện người nào: REST từ chối invalid_behalf), không ROPC/implicit, không full scope.
    KHÔNG nhận mcp-xt; chỉ nhận xt-data/xt-workflow (optional) qua provision_data.py."""
    return {
        "clientId": env.get("XT_CLI_CLIENT_ID", "xt-cli"), "name": "xT CLI (agent/curl headless, auth-code + PKCE)",
        "publicClient": True, "standardFlowEnabled": True, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False, "fullScopeAllowed": False,
        "redirectUris": ["http://127.0.0.1/callback", "http://[::1]/callback"], "webOrigins": [],
        "attributes": {"pkce.code.challenge.method": "S256", "oauth2.device.authorization.grant.enabled": "false"}}


def dpop_required(env: dict[str, str]) -> bool:
    """`XT_DPOP_REQUIRED` (cùng cờ với xt-api, mặc định tắt): bật thì Keycloak từ chối token request của
    xt-desk-agent không có DPoP proof. Tắt thì Desk gửi proof vẫn được token gắn khóa (`cnf.jkt`)."""
    return env.get("XT_DPOP_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")


def desk_agent_client_rep(env: dict[str, str], desk: str, mappers: list[dict[str, Any]]) -> dict[str, Any]:
    """Client chat của Desk: public + PKCE S256, redirect không loopback ở production, DPoP (RFC 9449).
    Attribute Keycloak 26: `dpop.bound.access.tokens` (OIDCConfigAttributes.DPOP_BOUND_ACCESS_TOKENS,
    "Require DPoP bound tokens" trong Admin Console)."""
    redirects = desk_agent_redirects(env, desk)
    origins = desk_agent_web_origins(desk, redirects)
    return {
        "clientId": env.get("XT_DESK_AGENT_CLIENT_ID", "xt-desk-agent"), "name": "xT Desk agent (chat thay mặt người dùng)",
        "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "implicitFlowEnabled": False, "redirectUris": redirects, "webOrigins": origins,
        "attributes": {"pkce.code.challenge.method": "S256",
                       "dpop.bound.access.tokens": "true" if dpop_required(env) else "false",
                       # RP-initiated logout của Desk (audit DPA-03): chỉ về trang gốc của chính origin Desk.
                       "post.logout.redirect.uris": "##".join(f"{o}/" for o in origins)},
        "protocolMappers": mappers}


def mcp_client_ids(env: dict[str, str]) -> list[str]:
    """Mọi client thuộc bề mặt MCP/Desk/IdP-nội-bộ: KHÔNG bao giờ được nhận scope xt-data/xt-workflow."""
    return [*env_list(env, "XT_MCP_HUMAN_CLIENTS", "xt-desk"), env.get("XT_DESK_AGENT_CLIENT_ID", "xt-desk-agent"),
            env.get("XT_CONFIRM_CLIENT_ID", "xt-desk-confirm"), env.get("XT_IDP_INTROSPECTION_CLIENT_ID", "xt-api-introspect"),
            env.get("XT_IDP_ADMIN_CLIENT_ID", "xt-client-registry")]


def desk_agent_web_origins(desk: str, redirects: list[str]) -> list[str]:
    return [desk, *dict.fromkeys(u.rsplit("/auth/callback", 1)[0] for u in redirects if not u.startswith(desk + "/"))]


def upsert_realm(kc: KC, realm: str, env: dict[str, str]) -> None:
    rep = {
        "realm": realm,
        "displayName": "xT",
        "enabled": True,
        "organizationsEnabled": True,          # tenant = Organization (D9)
        "sslRequired": "external",
        "registrationAllowed": False,
        "resetPasswordAllowed": True,
        "loginWithEmailAllowed": True,
        "bruteForceProtected": True,
        "accessTokenLifespan": 300,            # identity.md: trần thu hồi
        "revokeRefreshToken": True,            # refresh token rotation
        "refreshTokenMaxReuse": 0,
        "passwordPolicy": "length(12) and upperCase(1) and lowerCase(1) and digits(1) and notUsername and notEmail and passwordHistory(3)",
        "otpPolicyType": "totp",
        "otpPolicyAlgorithm": "HmacSHA1",
        "otpPolicyDigits": 6,
        "otpPolicyPeriod": 30,
        "attributes": {"acr.loa.map": json.dumps(ACR_LOA)},
    }
    if kc.get(f"/realms/{realm}", ok=(404,)) is None:
        kc.call("POST", "/realms", rep)
        print(f"realm {realm}: đã tạo")
    else:
        old = kc.get(f"/realms/{realm}")
        rep["attributes"] = {**old.get("attributes", {}), **rep["attributes"]}
        kc.call("PUT", f"/realms/{realm}", rep)
        print(f"realm {realm}: đã cập nhật")


def ensure_roles(kc: KC, realm: str) -> None:
    for name, desc in ROLES.items():
        if kc.get(f"/realms/{realm}/roles/{name}", ok=(404,)) is None:
            kc.call("POST", f"/realms/{realm}/roles", {"name": name, "description": desc})
        else:
            kc.call("PUT", f"/realms/{realm}/roles/{name}", {"name": name, "description": desc})
    print(f"roles: {', '.join(ROLES)}")


def scope_id(kc: KC, realm: str, name: str) -> str | None:
    return next((s["id"] for s in kc.get(f"/realms/{realm}/client-scopes") if s["name"] == name), None)


def upsert_mapper(kc: KC, base: str, mapper: dict[str, Any]) -> None:
    cur = {m["name"]: m for m in kc.get(f"{base}/protocol-mappers/models")}
    if mapper["name"] in cur:
        kc.call("PUT", f"{base}/protocol-mappers/models/{cur[mapper['name']]['id']}", {**mapper, "id": cur[mapper["name"]]["id"]})
    else:
        kc.call("POST", f"{base}/protocol-mappers/models", mapper)


def drop_account_audience(kc: KC, realm: str) -> None:
    """Gỡ mapper `audience resolve` khỏi client scope `roles`: nguồn của aud `account` (và `realm-management`)
    trong access token. aud còn lại chỉ do mapper tường minh của `mcp-xt` (resource MCP + xt-api-introspect)."""
    sid = scope_id(kc, realm, "roles")
    if not sid:
        return
    base = f"/realms/{realm}/client-scopes/{sid}/protocol-mappers/models"
    for m in kc.get(base):
        if m.get("protocolMapper") == "oidc-audience-resolve-mapper":
            kc.call("DELETE", f"{base}/{m['id']}", ok=(404,))
            print(f"scope roles: đã gỡ mapper {m['name']} (không còn aud account)")


def ensure_mcp_scope(kc: KC, realm: str, resource: str, introspect_client: str) -> str:
    rep = {
        "name": "mcp-xt",
        "protocol": "openid-connect",
        "description": "Quyền gọi MCP xT (aud = resource MCP)",
        "attributes": {"include.in.token.scope": "true", "display.on.consent.screen": "false"},
    }
    sid = scope_id(kc, realm, "mcp-xt")
    if sid is None:
        kc.call("POST", f"/realms/{realm}/client-scopes", rep)
        sid = scope_id(kc, realm, "mcp-xt")
    else:
        kc.call("PUT", f"/realms/{realm}/client-scopes/{sid}", {**rep, "id": sid})
    base = f"/realms/{realm}/client-scopes/{sid}"
    aud = {"id.token.claim": "false", "access.token.claim": "true", "introspection.token.claim": "true"}
    upsert_mapper(kc, base, {"name": "mcp-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                             "config": {**aud, "included.custom.audience": resource}})
    # Keycloak 26: introspection trả active:false nếu client hỏi không nằm trong aud (identity.md)
    upsert_mapper(kc, base, {"name": "introspect-audience", "protocol": "openid-connect", "protocolMapper": "oidc-audience-mapper",
                             "config": {**aud, "included.client.audience": introspect_client}})
    upsert_mapper(kc, base, {"name": "organization", "protocol": "openid-connect", "protocolMapper": "oidc-organization-membership-mapper",
                             "config": {"id.token.claim": "false", "access.token.claim": "true", "introspection.token.claim": "true",
                                        "claim.name": "organization", "jsonType.label": "String", "multivalued": "true"}})
    # KHÔNG phải realm default; là optional: connector chủ động xin `scope=mcp-xt`
    kc.call("DELETE", f"/realms/{realm}/default-default-client-scopes/{sid}", ok=(404,))
    kc.call("PUT", f"/realms/{realm}/default-optional-client-scopes/{sid}", ok=(409,))
    # MCP spec cấm quảng bá offline_access: gỡ khỏi optional mặc định của realm
    off = scope_id(kc, realm, "offline_access")
    if off:
        kc.call("DELETE", f"/realms/{realm}/default-optional-client-scopes/{off}", ok=(404,))
    print(f"scope mcp-xt: aud={resource} + {introspect_client}; realm default-optional")
    return sid


def upsert_client(kc: KC, realm: str, rep: dict[str, Any]) -> str:
    found = kc.get(f"/realms/{realm}/clients?clientId={urllib.parse.quote(rep['clientId'])}")
    if found:
        cid = found[0]["id"]
        kc.call("PUT", f"/realms/{realm}/clients/{cid}", {**rep, "id": cid})
    else:
        kc.call("POST", f"/realms/{realm}/clients", rep)
        cid = kc.get(f"/realms/{realm}/clients?clientId={urllib.parse.quote(rep['clientId'])}")[0]["id"]
    print(f"client {rep['clientId']}: OK")
    return cid


def drop_mcp_scope(kc: KC, realm: str, cid: str, mcp_sid: str) -> None:
    """xt-cli chỉ dành cho /data /workflow /files headless: không được giữ mcp-xt ở bất kỳ loại nào."""
    for kind in ("optional", "default"):
        kc.call("DELETE", f"/realms/{realm}/clients/{cid}/{kind}-client-scopes/{mcp_sid}", ok=(404,))


def set_client_scope(kc: KC, realm: str, cid: str, sid: str, kind: str) -> None:
    other = "optional" if kind == "default" else "default"
    kc.call("DELETE", f"/realms/{realm}/clients/{cid}/{other}-client-scopes/{sid}", ok=(404,))
    kc.call("PUT", f"/realms/{realm}/clients/{cid}/{kind}-client-scopes/{sid}", ok=(409,))


def build_stepup_flow(kc: KC, realm: str) -> str:
    """Flow LoA: LoA1 = mật khẩu, LoA2 = OTP với loa-max-age=0 (luôn hỏi lại). Theo spike-hitl/kc_setup.py."""
    a = f"/realms/{realm}/authentication"

    def execs() -> list[dict[str, Any]]:
        return kc.get(f"{a}/flows/{STEPUP_FLOW}/executions")

    def require(match: Any, req: str) -> None:
        for e in execs():
            if match(e):
                e["requirement"] = req
                kc.call("PUT", f"{a}/flows/{STEPUP_FLOW}/executions", e)
                return
        sys.exit(f"flow {STEPUP_FLOW}: thiếu execution {req}")

    flows = kc.get(f"{a}/flows")
    if not any(f["alias"] == STEPUP_FLOW for f in flows):
        kc.call("POST", f"{a}/flows", {"alias": STEPUP_FLOW, "providerId": "basic-flow", "topLevel": True,
                                       "builtIn": False, "description": "xT confirm: mật khẩu + OTP tươi"})
        kc.call("POST", f"{a}/flows/{STEPUP_FLOW}/executions/execution", {"provider": "auth-cookie"})
        for alias, parent in (("xt-auth", STEPUP_FLOW), ("xt-loa1", "xt-auth"), ("xt-loa2", "xt-auth")):
            kc.call("POST", f"{a}/flows/{parent}/executions/flow", {"alias": alias, "type": "basic-flow", "provider": "registration-page-form"})
        for parent, prov in (("xt-loa1", "conditional-level-of-authentication"), ("xt-loa1", "auth-username-password-form"),
                             ("xt-loa2", "conditional-level-of-authentication"), ("xt-loa2", "auth-otp-form")):
            kc.call("POST", f"{a}/flows/{parent}/executions/execution", {"provider": prov})
        require(lambda e: e.get("providerId") == "auth-cookie", "ALTERNATIVE")
        require(lambda e: e.get("displayName") == "xt-auth", "ALTERNATIVE")
        require(lambda e: e.get("displayName") == "xt-loa1", "CONDITIONAL")
        require(lambda e: e.get("displayName") == "xt-loa2", "CONDITIONAL")
        for e in execs():
            if e.get("providerId") in ("conditional-level-of-authentication", "auth-username-password-form", "auth-otp-form"):
                e["requirement"] = "REQUIRED"
                kc.call("PUT", f"{a}/flows/{STEPUP_FLOW}/executions", e)
        loas = [e for e in execs() if e.get("providerId") == "conditional-level-of-authentication"]
        for lvl, e in zip((1, 2), loas, strict=True):
            kc.call("POST", f"{a}/executions/{e['id']}/config", {"alias": f"xt-loa{lvl}-cond", "config": {
                "loa-condition-level": str(lvl), "loa-max-age": "36000" if lvl == 1 else "0"}})
        print(f"flow {STEPUP_FLOW}: đã tạo")
    # Authentication Reference cho AMR mapper: gắn cho flow step-up + browser mặc định, ép maxAge (idempotent)
    for flow in (STEPUP_FLOW, "browser"):
        for e in kc.get(f"{a}/flows/{flow}/executions"):
            ref = {"auth-username-password-form": "pwd", "auth-otp-form": "otp"}.get(e.get("providerId", ""))
            if not ref:
                continue
            want = {"default.reference.value": ref, "default.reference.maxAge": AMR_MAX_AGE}
            if e.get("authenticationConfig"):
                cfg = kc.get(f"{a}/config/{e['authenticationConfig']}")
                if cfg["config"].get("default.reference.value") in (None, ref):
                    cfg["config"].update(want)
                    kc.call("PUT", f"{a}/config/{cfg['id']}", cfg)
            else:
                kc.call("POST", f"{a}/executions/{e['id']}/config", {"alias": f"{flow}-amr-{ref}", "config": want})
    return next(f["id"] for f in kc.get(f"{a}/flows") if f["alias"] == STEPUP_FLOW)


def ensure_dcr_policies(kc: KC, realm: str, trusted: list[str]) -> None:
    ptype = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
    comps = kc.get(f"/realms/{realm}/components?type={ptype}")

    def anon(provider: str) -> dict[str, Any]:
        return next(c for c in comps if c["providerId"] == provider and c["subType"] == "anonymous")

    th = anon("trusted-hosts")
    # ChatGPT/Claude đăng ký từ IP cloud không biết trước: không ép host gửi request, nhưng
    # redirect_uris/client_uris BẮT BUỘC thuộc host tin cậy (chatgpt.com, claude.ai, loopback).
    th["config"] = {"host-sending-registration-request-must-match": ["false"], "client-uris-must-match": ["true"],
                    "trusted-hosts": trusted}
    kc.call("PUT", f"/realms/{realm}/components/{th['id']}", th)
    mc = anon("max-clients")
    mc["config"] = {"max-clients": ["50"]}
    kc.call("PUT", f"/realms/{realm}/components/{mc['id']}", mc)
    tpl = anon("allowed-client-templates")
    scopes = kc.get(f"/realms/{realm}/client-scopes")
    allowed = [s["name"] for s in scopes if s["name"] in ("openid", "profile", "email", "roles", "web-origins", "acr", "basic", "organization", "mcp-xt")]
    tpl["config"] = {"allow-default-scopes": ["true"], "allowed-client-scopes": allowed}   # không có offline_access
    kc.call("PUT", f"/realms/{realm}/components/{tpl['id']}", tpl)
    print(f"DCR anonymous: trusted-hosts={trusted}; allowed-client-scopes={allowed}; consent-required giữ nguyên")


def ensure_dcr_client_policy(kc: KC, realm: str, permitted_domains: list[str]) -> None:
    """Client policy chỉ áp cho đăng ký ẩn danh (DCR): client public auth-code + PKCE S256, redirect cố định.

    Chặn ROPC, implicit, response_type khác `code`, http (trừ loopback IPv4/IPv6), wildcard, scope đầy đủ,
    và buộc consent. Gateway (apisix.yaml.tmpl, route auth-dcr) chặn thêm grant_types/auth method ở schema.
    Lưu ý: PUT thay toàn bộ profile/policy tùy biến của realm.
    """
    on = {"auto-configure": True}
    profile = {"name": "xt-dcr-anonymous", "description": "DCR ẩn danh: client public auth-code + PKCE", "executors": [
        {"executor": "pkce-enforcer", "configuration": on},
        {"executor": "reject-ropc-grant", "configuration": on},
        {"executor": "reject-implicit-grant", "configuration": on},
        {"executor": "secure-response-type", "configuration": {**on, "allow-token-response-type": False}},
        {"executor": "full-scope-disabled", "configuration": on},
        {"executor": "consent-required", "configuration": on},
        {"executor": "secure-redirect-uris-enforcer", "configuration": {
            "allow-ipv4-loopback-address": True, "allow-ipv6-loopback-address": True, "allow-private-use-uri-scheme": False,
            "allow-http-scheme": True,   # bắt buộc bật để nhận loopback http; http không-loopback bị gateway chặn "allow-wildcard-context-path": False, "allow-open-redirect": False,
            "oauth-2-1-compliant": False, "allow-permitted-domains": permitted_domains}},
    ]}
    policy = {"name": "xt-dcr-anonymous", "description": "Áp cho client tạo bởi DCR ẩn danh", "enabled": True,
              "conditions": [{"condition": "client-updater-context", "configuration": {"update-client-source": ["ByAnonymous", "ByRegistrationAccessToken"]}}],
              "profiles": ["xt-dcr-anonymous"]}
    kc.call("PUT", f"/realms/{realm}/client-policies/profiles", {"profiles": [profile]})
    kc.call("PUT", f"/realms/{realm}/client-policies/policies", {"policies": [policy]})
    print(f"client policy xt-dcr-anonymous: PKCE S256, no ROPC/implicit, redirect cố định {permitted_domains}")


def ensure_totp(kc: KC, realm: str, roles: list[str]) -> None:
    a = f"/realms/{realm}/authentication/required-actions/CONFIGURE_TOTP"
    ra = kc.get(a)
    ra.update({"enabled": True, "defaultAction": False})
    kc.call("PUT", a, ra)
    n = 0
    for role in roles:
        for u in kc.get(f"/realms/{realm}/roles/{role}/users?max=1000", ok=(404,)) or []:
            creds = kc.get(f"/realms/{realm}/users/{u['id']}/credentials")
            actions = u.get("requiredActions", [])
            if not any(c["type"] == "otp" for c in creds) and "CONFIGURE_TOTP" not in actions:
                kc.call("PUT", f"/realms/{realm}/users/{u['id']}", {**u, "requiredActions": [*actions, "CONFIGURE_TOTP"]})
                n += 1
    print(f"TOTP: required action bật; {n} user giữ role {roles} bị buộc cấu hình TOTP (flow {STEPUP_FLOW} luôn đòi OTP)")


def provision(kc: KC, env: dict[str, str]) -> None:
    realm = env.get("XT_REALM", "xt")
    kc.wait_ready()
    resource = f"https://{env['XT_DOMAIN_MCP']}/mcp"
    intro = env.get("XT_IDP_INTROSPECTION_CLIENT_ID", "xt-api-introspect")
    upsert_realm(kc, realm, env)
    ensure_roles(kc, realm)
    mcp_sid = ensure_mcp_scope(kc, realm, resource, intro)
    drop_account_audience(kc, realm)
    flow_id = build_stepup_flow(kc, realm)

    desk = f"https://{env['XT_DOMAIN_DESK']}"
    amr = {"name": "amr", "protocol": "openid-connect", "protocolMapper": "oidc-amr-mapper",
           "config": {"id.token.claim": "true", "access.token.claim": "true", "introspection.token.claim": "true"}}
    pkce = {"pkce.code.challenge.method": "S256"}
    human = upsert_client(kc, realm, {
        "clientId": env.get("XT_MCP_HUMAN_CLIENTS", "xt-desk").split(",")[0].strip(), "name": "xT Desk (phiên người dùng)",
        "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "redirectUris": [f"{desk}/*"], "webOrigins": [desk], "attributes": pkce, "protocolMappers": [amr]})
    set_client_scope(kc, realm, human, mcp_sid, "default")   # token Desk mang aud MCP + scope mcp-xt
    # Chat của Desk là AGENT hành động thay người dùng: client riêng, public + PKCE S256, mcp-xt là scope OPTIONAL
    # (Desk chủ động xin scope=mcp-xt). Client public tối đa tier masked (docs/decisions.md, Tier PII); người xem giá trị thật qua /pii/reveal.
    # Redirect: production KHÔNG có loopback; dev (XT_PROFILE=dev hoặc --dev) thêm localhost/127.0.0.1.
    # DPoP: token gắn khóa non-extractable của trình duyệt (docs/decisions.md "DPoP cho Desk trước v1.0").
    agent = upsert_client(kc, realm, desk_agent_client_rep(env, desk, [amr]))
    set_client_scope(kc, realm, agent, mcp_sid, "optional")
    confirm = upsert_client(kc, realm, {
        "clientId": env.get("XT_CONFIRM_CLIENT_ID", "xt-desk-confirm"), "name": "xT Desk confirm (step-up OTP)",
        "publicClient": True, "standardFlowEnabled": True, "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False,
        "redirectUris": [f"{desk}/desk/confirm/callback", f"{desk}/desk/telegram/callback"], "webOrigins": [desk],
        "attributes": {**pkce, "minimum.acr.value": env.get("XT_CONFIRM_ACR", "urn:xt:acr:otp")},
        "authenticationFlowBindingOverrides": {"browser": flow_id}, "protocolMappers": [amr]})
    kc.call("DELETE", f"/realms/{realm}/clients/{confirm}/default-client-scopes/{mcp_sid}", ok=(404,))
    upsert_client(kc, realm, {
        "clientId": intro, "name": "xt-api introspection", "publicClient": False, "secret": env["XT_IDP_INTROSPECTION_CLIENT_SECRET"],
        "clientAuthenticatorType": "client-secret", "standardFlowEnabled": False, "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False, "serviceAccountsEnabled": False})
    reg = env.get("XT_IDP_ADMIN_CLIENT_ID", "xt-client-registry")
    reg_cid = upsert_client(kc, realm, {
        "clientId": reg, "name": "xt-api connector registry (view-clients)", "publicClient": False,
        "secret": env["XT_IDP_ADMIN_CLIENT_SECRET"], "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": False, "implicitFlowEnabled": False, "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": True, "fullScopeAllowed": True})   # chỉ có role view-clients nên full scope vẫn tối thiểu; False làm token mất role
    sa = kc.get(f"/realms/{realm}/clients/{reg_cid}/service-account-user")
    mgmt = kc.get(f"/realms/{realm}/clients?clientId=realm-management")[0]["id"]
    vc = kc.get(f"/realms/{realm}/clients/{mgmt}/roles/view-clients")
    kc.call("POST", f"/realms/{realm}/users/{sa['id']}/role-mappings/clients/{mgmt}", [vc])   # chỉ view-clients
    cli = upsert_client(kc, realm, cli_client_rep(env))
    drop_mcp_scope(kc, realm, cli, mcp_sid)   # client mới kế thừa default-optional của realm: gỡ mcp-xt
    # không gán mcp-xt; xt-data/xt-workflow cấp bởi provision_data.py
    ensure_dcr_policies(kc, realm, env_list(env, "XT_DCR_TRUSTED_HOSTS", "chatgpt.com,claude.ai"))
    hosts = env_list(env, "XT_DCR_TRUSTED_HOSTS", "chatgpt.com,claude.ai")
    ensure_dcr_client_policy(kc, realm, ["^" + h.replace(".", "\\.") + "$" for h in hosts if h not in ("localhost",)])
    ensure_totp(kc, realm, env_list(env, "XT_TOTP_ROLES"))
    print("redirect URI connector (host: trusted-hosts + client policy; scheme/số lượng/wildcard: client policy + gateway):", ", ".join(CONNECTOR_REDIRECTS))


def strip_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items()
                if k not in ("secret", "registrationAccessToken", "clientSecret") and not (isinstance(v, str) and v == "**********")}
    if isinstance(obj, list):
        return [strip_secrets(x) for x in obj]
    return obj


def export(kc: KC, realm: str) -> None:
    rep = kc.call("POST", f"/realms/{realm}/partial-export?exportClients=true&exportGroupsAndRoles=true")
    out = DEPLOY / "keycloak" / "realm-xt.json"
    out.write_text(json.dumps(strip_secrets(rep), indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"đã xuất {out} (secret đã loại)")


def main() -> int:
    env = {**ensure_env(DEPLOY), **{k: v for k, v in os.environ.items() if k.startswith(("XT_", "KC_"))}}
    base = env.get("KC_ADMIN_URL") or f"http://127.0.0.1:{env.get('KC_HTTP_PORT', '18080')}"
    kc = KC(base, env.get("KC_BOOTSTRAP_ADMIN_USERNAME", "admin"), env["KC_BOOTSTRAP_ADMIN_PASSWORD"])
    provision(kc, env)
    if "--export" in sys.argv:
        export(kc, env.get("XT_REALM", "xt"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
