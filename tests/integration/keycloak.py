# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dựng Keycloak 26.6.3 thật cho test tích hợp (Admin REST, không click tay).

Phỏng theo `research/spike-mcp-auth/kc_setup.py` + `research/spike-hitl/kc_setup.py`, đã sửa:
- scope audience `mcp-xt` là scope RIÊNG, KHÔNG nằm trong realm default; chỉ gán cho client MCP;
- có client `it-rogue` mang đúng audience nhưng không nằm trong allowlist `azp` (VS #1);
- mật khẩu admin sinh ngẫu nhiên mỗi lần chạy.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import socket
import struct
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

IMAGE = "quay.io/keycloak/keycloak:26.6.3"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# Tên + cổng riêng mỗi tiến trình test: nhiều lượt chạy song song không xóa nhầm Keycloak của nhau.
CONTAINER = f"xt-it-identity-kc-{secrets.token_hex(4)}"
PORT = _free_port()
BASE = f"http://localhost:{PORT}"
RESOURCE = "http://localhost:19401/mcp"
CONFIRM_CB = "http://localhost:19402/confirm/cb"
ACR_OTP = "urn:xt:acr:otp"
TOTP_RAW = secrets.token_hex(10)  # Keycloak lưu chuỗi thô; khóa HMAC = bytes của chuỗi


def totp(raw: str, at: float | None = None, step: int = 30) -> str:
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(raw.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def start_container(admin_password: str) -> None:
    subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True, check=False)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "-p",
            f"127.0.0.1:{PORT}:8080",
            "-e",
            "KC_BOOTSTRAP_ADMIN_USERNAME=admin",
            "-e",
            f"KC_BOOTSTRAP_ADMIN_PASSWORD={admin_password}",
            IMAGE,
            "start-dev",
        ],
        check=True,
        capture_output=True,
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/realms/master", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    logs = subprocess.run(["docker", "logs", "--tail", "50", CONTAINER], capture_output=True)
    stop_container()
    raise RuntimeError("Keycloak không lên trong 180 s:\n" + logs.stdout.decode(errors="replace"))


def stop_container() -> None:
    subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True, check=False)


@dataclass
class Realm:
    name: str
    admin: Admin
    secrets: dict[str, str] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=dict)  # username -> id

    @property
    def issuer(self) -> str:
        return f"{BASE}/realms/{self.name}"

    @property
    def oidc(self) -> str:
        return f"{self.issuer}/protocol/openid-connect"

    def token(self, client: str, **data: str) -> dict[str, Any]:
        body = {"client_id": client, **data}
        if client in self.secrets:
            body["client_secret"] = self.secrets[client]
        r = httpx.post(f"{self.oidc}/token", data=body, timeout=30)
        assert r.status_code == 200, r.text
        out: dict[str, Any] = r.json()
        return out

    def password_token(self, client: str, user: str, scope: str = "openid") -> dict[str, Any]:
        return self.token(
            client, grant_type="password", username=user, password=f"{user}-pw", scope=scope
        )

    def env(self, **over: str) -> dict[str, str]:
        e = {
            "XT_IDP_ISSUER": self.issuer,
            "XT_IDP_JWKS_URL": f"{self.oidc}/certs",
            "XT_IDP_ALLOW_HTTP": "true",
            "XT_MCP_RESOURCE": RESOURCE,
            "XT_MCP_ALLOWED_CLIENTS": "it-agent,it-short",
            "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
            "XT_MCP_SCOPES_SUPPORTED": "openid email mcp-xt",
            "XT_CONFIRM_CLIENT_ID": "it-confirm",
            "XT_CONFIRM_ACR": ACR_OTP,
            "XT_CONFIRM_REQUIRED_AMR": "otp",
        }
        e.update(over)
        return e


class _AdminAuth(httpx.Auth):
    """Token admin-cli của master sống 60 s; fixture session dùng lâu hơn -> lấy lại khi 401."""

    def __init__(self, password: str) -> None:
        self.password = password
        self.token = self._fetch()

    def _fetch(self) -> str:
        r = httpx.post(
            f"{BASE}/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": "admin",
                "password": self.password,
            },
            timeout=30,
        )
        r.raise_for_status()
        return str(r.json()["access_token"])

    def auth_flow(self, request: httpx.Request) -> Any:
        request.headers["Authorization"] = f"Bearer {self.token}"
        response = yield request
        if response.status_code == 401:
            self.token = self._fetch()
            request.headers["Authorization"] = f"Bearer {self.token}"
            yield request


