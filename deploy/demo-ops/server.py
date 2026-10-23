# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Demo-ops: bật/tắt OTP, xem mật khẩu + mã TOTP tài khoản demo (CHỈ môi trường demo).

Chỉ stdlib. Một tiến trình, không cổng publish; APISIX route `/demo/*` tới đây
(docs/deploy-demo-ops.md).

  GET  /demo/                trang giao diện
  GET  /demo/api/state       trạng thái OTP thật từ Keycloak (nguồn sự thật) + tệp chế độ
  POST /demo/api/otp {on}    bật/tắt OTP: flow "Browser - Conditional 2FA" + acr + tệp chế độ
  GET  /demo/api/accounts    tài khoản, mật khẩu, mã TOTP hiện tại, số giây còn lại

Xác thực HTTP Basic (mật khẩu đọc từ tệp), so sánh hằng thời gian; POST kiểm cùng origin.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import struct
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PERIOD = 30
DIGITS = 6
OTP_ACR = "urn:xt:acr:otp"
PWD_ACR = "urn:xt:acr:pwd"
FLOW_NAME = "Browser - Conditional 2FA"
HERE = Path(__file__).resolve().parent
MAX_BODY = 1024
AUTH_FAIL_DELAY = 0.4  # làm chậm dò mật khẩu


