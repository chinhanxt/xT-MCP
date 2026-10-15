# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Admin REST Keycloak qua cổng loopback 127.0.0.1:${KC_HTTP_PORT} (giống provision.py, không qua APISIX)."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from xte2e.config import settings


class Admin:
    def __init__(self) -> None:
        s = settings()
        self.realm = s.realm
        self.c = httpx.Client(base_url=s.kc_admin, timeout=60)
        self.user = s.env.get("KC_BOOTSTRAP_ADMIN_USERNAME") or "admin"
        self.password = s.env["KC_BOOTSTRAP_ADMIN_PASSWORD"]
        self.token = ""

    def login(self) -> None:
        r = self.c.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.user,
                "password": self.password,
            },
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def wait_ready(self, timeout: float = 240) -> None:
        end = time.time() + timeout
        last = ""
        while time.time() < end:
            try:
                self.login()
                return
            except (httpx.HTTPError, KeyError) as e:
                last = str(e)
                time.sleep(3)
        raise RuntimeError(f"Keycloak admin không sẵn sàng: {last}")

    def call(
        self, method: str, path: str, *, ok: tuple[int, ...] = (), **kw: Any
    ) -> httpx.Response:
        extra = kw.pop("headers", {})
        for attempt in (0, 1):
            headers = {"Authorization": f"Bearer {self.token}", **extra}
            r = self.c.request(method, f"/admin/realms/{self.realm}{path}", headers=headers, **kw)
            if r.status_code == 401 and attempt == 0:
                self.login()
                continue
            if r.status_code >= 400 and r.status_code not in ok:
                raise RuntimeError(f"KC {method} {path} -> {r.status_code} {r.text[:400]}")
            return r
        return r

    def get(self, path: str, **kw: Any) -> Any:
        r = self.call("GET", path, **kw)
        return r.json() if r.content else None

    # ------------------------------------------------------------------ user ----
    def find_user(self, username: str) -> dict[str, Any] | None:
        found = self.get(f"/users?username={quote(username)}&exact=true") or []
        return found[0] if found else None

    def upsert_user(
        self, rep: dict[str, Any], password: str, totp_raw: str | None, *, recreate: bool = False
    ) -> str:
        """Tạo user (mật khẩu + OTP seed đã biết) hoặc đặt lại cho khớp tệp bí mật cục bộ.

        `recreate`: bí mật cục bộ vừa được sinh mới (không còn bản cũ) -> seed OTP trong Keycloak không
        đối chiếu được, nên xóa và tạo lại user với seed mới.
        """
        u = self.find_user(rep["username"])
        if u is not None and recreate:
            self.call("DELETE", f"/users/{u['id']}")
            u = None
        if u is None:
            creds: list[dict[str, Any]] = [
                {"type": "password", "value": password, "temporary": False}
            ]
            if totp_raw:
                creds.append(otp_credential(totp_raw))
            self.call(
                "POST",
                "/users",
                json={
                    **rep,
                    "enabled": True,
                    "emailVerified": True,
                    "requiredActions": [],
                    "credentials": creds,
                },
            )
            u = self.find_user(rep["username"])
            assert u is not None
            return str(u["id"])
        uid = str(u["id"])
        self.call(
            "PUT",
            f"/users/{uid}",
            json={**u, **rep, "enabled": True, "emailVerified": True, "requiredActions": []},
        )
        self.call(
            "PUT",
            f"/users/{uid}/reset-password",
            json={"type": "password", "value": password, "temporary": False},
            ok=(400,),
        )
        if totp_raw:
            creds = self.get(f"/users/{uid}/credentials") or []
            if not any(c["type"] == "otp" for c in creds):
                # Admin REST không thêm OTP vào user đã có: tạo lại user (id mới) với seed đã biết.
                self.call("DELETE", f"/users/{uid}")
                return self.upsert_user(rep, password, totp_raw)
        return uid

    def set_realm_roles(self, uid: str, roles: list[str]) -> None:
        reps = [self.get(f"/roles/{quote(r)}") for r in roles]
        self.call("POST", f"/users/{uid}/role-mappings/realm", json=reps)

    def delete_user(self, username: str) -> None:
        u = self.find_user(username)
        if u:
            self.call("DELETE", f"/users/{u['id']}")

    # ---------------------------------------------------------- organization ----
    def org(self, alias: str) -> dict[str, Any] | None:
        for o in self.get(f"/organizations?search={quote(alias)}&briefRepresentation=false") or []:
            if o.get("alias") == alias:
                return o  # type: ignore[no-any-return]
        return None

    def upsert_org(self, alias: str, name: str, domain: str) -> str:
        o = self.org(alias)
        rep = {
            "name": name,
            "alias": alias,
            "enabled": True,
            "domains": [{"name": domain, "verified": False}],
        }
        if o is None:
            self.call("POST", "/organizations", json=rep)
            o = self.org(alias)
            assert o is not None
        else:
            self.call("PUT", f"/organizations/{o['id']}", json={**o, **rep, "id": o["id"]})
        return str(o["id"])

    def add_member(self, org_id: str, uid: str) -> None:
        # 409 = đã là thành viên (idempotent)
        self.call(
            "POST",
            f"/organizations/{org_id}/members",
            ok=(409,),
            content=f'"{uid}"'.encode(),
            headers={"Content-Type": "application/json"},
        )

    # --------------------------------------------------------------- client ----
    def client(self, client_id: str) -> dict[str, Any] | None:
        found = self.get(f"/clients?clientId={quote(client_id)}") or []
        return next((c for c in found if c.get("clientId") == client_id), None)

    def upsert_client(self, rep: dict[str, Any]) -> str:
        c = self.client(rep["clientId"])
        if c is None:
            self.call("POST", "/clients", json=rep)
            c = self.client(rep["clientId"])
            assert c is not None
        else:
            self.call("PUT", f"/clients/{c['id']}", json={**c, **rep, "id": c["id"]})
        return str(c["id"])

    def scope_id(self, name: str) -> str | None:
        return next((s["id"] for s in self.get("/client-scopes") if s["name"] == name), None)


def otp_credential(raw: str) -> dict[str, Any]:
    import json

    return {
        "type": "otp",
        "userLabel": "e2e",
        "secretData": json.dumps({"value": raw}),
        "credentialData": json.dumps(
            {"subType": "totp", "digits": 6, "counter": 0, "period": 30, "algorithm": "HmacSHA1"}
        ),
    }