class Admin:
    def __init__(self, password: str) -> None:
        auth = _AdminAuth(password)
        self.c = httpx.Client(base_url=BASE, timeout=30, auth=auth)
        # Một số test đọc token master trực tiếp; auth_flow vẫn ghi đè bằng token mới nhất.
        self.c.headers["Authorization"] = f"Bearer {auth.token}"

    def ok(self, r: httpx.Response) -> httpx.Response:
        if r.status_code not in (200, 201, 204):
            raise RuntimeError(f"{r.request.method} {r.request.url} -> {r.status_code} {r.text}")
        return r

    def client_uuid(self, realm: str, client_id: str) -> str:
        found = self.ok(
            self.c.get(f"/admin/realms/{realm}/clients", params={"clientId": client_id})
        ).json()
        return str(found[0]["id"])

    # ------------------------------------------------------------------ realm
    def create_realm(self, name: str, *, stepup: bool) -> Realm:
        realm = Realm(name, self)
        a = f"/admin/realms/{name}"
        self.ok(
            self.c.post(
                "/admin/realms",
                json={
                    "realm": name,
                    "enabled": True,
                    "sslRequired": "none",
                    "accessTokenLifespan": 300,
                    "otpPolicyType": "totp",
                    "otpPolicyAlgorithm": "HmacSHA1",
                    "otpPolicyDigits": 6,
                    "otpPolicyPeriod": 30,
                    "attributes": {"acr.loa.map": json.dumps({"urn:xt:acr:pwd": 1, ACR_OTP: 2})},
                },
            )
        )
        # Scope audience riêng; KHÔNG gán vào default-default-client-scopes của realm.
        self.ok(
            self.c.post(
                f"{a}/client-scopes",
                json={
                    "name": "mcp-xt",
                    "protocol": "openid-connect",
                    "attributes": {
                        "include.in.token.scope": "true",
                        "display.on.consent.screen": "false",
                    },
                    "protocolMappers": [
                        {
                            "name": "mcp-audience",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-audience-mapper",
                            "config": {
                                "included.custom.audience": RESOURCE,
                                "id.token.claim": "false",
                                "access.token.claim": "true",
                                "introspection.token.claim": "true",
                            },
                        },
                        # Keycloak 26 chỉ cho introspect nếu client hỏi nằm trong `aud` của token.
                        {
                            "name": "introspection-audience",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-audience-mapper",
                            "config": {
                                "included.client.audience": "it-rs",
                                "id.token.claim": "false",
                                "access.token.claim": "true",
                                "introspection.token.claim": "true",
                            },
                        },
                    ],
                },
            )
        )
        scopes = self.ok(self.c.get(f"{a}/client-scopes")).json()
        mcp_scope = next(s["id"] for s in scopes if s["name"] == "mcp-xt")
        defaults = self.ok(self.c.get(f"{a}/default-default-client-scopes")).json()
        assert all(s["name"] != "mcp-xt" for s in defaults)

        for user in ("alice", "bob"):
            creds: list[dict[str, Any]] = [
                {"type": "password", "value": f"{user}-pw", "temporary": False}
            ]
            if user == "alice":
                creds.append(
                    {
                        "type": "otp",
                        "userLabel": "it",
                        "secretData": json.dumps({"value": TOTP_RAW}),
                        "credentialData": json.dumps(
                            {
                                "subType": "totp",
                                "digits": 6,
                                "counter": 0,
                                "period": 30,
                                "algorithm": "HmacSHA1",
                            }
                        ),
                    }
                )
            self.ok(
                self.c.post(
                    f"{a}/users",
                    json={
                        "username": user,
                        "email": f"{user}@example.test",
                        "emailVerified": True,
                        "firstName": user.title(),
                        "lastName": "IT",
                        "enabled": True,
                        "credentials": creds,
                    },
                )
            )
            found = self.ok(
                self.c.get(f"{a}/users", params={"username": user, "exact": "true"})
            ).json()
            realm.users[user] = found[0]["id"]

        def confidential(
            client_id: str, *, mcp: bool, sa: bool = False, attrs: dict[str, str] | None = None
        ) -> None:
            secret = secrets.token_urlsafe(24)
            self.ok(
                self.c.post(
                    f"{a}/clients",
                    json={
                        "clientId": client_id,
                        "secret": secret,
                        "publicClient": False,
                        "standardFlowEnabled": False,
                        "directAccessGrantsEnabled": True,
                        "serviceAccountsEnabled": sa,
                        "attributes": attrs or {},
                    },
                )
            )
            realm.secrets[client_id] = secret
            if mcp:
                uid = self.client_uuid(name, client_id)
                self.ok(self.c.put(f"{a}/clients/{uid}/default-client-scopes/{mcp_scope}"))

        confidential("it-rs", mcp=False)  # resource server: chỉ dùng cho introspection
        confidential("it-agent", mcp=True, sa=True)  # MCP client trong allowlist
        confidential("it-rogue", mcp=True)  # có aud MCP nhưng không trong allowlist
        confidential("it-plain", mcp=False)  # client nội bộ khác: không có aud MCP
        confidential("it-short", mcp=True, attrs={"access.token.lifespan": "2"})
        if stepup:
            self._stepup(realm)
        return realm

    def _stepup(self, realm: Realm) -> None:
        a = f"/admin/realms/{realm.name}"
        flow = "xt-stepup"
        self.ok(
            self.c.post(
                f"{a}/authentication/flows",
                json={
                    "alias": flow,
                    "providerId": "basic-flow",
                    "topLevel": True,
                    "builtIn": False,
                    "description": "xT confirm: pwd + OTP tươi",
                },
            )
        )

        def sub(parent: str, alias: str) -> None:
            self.ok(
                self.c.post(
                    f"{a}/authentication/flows/{parent}/executions/flow",
                    json={
                        "alias": alias,
                        "type": "basic-flow",
                        "provider": "registration-page-form",
                    },
                )
            )

        def exe(parent: str, provider: str) -> None:
            self.ok(
                self.c.post(
                    f"{a}/authentication/flows/{parent}/executions/execution",
                    json={"provider": provider},
                )
            )

        def execs() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = self.ok(
                self.c.get(f"{a}/authentication/flows/{flow}/executions")
            ).json()
            return out

        def put(e: dict[str, Any], req: str) -> None:
            e["requirement"] = req
            self.ok(self.c.put(f"{a}/authentication/flows/{flow}/executions", json=e))

        exe(flow, "auth-cookie")
        sub(flow, "xt-auth")
        sub("xt-auth", "xt-loa1")
        sub("xt-auth", "xt-loa2")
        exe("xt-loa1", "conditional-level-of-authentication")
        exe("xt-loa1", "auth-username-password-form")
        exe("xt-loa2", "conditional-level-of-authentication")
        exe("xt-loa2", "auth-otp-form")
        reqs = {"xt-auth": "ALTERNATIVE", "xt-loa1": "CONDITIONAL", "xt-loa2": "CONDITIONAL"}
        for e in execs():
            if e.get("providerId") == "auth-cookie":
                put(e, "ALTERNATIVE")
            elif e.get("displayName") in reqs:
                put(e, reqs[e["displayName"]])
            elif e.get("providerId") in (
                "conditional-level-of-authentication",
                "auth-username-password-form",
                "auth-otp-form",
            ):
                put(e, "REQUIRED")
        loas = [e for e in execs() if e.get("providerId") == "conditional-level-of-authentication"]
        for lvl, e in zip((1, 2), loas, strict=True):
            self.ok(
                self.c.post(
                    f"{a}/authentication/executions/{e['id']}/config",
                    json={
                        "alias": f"xt-loa{lvl}-cond",
                        "config": {
                            "loa-condition-level": str(lvl),
                            "loa-max-age": "36000" if lvl == 1 else "0",
                        },
                    },
                )
            )
        for e in execs():
            ref = {"auth-username-password-form": "pwd", "auth-otp-form": "otp"}.get(
                e.get("providerId", "")
            )
            if ref:
                self.ok(
                    self.c.post(
                        f"{a}/authentication/executions/{e['id']}/config",
                        json={
                            "alias": f"xt-amr-{ref}",
                            "config": {
                                "default.reference.value": ref,
                                "default.reference.maxAge": "300",
                            },
                        },
                    )
                )
        flows = self.ok(self.c.get(f"{a}/authentication/flows")).json()
        flow_id = next(f["id"] for f in flows if f["alias"] == flow)
        self.ok(
            self.c.post(
                f"{a}/clients",
                json={
                    "clientId": "it-confirm",
                    "publicClient": True,
                    "standardFlowEnabled": True,
                    "directAccessGrantsEnabled": False,
                    "redirectUris": [CONFIRM_CB],
                    "attributes": {
                        "pkce.code.challenge.method": "S256",
                        "minimum.acr.value": ACR_OTP,
                    },
                    "authenticationFlowBindingOverrides": {"browser": flow_id},
                    "protocolMappers": [
                        {
                            "name": "amr",
                            "protocol": "openid-connect",
                            "protocolMapper": "oidc-amr-mapper",
                            "config": {
                                "id.token.claim": "true",
                                "access.token.claim": "true",
                                "introspection.token.claim": "true",
                            },
                        }
                    ],
                },
            )
        )

    # ------------------------------------------------------------------ keys
    def rotate_rsa_key(self, realm: str) -> None:
        """Thêm khóa RSA mới ưu tiên cao hơn (active), rồi xóa khóa RSA ký ban đầu."""
        realm_id = self.ok(self.c.get(f"/admin/realms/{realm}")).json()["id"]
        comps = self.ok(
            self.c.get(
                f"/admin/realms/{realm}/components",
                params={"type": "org.keycloak.keys.KeyProvider"},
            )
        ).json()
        old = [c for c in comps if c["providerId"] == "rsa-generated"]
        self.ok(
            self.c.post(
                f"/admin/realms/{realm}/components",
                json={
                    "name": "rsa-rotated",
                    "providerId": "rsa-generated",
                    "providerType": "org.keycloak.keys.KeyProvider",
                    "parentId": realm_id,
                    "config": {"priority": ["500"], "keySize": ["2048"], "algorithm": ["RS256"]},
                },
            )
        )
        for c in old:
            self.ok(self.c.delete(f"/admin/realms/{realm}/components/{c['id']}"))

    def set_user_enabled(self, realm: str, user_id: str, enabled: bool) -> None:
        self.ok(self.c.put(f"/admin/realms/{realm}/users/{user_id}", json={"enabled": enabled}))