# ---------------------------------------------------------------------------------------------
# TOTP: HMAC-SHA1 trên chuỗi bí mật thô (UTF-8, KHÔNG base32), 30 s, 6 chữ số (như xt-demo-login.sh)
# ---------------------------------------------------------------------------------------------
def totp(secret: str, now: float) -> tuple[str, int]:
    counter = int(now) // PERIOD
    mac = hmac.new(secret.encode(), struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 15
    code = (struct.unpack(">I", mac[off : off + 4])[0] & 0x7FFFFFFF) % 10**DIGITS
    return f"{code:0{DIGITS}d}", PERIOD - int(now) % PERIOD


def read_secret(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").rstrip("\r\n")


def load_users(seed_file: Path) -> dict[str, dict[str, Any]]:
    doc = json.loads(seed_file.read_text(encoding="utf-8"))
    users = doc.get("users") if isinstance(doc, dict) else None
    if not isinstance(users, dict):
        raise ValueError("tệp seed thiếu khóa `users`")
    return users


def accounts_view(users: Mapping[str, Mapping[str, Any]], now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for username, u in users.items():
        raw = u.get("totp")
        code, left = totp(raw, now) if isinstance(raw, str) and raw else (None, None)
        roles = u.get("roles")
        out.append(
            {
                "username": username,
                "name": u.get("name"),
                "role": ", ".join(roles) if isinstance(roles, list) else None,
                "tenant": u.get("tenant"),
                "password": u.get("password"),
                "code": code,
                "seconds_left": left,
            }
        )
    return out


# ---------------------------------------------------------------------------------------------
# Keycloak Admin REST
# ---------------------------------------------------------------------------------------------
class KeycloakError(Exception):
    """Lỗi gọi Keycloak; thông điệp không chứa bí mật."""


@dataclass(frozen=True)
class KcConfig:
    base_url: str
    realm: str
    client_id: str
    admin_user: str
    admin_password: str
    timeout: float = 10.0


class KeycloakAdmin:
    def __init__(self, cfg: KcConfig) -> None:
        self.cfg = cfg

    # -- HTTP --------------------------------------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        form: Mapping[str, str] | None = None,
        body: Any = None,
    ) -> Any:
        data: bytes | None = None
        headers = {"Accept": "application/json"}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            self.cfg.base_url.rstrip("/") + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise KeycloakError(
                f"Keycloak {method} {path.split('?')[0]} -> HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise KeycloakError(f"Keycloak không trả lời ({type(exc).__name__})") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise KeycloakError(
                f"Keycloak {method} {path.split('?')[0]}: phản hồi không phải JSON"
            ) from exc

    def token(self) -> str:
        res = self._call(
            "POST",
            "/realms/master/protocol/openid-connect/token",
            form={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": self.cfg.admin_user,
                "password": self.cfg.admin_password,
            },
        )
        tok = res.get("access_token") if isinstance(res, dict) else None
        if not isinstance(tok, str) or not tok:
            raise KeycloakError("Keycloak không cấp token quản trị")
        return tok

    # -- thao tác ----------------------------------------------------------------------------
    def _realm(self) -> str:
        return f"/admin/realms/{urllib.parse.quote(self.cfg.realm, safe='')}"

    def _flow_execution(self, tok: str) -> tuple[str, dict[str, Any]]:
        realm = self._call("GET", self._realm(), token=tok)
        flow = realm.get("browserFlow") if isinstance(realm, dict) else None
        if not isinstance(flow, str) or not flow:
            raise KeycloakError("realm không có browserFlow")
        alias = urllib.parse.quote(flow, safe="")
        execs = self._call(
            "GET", f"{self._realm()}/authentication/flows/{alias}/executions", token=tok
        )
        found = [
            e for e in execs or [] if isinstance(e, dict) and e.get("displayName") == FLOW_NAME
        ]
        if len(found) != 1:
            raise KeycloakError(
                f"không tìm thấy đúng một execution '{FLOW_NAME}' (thấy {len(found)})"
            )
        return alias, found[0]

    def _client(self, tok: str) -> dict[str, Any]:
        q = urllib.parse.urlencode({"clientId": self.cfg.client_id})
        res = self._call("GET", f"{self._realm()}/clients?{q}", token=tok)
        found = [
            c for c in res or [] if isinstance(c, dict) and c.get("clientId") == self.cfg.client_id
        ]
        if len(found) != 1:
            raise KeycloakError(f"không tìm thấy client '{self.cfg.client_id}'")
        return found[0]

    def read(self) -> dict[str, str | None]:
        """Giá trị thật đang chạy trên Keycloak."""
        tok = self.token()
        _, ex = self._flow_execution(tok)
        client = self._client(tok)
        attrs = client.get("attributes")
        acr = attrs.get("minimum.acr.value") if isinstance(attrs, dict) else None
        req = ex.get("requirement")
        return {
            "flow_requirement": req if isinstance(req, str) else None,
            "client_acr": acr if isinstance(acr, str) else None,
        }

    def set_otp(self, on: bool) -> None:
        tok = self.token()
        alias, ex = self._flow_execution(tok)
        choices = ex.get("requirementChoices")
        want = "CONDITIONAL" if on else "DISABLED"
        if isinstance(choices, list) and want not in choices:
            raise KeycloakError(f"execution không cho phép requirement {want}")
        if ex.get("requirement") != want:
            self._call(
                "PUT",
                f"{self._realm()}/authentication/flows/{alias}/executions",
                token=tok,
                body={**ex, "requirement": want},
            )
        client = self._client(tok)
        attrs = dict(client.get("attributes") or {})
        acr = OTP_ACR if on else PWD_ACR
        if attrs.get("minimum.acr.value") != acr:
            attrs["minimum.acr.value"] = acr
            cid = urllib.parse.quote(str(client["id"]), safe="")
            self._call(
                "PUT",
                f"{self._realm()}/clients/{cid}",
                token=tok,
                body={**client, "attributes": attrs},
            )


# ---------------------------------------------------------------------------------------------
# Tệp chế độ dùng chung với xt-api (XT_CONFIRM_MODE_FILE)
# ---------------------------------------------------------------------------------------------
def read_mode_file(path: Path) -> bool | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    otp = doc.get("otp") if isinstance(doc, dict) else None
    return otp if isinstance(otp, bool) else None


def write_mode_file(path: Path, on: bool) -> None:
    """Ghi nguyên tử (tạo tệp tạm cùng thư mục rồi rename) để xt-api không bao giờ đọc nửa chừng."""
    body = json.dumps({"otp": on, "updated_at": datetime.now(UTC).isoformat(timespec="seconds")})
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mode-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _kc_flag(value: str | None, on: str, off: str) -> bool | None:
    return True if value == on else False if value == off else None


# ---------------------------------------------------------------------------------------------
# Ứng dụng
# ---------------------------------------------------------------------------------------------
class App:
    def __init__(
        self,
        *,
        password: str,
        kc: KeycloakAdmin,
        seed_file: Path,
        mode_file: Path,
        html: str | None = None,
        clock: Any = time.time,
    ) -> None:
        self.password = password
        self.kc = kc
        self.seed_file = seed_file
        self.mode_file = mode_file
        self.html = html if html is not None else (HERE / "index.html").read_text(encoding="utf-8")
        self.clock = clock
        self.lock = threading.Lock()

    # -- auth --------------------------------------------------------------------------------
    def authorized(self, header: str | None) -> bool:
        ok = False
        if header and header[:6].lower() == "basic ":
            try:
                _user, _, pw = (
                    base64.b64decode(header[6:].strip(), validate=True).decode().partition(":")
                )
            except (binascii.Error, UnicodeDecodeError, ValueError):
                pw = ""
            ok = hmac.compare_digest(pw.encode(), self.password.encode())
        return ok

    @staticmethod
    def same_origin(headers: Mapping[str, str]) -> bool:
        """POST phải có Origin (hoặc Referer) cùng host với Host / X-Forwarded-Host."""
        src = headers.get("Origin") or headers.get("Referer")
        if not src or src == "null":
            return False
        netloc = urllib.parse.urlsplit(src).netloc.lower()
        hosts = {h.strip().lower() for k in ("Host", "X-Forwarded-Host") if (h := headers.get(k))}
        return bool(netloc) and netloc in hosts

    # -- nghiệp vụ ---------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        mode = read_mode_file(self.mode_file)
        try:
            kc = self.kc.read()
        except KeycloakError as exc:
            return {
                "otp": None,
                "consistent": False,
                "keycloak": None,
                "mode_file": mode,
                "mismatch": ["keycloak: " + str(exc)],
            }
        flow = _kc_flag(kc["flow_requirement"], "CONDITIONAL", "DISABLED")
        acr = _kc_flag(kc["client_acr"], OTP_ACR, PWD_ACR)
        mismatch: list[str] = []
        if flow is None:
            mismatch.append(f"flow ở trạng thái lạ: {kc['flow_requirement']}")
        if acr is None:
            mismatch.append(f"minimum.acr.value lạ: {kc['client_acr']}")
        if flow is not None and acr is not None and flow != acr:
            mismatch.append("flow và minimum.acr.value Keycloak không cùng chế độ")
        otp = flow if flow == acr else None
        if otp is not None and mode != otp:
            mismatch.append(
                "tệp chế độ của xt-api "
                + ("chưa có/hỏng" if mode is None else f"đang {'bật' if mode else 'tắt'}")
            )
        return {
            "otp": otp,
            "consistent": not mismatch,
            "keycloak": kc,
            "mode_file": mode,
            "mismatch": mismatch,
        }

    def set_otp(self, on: bool) -> dict[str, Any]:
        with self.lock:
            self.kc.set_otp(on)
            write_mode_file(self.mode_file, on)
        return self.state()

    def sync_mode_file(self) -> bool:
        """Lúc khởi động: tệp chế độ khớp Keycloak (nếu nhất quán). False = Keycloak chưa lên."""
        st = self.state()
        if st["keycloak"] is None:
            return False
        if st["otp"] is not None and st["mode_file"] != st["otp"]:
            with self.lock:
                write_mode_file(self.mode_file, st["otp"])
        return True

    def accounts(self) -> dict[str, Any]:
        now = self.clock()
        return {
            "period": PERIOD,
            "server_time": int(now),
            "accounts": accounts_view(load_users(self.seed_file), now),
        }


def make_handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "demo-ops"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")  # không log header/body

        def _send(
            self,
            status: int,
            body: bytes,
            ctype: str = "application/json; charset=utf-8",
            extra: Mapping[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, doc: Any) -> None:
            self._send(status, json.dumps(doc, ensure_ascii=False).encode())

        def _auth(self) -> bool:
            if app.authorized(self.headers.get("Authorization")):
                return True
            time.sleep(AUTH_FAIL_DELAY)
            self._send(
                HTTPStatus.UNAUTHORIZED,
                b'{"error":"unauthorized"}',
                extra={"WWW-Authenticate": 'Basic realm="xT demo", charset="UTF-8"'},
            )
            return False

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/demo":
                self._send(HTTPStatus.MOVED_PERMANENTLY, b"", extra={"Location": "/demo/"})
            elif not self._auth():
                return
            elif path == "/demo/":
                nonce = secrets.token_urlsafe(16)
                csp = (
                    f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                    "connect-src 'self'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'"
                )
                self._send(
                    HTTPStatus.OK,
                    app.html.replace("__NONCE__", nonce).encode(),
                    "text/html; charset=utf-8",
                    {"Content-Security-Policy": csp},
                )
            elif path == "/demo/api/state":
                self._json(HTTPStatus.OK, app.state())
            elif path == "/demo/api/accounts":
                try:
                    self._json(HTTPStatus.OK, app.accounts())
                except (OSError, ValueError):
                    self._json(
                        HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "không đọc được tệp seed"}
                    )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if not self._auth():
                return
            if path != "/demo/api/otp":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not app.same_origin(self.headers):
                self._json(HTTPStatus.FORBIDDEN, {"error": "cross_origin"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "0")
                if not 0 < length <= MAX_BODY:
                    raise ValueError
                doc = json.loads(self.rfile.read(length))
                on = doc["on"] if isinstance(doc, dict) else None
                if not isinstance(on, bool):
                    raise ValueError
            except (ValueError, KeyError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": 'cần JSON {"on": true|false}'})
                return
            try:
                self._json(HTTPStatus.OK, app.set_otp(on))
            except (KeycloakError, OSError) as exc:
                self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    return Handler


def build_app(env: Mapping[str, str]) -> App:
    def need(name: str) -> str:
        v = env.get(name, "").strip()
        if not v:
            sys.exit(f"thiếu biến {name}")
        return v

    password = read_secret(need("DEMO_OPS_PASSWORD_FILE"))
    if not password:
        sys.exit("DEMO_OPS_PASSWORD_FILE rỗng")
    kc = KeycloakAdmin(
        KcConfig(
            base_url=env.get("KC_URL", "http://keycloak:8080"),
            realm=env.get("KC_REALM", "xt"),
            client_id=env.get("DEMO_OPS_CONFIRM_CLIENT", "xt-desk-confirm"),
            admin_user=read_secret(need("KC_ADMIN_USER_FILE")),
            admin_password=read_secret(need("KC_ADMIN_PASSWORD_FILE")),
        )
    )
    return App(
        password=password,
        kc=kc,
        seed_file=Path(need("DEMO_SEED_FILE")),
        mode_file=Path(env.get("DEMO_OPS_MODE_FILE", "/state/mode.json")),
    )


def main() -> int:
    app = build_app(os.environ)

    def startup_sync() -> None:
        for _ in range(12):
            try:
                if app.sync_mode_file():
                    return
            except OSError as exc:
                print(f"sync tệp chế độ: {exc}", file=sys.stderr)
            time.sleep(10)

    threading.Thread(target=startup_sync, daemon=True).start()
    port = int(os.environ.get("DEMO_OPS_PORT", "8090"))
    srv = ThreadingHTTPServer((os.environ.get("DEMO_OPS_HOST", "0.0.0.0"), port), make_handler(app))
    print(f"demo-ops lắng nghe :{port}", file=sys.stderr)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
