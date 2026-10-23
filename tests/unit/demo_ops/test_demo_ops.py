# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""deploy/demo-ops: TOTP, Basic auth, kiểm origin, gọi Keycloak Admin (máy chủ giả), tệp chế độ."""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import importlib.util
import json
import struct
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from xt.adapters.identity.confirm_mode import read_confirm_otp

_DIR = Path(__file__).resolve().parents[3] / "deploy" / "demo-ops"
_spec = importlib.util.spec_from_file_location("demo_ops_server", _DIR / "server.py")
assert _spec is not None and _spec.loader is not None
server: ModuleType = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server  # dataclass cần module đã đăng ký
_spec.loader.exec_module(server)

PASSWORD = "demo-ops-pass"
ADMIN_USER, ADMIN_PASS = "kc-admin", "kc-admin-pass"
TOTP_SECRET = "fake-seed-secret-not-a-real-one"


def script_totp(raw: str, now: int) -> str:
    """Bản sao thuật toán của ~/xt-demo-login.sh (nguyên văn), để so khớp byte-for-byte."""
    c = now // 30
    mac = hmac.new(raw.encode(), struct.pack(">Q", c), hashlib.sha1).digest()
    o = mac[-1] & 15
    code = (struct.unpack(">I", mac[o : o + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


# ----------------------------------------------------------------------------------- TOTP ---
@pytest.mark.parametrize("now", [0, 29, 30, 59, 1_700_000_000, 1_700_000_029, 1_900_000_001])
def test_totp_matches_script_algorithm(now: int) -> None:
    code, left = server.totp(TOTP_SECRET, now)
    assert code == script_totp(TOTP_SECRET, now)
    assert left == 30 - now % 30 and len(code) == 6


@pytest.mark.parametrize(
    ("now", "code"), [(59, "287082"), (1_111_111_109, "081804"), (1_234_567_890, "005924")]
)
def test_totp_rfc6238_sha1_vectors(now: int, code: str) -> None:
    # Khóa thô "12345678901234567890" giống RFC 6238 (SHA-1); 6 chữ số cuối của vector 8 chữ số.
    assert server.totp("12345678901234567890", now)[0] == code


def test_accounts_view_shapes() -> None:
    users = {
        "a.b": {
            "password": "p1",
            "totp": TOTP_SECRET,
            "name": "A B",
            "roles": ["x", "y"],
            "tenant": "t",
        },
        "c.d": {"password": "p2"},
    }
    a, c = server.accounts_view(users, 1_700_000_000)
    assert a["username"] == "a.b" and a["name"] == "A B" and a["role"] == "x, y"
    assert a["password"] == "p1" and a["code"] == script_totp(TOTP_SECRET, 1_700_000_000)
    assert a["seconds_left"] == 30 - 1_700_000_000 % 30
    assert (
        c["code"] is None and c["seconds_left"] is None and c["name"] is None and c["role"] is None
    )


# --------------------------------------------------------------------- Keycloak giả ---------
class FakeKeycloak:
    """Giả lập đúng các endpoint Admin REST mà demo-ops dùng; giữ trạng thái và nhật ký gọi."""

    def __init__(self) -> None:
        self.flow_req = "CONDITIONAL"
        self.client_acr = "urn:xt:acr:otp"
        self.calls: list[tuple[str, str]] = []
        self.token_form: dict[str, list[str]] = {}
        self.bearers: set[str | None] = set()
        self.fail_token = False
        self.admin_status = 200

    def executions(self) -> list[dict[str, Any]]:
        return [
            {"id": "e1", "displayName": "Cookie", "requirement": "ALTERNATIVE", "level": 0},
            {"id": "e2", "displayName": "forms", "requirement": "ALTERNATIVE", "level": 0},
            {
                "id": "e3",
                "displayName": "Browser - Conditional 2FA",
                "requirement": self.flow_req,
                "requirementChoices": ["REQUIRED", "CONDITIONAL", "DISABLED"],
                "level": 1,
                "authenticationFlow": True,
            },
            {"id": "e4", "displayName": "OTP Form", "requirement": "REQUIRED", "level": 2},
        ]

    def client(self) -> dict[str, Any]:
        return {
            "id": "uuid-1",
            "clientId": "xt-desk-confirm",
            "attributes": {
                "minimum.acr.value": self.client_acr,
                "pkce.code.challenge.method": "S256",
            },
        }

    def handler(self) -> type[BaseHTTPRequestHandler]:
        kc = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _reply(self, status: int, doc: Any = None) -> None:
                body = b"" if doc is None else json.dumps(doc).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def _admin_ok(self) -> bool:
                kc.bearers.add(self.headers.get("Authorization"))
                if kc.admin_status != 200 or self.headers.get("Authorization") != "Bearer kc-token":
                    self._reply(kc.admin_status if kc.admin_status != 200 else 401)
                    return False
                return True

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                kc.calls.append(("POST", path))
                if path == "/realms/master/protocol/openid-connect/token":
                    form = parse_qs(self._body().decode())
                    kc.token_form = form
                    ok = form.get("username") == [ADMIN_USER] and form.get("password") == [
                        ADMIN_PASS
                    ]
                    if ok and not kc.fail_token:
                        self._reply(200, {"access_token": "kc-token"})
                    else:
                        self._reply(401, {"error": "invalid_grant"})
                else:
                    self._reply(404)

            def do_GET(self) -> None:
                u = urlsplit(self.path)
                kc.calls.append(("GET", u.path + ("?" + u.query if u.query else "")))
                if not self._admin_ok():
                    return
                if u.path == "/admin/realms/xt":
                    self._reply(200, {"realm": "xt", "browserFlow": "browser"})
                elif u.path == "/admin/realms/xt/authentication/flows/browser/executions":
                    self._reply(200, kc.executions())
                elif u.path == "/admin/realms/xt/clients":
                    q = parse_qs(u.query)
                    self._reply(
                        200, [kc.client()] if q.get("clientId") == ["xt-desk-confirm"] else []
                    )
                else:
                    self._reply(404)

            def do_PUT(self) -> None:
                path = urlsplit(self.path).path
                kc.calls.append(("PUT", path))
                if not self._admin_ok():
                    return
                doc = json.loads(self._body())
                if path == "/admin/realms/xt/authentication/flows/browser/executions":
                    assert doc["id"] == "e3" and doc["displayName"] == "Browser - Conditional 2FA"
                    kc.flow_req = doc["requirement"]
                    self._reply(204)
                elif path == "/admin/realms/xt/clients/uuid-1":
                    assert doc["clientId"] == "xt-desk-confirm"
                    assert (
                        doc["attributes"]["pkce.code.challenge.method"] == "S256"
                    )  # giữ thuộc tính khác
                    kc.client_acr = doc["attributes"]["minimum.acr.value"]
                    self._reply(204)
                else:
                    self._reply(404)

        return H


def serve(handler: type[BaseHTTPRequestHandler]) -> Iterator[tuple[str, int]]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    t.start()
    try:
        yield "127.0.0.1", srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(5)


@pytest.fixture
def kc() -> Iterator[tuple[FakeKeycloak, str]]:
    fake = FakeKeycloak()
    for host, port in serve(fake.handler()):
        yield fake, f"http://{host}:{port}"


def kc_admin(base: str, password: str = ADMIN_PASS) -> Any:
    return server.KeycloakAdmin(
        server.KcConfig(base, "xt", "xt-desk-confirm", ADMIN_USER, password, timeout=5)
    )


def test_keycloak_read_uses_admin_token_and_finds_flow_by_display_name(
    kc: tuple[FakeKeycloak, str],
) -> None:
    fake, base = kc
    assert kc_admin(base).read() == {
        "flow_requirement": "CONDITIONAL",
        "client_acr": "urn:xt:acr:otp",
    }
    assert fake.token_form["grant_type"] == ["password"] and fake.token_form["client_id"] == [
        "admin-cli"
    ]
    assert fake.bearers == {"Bearer kc-token"}


def test_keycloak_set_otp_off_then_on(kc: tuple[FakeKeycloak, str]) -> None:
    fake, base = kc
    admin = kc_admin(base)
    admin.set_otp(False)
    assert (fake.flow_req, fake.client_acr) == ("DISABLED", "urn:xt:acr:pwd")
    admin.set_otp(True)
    assert (fake.flow_req, fake.client_acr) == ("CONDITIONAL", "urn:xt:acr:otp")


def test_keycloak_set_otp_is_idempotent_no_writes(kc: tuple[FakeKeycloak, str]) -> None:
    fake, base = kc
    kc_admin(base).set_otp(True)  # đã bật sẵn
    assert [c for c in fake.calls if c[0] == "PUT"] == []


def test_keycloak_bad_admin_password_and_down(kc: tuple[FakeKeycloak, str]) -> None:
    _, base = kc
    with pytest.raises(server.KeycloakError):
        kc_admin(base, "wrong").token()
    with pytest.raises(server.KeycloakError) as e:
        kc_admin("http://127.0.0.1:9").read()  # cổng đóng
    assert ADMIN_PASS not in str(e.value)


def test_keycloak_missing_execution_is_error(kc: tuple[FakeKeycloak, str]) -> None:
    fake, base = kc
    fake.executions = lambda: [{"id": "x", "displayName": "other"}]  # type: ignore[method-assign]
    with pytest.raises(server.KeycloakError, match="Conditional 2FA"):
        kc_admin(base).read()


# ----------------------------------------------------------------------- ứng dụng HTTP -----
class Env:
    def __init__(self, app: Any, host: str, port: int, fake: FakeKeycloak, mode: Path) -> None:
        self.app, self.host, self.port, self.fake, self.mode = app, host, port, fake, mode

    def req(
        self,
        method: str,
        path: str,
        *,
        auth: str | None = f"x:{PASSWORD}",
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        h = dict(headers or {})
        if auth is not None:
            h["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
        data = (
            body
            if isinstance(body, bytes)
            else (None if body is None else json.dumps(body).encode())
        )
        c = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            c.close()

    def origin(self) -> dict[str, str]:
        return {"Origin": f"http://{self.host}:{self.port}", "Content-Type": "application/json"}


@pytest.fixture
def env(
    tmp_path: Path, kc: tuple[FakeKeycloak, str], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Env]:
    monkeypatch.setattr(server, "AUTH_FAIL_DELAY", 0)
    fake, base = kc
    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "users": {
                    "tri.nguyenhuu": {
                        "password": "pw-tri",
                        "totp": TOTP_SECRET,
                        "name": "Nguyễn Hữu Trí",
                        "roles": ["director"],
                    },
                    "dung.phantien": {
                        "password": "pw-dung",
                        "name": "Phan Tiến Dũng",
                        "roles": ["employee"],
                    },
                }
            }
        )
    )
    mode = tmp_path / "state" / "mode.json"
    mode.parent.mkdir()
    app = server.App(
        password=PASSWORD,
        kc=kc_admin(base),
        seed_file=seed,
        mode_file=mode,
        html='<!doctype html><style nonce="__NONCE__"></style>demo',
        clock=lambda: 1_700_000_000.0,
    )
    for host, port in serve(server.make_handler(app)):
        yield Env(app, host, port, fake, mode)


def test_auth_required_everywhere(env: Env) -> None:
    for method, path in [
        ("GET", "/demo/"),
        ("GET", "/demo/api/state"),
        ("GET", "/demo/api/accounts"),
        ("POST", "/demo/api/otp"),
    ]:
        st, h, _ = env.req(method, path, auth=None, headers=env.origin())
        assert st == 401 and h["www-authenticate"].startswith("Basic"), (method, path)
    assert env.fake.calls == []


@pytest.mark.parametrize("auth", ["x:wrong", "x:", f"x:{PASSWORD}x", f"x:{PASSWORD[:-1]}", ":"])
def test_wrong_password_rejected(env: Env, auth: str) -> None:
    assert env.req("GET", "/demo/api/state", auth=auth)[0] == 401


def test_malformed_authorization_headers(env: Env) -> None:
    for value in [
        "Basic !!!notbase64",
        "Basic",
        "Bearer abc",
        "Basic " + base64.b64encode(b"\xff\xfe").decode(),
    ]:
        st, _, _ = env.req("GET", "/demo/api/state", auth=None, headers={"Authorization": value})
        assert st == 401, value


def test_any_username_with_right_password_and_colon_in_password(env: Env) -> None:
    assert env.req("GET", "/demo/api/state", auth=f"whoever:{PASSWORD}")[0] == 200
    env.app.password = "pa:ss"
    assert env.req("GET", "/demo/api/state", auth="u:pa:ss")[0] == 200


def test_constant_time_compare_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(server.hmac, "compare_digest", spy)
    app = server.App(password="s3", kc=None, seed_file=Path("x"), mode_file=Path("y"), html="")
    assert app.authorized("Basic " + base64.b64encode(b"u:s3").decode())
    assert seen == [(b"s3", b"s3")]


def test_page_headers_and_nonce(env: Env) -> None:
    st, h, body = env.req("GET", "/demo/")
    assert st == 200 and h["content-type"].startswith("text/html")
    assert h["cache-control"] == "no-store" and h["x-content-type-options"] == "nosniff"
    nonce = h["content-security-policy"].split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in body.decode() and "__NONCE__" not in body.decode()
    assert "'unsafe-inline'" not in h["content-security-policy"]
    st2, h2, _ = env.req("GET", "/demo")
    assert st2 == 301 and h2["location"] == "/demo/"


def test_every_authenticated_response_is_no_store(env: Env) -> None:
    for path in ["/demo/api/state", "/demo/api/accounts", "/demo/nope"]:
        _, h, _ = env.req("GET", path)
        assert h["cache-control"] == "no-store", path
    assert env.req("GET", "/demo/nope")[0] == 404


def test_accounts_endpoint(env: Env) -> None:
    st, _, body = env.req("GET", "/demo/api/accounts")
    doc = json.loads(body)
    assert st == 200 and doc["period"] == 30 and doc["server_time"] == 1_700_000_000
    tri, dung = doc["accounts"]
    assert tri == {
        "username": "tri.nguyenhuu",
        "name": "Nguyễn Hữu Trí",
        "role": "director",
        "tenant": None,
        "password": "pw-tri",
        "code": script_totp(TOTP_SECRET, 1_700_000_000),
        "seconds_left": 30 - 1_700_000_000 % 30,
    }
    assert dung["code"] is None and dung["password"] == "pw-dung"


def test_accounts_missing_seed_is_500_without_leak(env: Env) -> None:
    env.app.seed_file = Path("/nonexistent/seed.json")
    st, _, body = env.req("GET", "/demo/api/accounts")
    assert st == 500 and b"nonexistent" not in body


# ------------------------------------------------------------------------- same-origin ------
def test_post_origin_checks(env: Env) -> None:
    on = {"on": False}
    base = {"Content-Type": "application/json"}
    assert env.req("POST", "/demo/api/otp", body=on, headers=base)[0] == 403  # thiếu Origin/Referer
    for bad in [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": f"http://{env.host}:{env.port + 1}"},
        {"Referer": "https://evil.example/demo/"},
        {"Origin": f"http://{env.host}.evil.example:{env.port}"},
    ]:
        st, _, _ = env.req("POST", "/demo/api/otp", body=on, headers={**base, **bad})
        assert st == 403, bad
    assert env.fake.flow_req == "CONDITIONAL"  # chưa chạm Keycloak
    ok = env.req(
        "POST",
        "/demo/api/otp",
        body=on,
        headers={**base, "Referer": f"http://{env.host}:{env.port}/demo/"},
    )
    assert ok[0] == 200


def test_post_origin_behind_proxy_uses_forwarded_host(env: Env) -> None:
    hdr = {
        "Origin": "https://xt.freepro.online",
        "X-Forwarded-Host": "xt.freepro.online",
        "Content-Type": "application/json",
    }
    assert env.req("POST", "/demo/api/otp", body={"on": False}, headers=hdr)[0] == 200
    hdr["X-Forwarded-Host"] = "other.example"
    assert env.req("POST", "/demo/api/otp", body={"on": True}, headers=hdr)[0] == 403


@pytest.mark.parametrize("body", [{"on": "yes"}, {"on": 1}, {"on": None}, {}, [], b"not json", b""])
def test_post_body_validation(env: Env, body: Any) -> None:
    st, _, _ = env.req("POST", "/demo/api/otp", body=body, headers=env.origin())
    assert st == 400 and env.fake.flow_req == "CONDITIONAL"


# --------------------------------------------------------------------- bật/tắt + state ------
def test_toggle_off_and_on_end_to_end(env: Env) -> None:
    st, _, body = env.req("GET", "/demo/api/state")
    s = json.loads(body)
    assert st == 200 and s["otp"] is True
    assert s["mismatch"] == ["tệp chế độ của xt-api chưa có/hỏng"]  # tệp chưa tồn tại

    st, _, body = env.req("POST", "/demo/api/otp", body={"on": False}, headers=env.origin())
    s = json.loads(body)
    assert st == 200 and s["otp"] is False and s["consistent"] and s["mismatch"] == []
    assert (env.fake.flow_req, env.fake.client_acr) == ("DISABLED", "urn:xt:acr:pwd")
    assert json.loads(env.mode.read_text())["otp"] is False
    assert read_confirm_otp(env.mode) is False  # xt-api đọc được đúng tệp này
    assert oct(env.mode.stat().st_mode & 0o777) == "0o644"

    st, _, body = env.req("POST", "/demo/api/otp", body={"on": True}, headers=env.origin())
    s = json.loads(body)
    assert s["otp"] is True and s["consistent"]
    assert (env.fake.flow_req, env.fake.client_acr) == ("CONDITIONAL", "urn:xt:acr:otp")
    assert read_confirm_otp(env.mode) is True
    assert [p.name for p in env.mode.parent.iterdir()] == ["mode.json"]  # không để tệp tạm


def test_state_reports_keycloak_internal_mismatch(env: Env) -> None:
    env.fake.flow_req, env.fake.client_acr = "DISABLED", "urn:xt:acr:otp"
    s = json.loads(env.req("GET", "/demo/api/state")[2])
    assert s["otp"] is None and s["consistent"] is False
    assert any("không cùng chế độ" in m for m in s["mismatch"])
    assert s["keycloak"] == {"flow_requirement": "DISABLED", "client_acr": "urn:xt:acr:otp"}


def test_state_reports_unknown_values(env: Env) -> None:
    env.fake.flow_req, env.fake.client_acr = "REQUIRED", "urn:other"
    s = json.loads(env.req("GET", "/demo/api/state")[2])
    assert s["otp"] is None and len(s["mismatch"]) == 2


def test_state_reports_mode_file_mismatch(env: Env) -> None:
    server.write_mode_file(env.mode, False)  # tệp nói tắt, Keycloak đang bật
    s = json.loads(env.req("GET", "/demo/api/state")[2])
    assert s["otp"] is True and s["mode_file"] is False and s["consistent"] is False
    assert s["mismatch"] == ["tệp chế độ của xt-api đang tắt"]


def test_state_when_keycloak_down(env: Env) -> None:
    env.app.kc = kc_admin("http://127.0.0.1:9")
    st, _, body = env.req("GET", "/demo/api/state")
    s = json.loads(body)
    assert st == 200 and s["otp"] is None and s["consistent"] is False and s["keycloak"] is None


def test_toggle_failure_is_502_and_leaves_mode_file_alone(env: Env) -> None:
    server.write_mode_file(env.mode, True)
    env.fake.fail_token = True
    st, _, body = env.req("POST", "/demo/api/otp", body={"on": False}, headers=env.origin())
    assert st == 502 and ADMIN_PASS.encode() not in body
    assert read_confirm_otp(env.mode) is True and env.fake.flow_req == "CONDITIONAL"


def test_sync_mode_file_follows_keycloak(env: Env) -> None:
    env.fake.flow_req, env.fake.client_acr = "DISABLED", "urn:xt:acr:pwd"
    assert env.app.sync_mode_file() is True
    assert read_confirm_otp(env.mode) is False
    env.fake.flow_req = "REQUIRED"  # không nhất quán => không ghi
    server.write_mode_file(env.mode, True)
    assert env.app.sync_mode_file() is True and read_confirm_otp(env.mode) is True
    env.app.kc = kc_admin("http://127.0.0.1:9")
    assert env.app.sync_mode_file() is False


def test_build_app_reads_secret_files(tmp_path: Path) -> None:
    files = {"pw": "pass\n", "u": "admin\n", "ap": "adminpw\r\n"}
    for name, val in files.items():
        (tmp_path / name).write_text(val)
    app = server.build_app(
        {
            "DEMO_OPS_PASSWORD_FILE": str(tmp_path / "pw"),
            "KC_ADMIN_USER_FILE": str(tmp_path / "u"),
            "KC_ADMIN_PASSWORD_FILE": str(tmp_path / "ap"),
            "DEMO_SEED_FILE": str(tmp_path / "seed.json"),
        }
    )
    assert app.password == "pass"
    assert (app.kc.cfg.admin_user, app.kc.cfg.admin_password) == ("admin", "adminpw")
    assert app.kc.cfg.base_url == "http://keycloak:8080" and app.kc.cfg.realm == "xt"
    with pytest.raises(SystemExit):
        server.build_app({})
