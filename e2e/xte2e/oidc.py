# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đăng nhập Keycloak thật, headless, bằng auth-code + PKCE (không ROPC).

Realm `xt` tắt direct grant cho mọi client người dùng, nên harness lái đúng luồng trình duyệt:
GET authorize -> form đăng nhập -> (OTP) -> (consent) -> 302 về redirect_uri có `code` -> đổi code
kèm `code_verifier`. Toàn bộ qua APISIX (host xt-auth), cookie jar thật.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import struct
import threading
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from xte2e import net
from xte2e.config import settings


class LoginError(AssertionError):
    pass


# ------------------------------------------------------------------------------ TOTP ----
_last_counter: dict[str, int] = {}
_totp_lock = threading.Lock()


def totp(raw: str, at: float | None = None, step: int = 30) -> str:
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(raw.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def fresh_totp(user: str, raw: str) -> str:
    """Keycloak từ chối dùng lại cùng mã trong một cửa sổ 30 s: chờ cửa sổ mới nếu cần."""
    with _totp_lock:
        now = time.time()
        counter = int(now // 30)
        if _last_counter.get(user, -1) >= counter:
            time.sleep(30 - now % 30 + 0.5)
            counter = int(time.time() // 30)
        _last_counter[user] = counter
        return totp(raw)


# ------------------------------------------------------------------------------ PKCE ----
def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


def claims(jwt: str) -> dict[str, Any]:
    body = jwt.split(".")[1]
    data: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return data


# ------------------------------------------------------------------------------ HTML ----
class Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self.forms.append({"id": a.get("id", ""), "action": a.get("action", ""), "inputs": {}, "buttons": []})
        elif self.forms and tag in ("input", "button"):
            t = a.get("type", "submit" if tag == "button" else "text")
            name = a.get("name", "")
            if t in ("submit",) and name:
                self.forms[-1]["buttons"].append((name, a.get("value", "")))
            elif name and t in ("hidden", "text", "password", "email"):
                self.forms[-1]["inputs"][name] = a.get("value", "")


def forms(page: str) -> list[dict[str, Any]]:
    p = Forms()
    p.feed(page)
    return p.forms


def kc_error(page: str) -> str:
    m = re.search(r'id="input-error[^"]*"[^>]*>\s*([^<]+)<', page) or re.search(
        r'class="[^"]*(?:alert-error|pf-m-danger|kc-feedback-text)[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*([^<]+)<', page
    )
    return html.unescape(m.group(1).strip()) if m else ""


@dataclass
class Step:
    kind: str  # "redirect" | "page"
    url: str
    page: str = ""
    status: int = 0


@dataclass
class Browser:
    """Trình duyệt HTTP tối giản: cookie jar thật, theo 3xx cho tới khi gặp `stop` (redirect_uri)."""

    ip: str | None = None
    trail: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.c = net.client(self.ip)

    def close(self) -> None:
        self.c.close()

    def follow(self, r: httpx.Response, stop: str | None = None) -> Step:
        for _ in range(20):
            self.trail.append(f"{r.request.method} {urlsplit(str(r.request.url)).netloc}{urlsplit(str(r.request.url)).path} -> {r.status_code}")
            if r.status_code in (301, 302, 303, 307):
                loc = urljoin(str(r.request.url), r.headers["location"])
                if stop and loc.startswith(stop):
                    return Step("redirect", loc, status=r.status_code)
                r = self.c.get(loc)
                continue
            return Step("page", str(r.request.url), r.text, r.status_code)
        raise LoginError("quá nhiều chuyển hướng")

    def get(self, url: str, stop: str | None = None) -> Step:
        return self.follow(self.c.get(url), stop)

    def submit(self, page: str, form_id: str | None, data: dict[str, str], stop: str | None = None,
               match: str | None = None) -> Step:
        fs = forms(page)
        f = next((f for f in fs if (form_id and f["id"] == form_id) or (match and match in f["action"])), None)
        if f is None:
            raise LoginError(f"không thấy form {form_id or match}; lỗi trang: {kc_error(page)!r}")
        body = {**f["inputs"], **data}
        return self.follow(self.c.post(html.unescape(f["action"]), data=body), stop)

    def login_pages(self, step: Step, user: str, password: str, totp_raw: str | None,
                    stop: str, consent: bool = True) -> Step:
        """Đi qua các trang Keycloak (login, OTP, consent) tới redirect `stop`."""
        tried_password = False
        for _ in range(8):
            if step.kind == "redirect":
                return step
            page = step.page
            if 'id="kc-form-login"' in page:
                # Organizations bật => Keycloak 26 dùng identity-first: trang username, rồi trang mật khẩu.
                if 'name="password"' in page:
                    if tried_password:
                        raise LoginError(f"đăng nhập {user} thất bại: {kc_error(page)!r}")
                    tried_password = True
                    data = {"password": password, "credentialId": ""}
                    if 'name="username"' in page:
                        data["username"] = user
                    step = self.submit(page, "kc-form-login", data, stop)
                else:
                    step = self.submit(page, "kc-form-login", {"username": user}, stop)
            elif 'id="kc-otp-login-form"' in page:
                if not totp_raw:
                    raise LoginError(f"{user}: Keycloak đòi OTP nhưng không có TOTP seed")
                step = self.submit(page, "kc-otp-login-form", {"otp": fresh_totp(user, totp_raw)}, stop)
            elif "login-actions/consent" in page and consent:
                step = self.submit(page, None, {"accept": "Yes"}, stop, match="login-actions/consent")
            elif "kc-totp-settings-form" in page or "CONFIGURE_TOTP" in page:
                raise LoginError(f"{user}: Keycloak đòi cấu hình TOTP (seed chưa được nạp)")
            else:
                raise LoginError(f"{user}: trang Keycloak không mong đợi (HTTP {step.status}) {kc_error(page)!r} {page[:300]!r}")
        raise LoginError(f"{user}: không tới được {stop}")


# -------------------------------------------------------------------------- token ----
@dataclass
class Tokens:
    access: str
    refresh: str | None
    id_token: str | None
    expires_at: float
    raw: dict[str, Any]

    @property
    def claims(self) -> dict[str, Any]:
        return claims(self.access)


def token_endpoint() -> str:
    return f"{settings().issuer}/protocol/openid-connect/token"


def exchange(c: httpx.Client, client_id: str, redirect_uri: str, code: str, verifier: str,
             secret: str | None = None) -> Tokens:
    data = {"grant_type": "authorization_code", "client_id": client_id, "redirect_uri": redirect_uri,
            "code": code, "code_verifier": verifier}
    if secret:
        data["client_secret"] = secret
    r = c.post(token_endpoint(), data=data)
    if r.status_code != 200:
        raise LoginError(f"đổi code thất bại HTTP {r.status_code}: {r.text[:300]}")
    return _tokens(r.json())


def _tokens(j: dict[str, Any]) -> Tokens:
    return Tokens(j["access_token"], j.get("refresh_token"), j.get("id_token"),
                  time.time() + int(j.get("expires_in", 60)), j)


def refresh(c: httpx.Client, client_id: str, tok: Tokens) -> Tokens:
    r = c.post(token_endpoint(), data={"grant_type": "refresh_token", "client_id": client_id,
                                        "refresh_token": tok.refresh or ""})
    if r.status_code != 200:
        raise LoginError(f"refresh thất bại HTTP {r.status_code}: {r.text[:200]}")
    return _tokens(r.json())


def authorize_url(client_id: str, redirect_uri: str, scope: str, challenge: str, **extra: str) -> str:
    q = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": scope,
         "state": secrets.token_urlsafe(12), "nonce": secrets.token_urlsafe(12),
         "code_challenge": challenge, "code_challenge_method": "S256", **extra}
    return f"{settings().issuer}/protocol/openid-connect/auth?{urlencode(q)}"


def login(browser: Browser, client_id: str, redirect_uri: str, scope: str, user: str, password: str,
          totp_raw: str | None = None, **extra: str) -> Tokens:
    verifier, challenge = pkce()
    step = browser.get(authorize_url(client_id, redirect_uri, scope, challenge, **extra), stop=redirect_uri)
    step = browser.login_pages(step, user, password, totp_raw, stop=redirect_uri)
    q = parse_qs(urlsplit(step.url).query)
    if "code" not in q:
        raise LoginError(f"redirect không có code: {q.get('error')} {q.get('error_description')}")
    return exchange(browser.c, client_id, redirect_uri, q["code"][0], verifier)


# ------------------------------------------------------------------------- phiên ----
class Session:
    """Một người dùng + một client: đăng nhập một lần, sau đó refresh (không cần OTP lại)."""

    def __init__(self, user: dict[str, Any], client_id: str, redirect_uri: str, scope: str) -> None:
        self.user, self.client_id, self.redirect_uri, self.scope = user, client_id, redirect_uri, scope
        self.browser = Browser()
        self.tok: Tokens | None = None
        self.lock = threading.Lock()

    def access(self) -> str:
        with self.lock:
            if self.tok is None:
                self.tok = login(self.browser, self.client_id, self.redirect_uri, self.scope,
                                 self.user["username"], self.user["password"], self.user.get("totp"))
            elif self.tok.expires_at - time.time() < 60:
                try:
                    self.tok = refresh(self.browser.c, self.client_id, self.tok)
                except LoginError:
                    self.tok = login(self.browser, self.client_id, self.redirect_uri, self.scope,
                                     self.user["username"], self.user["password"], self.user.get("totp"))
            return self.tok.access


# ------------------------------------------------------------------------------ DCR ----
def register_client(redirect_uri: str, name: str, ip: str | None = None) -> dict[str, Any]:
    """DCR ẩn danh (RFC 7591) qua gateway, đúng như connector Claude Code (loopback)."""
    body = {"client_name": name, "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": "openid mcp-xt"}
    with net.client(ip) as c:
        r = c.post(f"{settings().issuer}/clients-registrations/openid-connect", json=body)
    if r.status_code != 201:
        raise LoginError(f"DCR thất bại HTTP {r.status_code}: {r.text[:300]}")
    data: dict[str, Any] = r.json()
    return data