def register_dcr(realm: str, body: dict[str, Any]) -> dict[str, Any]:
    """DCR ẩn danh (như ChatGPT/Claude gọi)."""
    r = httpx.post(f"{BASE}/realms/{realm}/clients-registrations/openid-connect", json=body)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


def harden_dcr(admin: Admin, realm: str, trusted_hosts: list[str]) -> None:
    """Gia cố DCR ẩn danh giống deploy: Trusted Hosts theo redirect + PKCE S256 tự gắn."""
    a = f"/admin/realms/{realm}"
    ptype = "org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy"
    comps = admin.ok(admin.c.get(f"{a}/components", params={"type": ptype})).json()
    th = next(
        c for c in comps if c["providerId"] == "trusted-hosts" and c["subType"] == "anonymous"
    )
    th["config"] = {
        "host-sending-registration-request-must-match": ["false"],
        "client-uris-must-match": ["true"],
        "trusted-hosts": trusted_hosts,
    }
    admin.ok(admin.c.put(f"{a}/components/{th['id']}", json=th))
    admin.ok(
        admin.c.put(
            f"{a}/client-policies/profiles",
            json={
                "profiles": [
                    {
                        "name": "xt-dcr",
                        "executors": [
                            {
                                "executor": "pkce-enforcer",
                                "configuration": {"auto-configure": "true"},
                            }
                        ],
                    }
                ]
            },
        )
    )
    admin.ok(
        admin.c.put(
            f"{a}/client-policies/policies",
            json={
                "policies": [
                    {
                        "name": "xt-dcr",
                        "enabled": True,
                        "conditions": [
                            {
                                "condition": "client-updater-context",
                                "configuration": {"update-client-source": ["ByAnonymous"]},
                            }
                        ],
                        "profiles": ["xt-dcr"],
                    }
                ]
            },
        )
    )


def create_registry_reader(admin: Admin, realm: str) -> tuple[str, str]:
    """Service account chỉ có `realm-management/view-clients` (least privilege)."""
    a = f"/admin/realms/{realm}"
    cid, secret = "xt-client-registry", secrets.token_urlsafe(24)
    admin.ok(
        admin.c.post(
            f"{a}/clients",
            json={
                "clientId": cid,
                "secret": secret,
                "publicClient": False,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": True,
            },
        )
    )
    uid = admin.client_uuid(realm, cid)
    sa = admin.ok(admin.c.get(f"{a}/clients/{uid}/service-account-user")).json()["id"]
    rm = admin.client_uuid(realm, "realm-management")
    role = admin.ok(admin.c.get(f"{a}/clients/{rm}/roles/view-clients")).json()
    admin.ok(admin.c.post(f"{a}/users/{sa}/role-mappings/clients/{rm}", json=[role]))
    return cid, secret


def set_client_enabled(admin: Admin, realm: str, client_id: str, enabled: bool) -> None:
    uid = admin.client_uuid(realm, client_id)
    rep = admin.ok(admin.c.get(f"/admin/realms/{realm}/clients/{uid}")).json()
    rep["enabled"] = enabled
    admin.ok(admin.c.put(f"/admin/realms/{realm}/clients/{uid}", json=rep))
