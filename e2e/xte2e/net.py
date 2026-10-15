# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ánh xạ hostname công khai -> APISIX 127.0.0.1:9080 ở tầng transport của httpx.

URL logic giữ nguyên `https://<host>/...` (cookie jar, redirect, issuer đều đúng như production);
chỉ socket được nối tới gateway với `Host` gốc. Không sửa /etc/hosts. Host không thuộc danh sách
và không phải loopback bị chặn (harness không bao giờ ra mạng thật).
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import httpx2

from xte2e.config import settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def run_ip() -> str:
    """IP client giả lập Cloudflare (`CF-Connecting-IP`, APISIX chỉ tin từ mạng nội bộ/loopback).

    Dùng TEST-NET-3 (203.0.113.0/24, RFC 5737) như deploy/tests; mỗi lần chạy một IP để rate-limit
    theo IP (DCR 20/giờ) của các lần chạy trước không ảnh hưởng.
    """
    return f"203.0.113.{random.randint(1, 254)}"


_IP = run_ip()


def _rewrite(mod: Any, request: Any, ip: str) -> Any:
    s = settings()
    host = request.url.host
    if host in s.hosts.values():
        gw_host, gw_port = s.gateway
        url = request.url.copy_with(scheme="http", host=gw_host, port=gw_port)
        headers = request.headers.copy()
        headers["host"] = host
        headers["cf-connecting-ip"] = ip
        headers["x-forwarded-proto"] = "https"
        return mod.Request(
            request.method, url, headers=headers, stream=request.stream, extensions=request.extensions
        )
    if host in LOOPBACK:
        return request
    raise mod.ConnectError(f"e2e: host {host!r} không được ánh xạ (chặn ra mạng thật)", request=request)


class Mapped(httpx.BaseTransport):
    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx.HTTPTransport()
        self.ip = ip or _IP

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.inner.handle_request(_rewrite(httpx, request, self.ip))

    def close(self) -> None:
        self.inner.close()


class AsyncMapped(httpx.AsyncBaseTransport):
    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.ip = ip or _IP

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.inner.handle_async_request(_rewrite(httpx, request, self.ip))

    async def aclose(self) -> None:
        await self.inner.aclose()


class AsyncMapped2(httpx2.AsyncBaseTransport):
    """Bản cho `httpx2` (thư viện `mcp` 2.x dùng httpx2)."""

    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx2.AsyncHTTPTransport()
        self.ip = ip or _IP

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await self.inner.handle_async_request(_rewrite(httpx2, request, self.ip))

    async def aclose(self) -> None:
        await self.inner.aclose()


def client(ip: str | None = None, **kw: Any) -> httpx.Client:
    kw.setdefault("timeout", 30)
    kw.setdefault("follow_redirects", False)
    return httpx.Client(transport=Mapped(ip), **kw)


class HostProxy:
    """Proxy HTTP loopback `http://127.0.0.1:<cổng>` -> APISIX với `Host` cố định.

    Dùng cho công cụ không tự ánh xạ host được (vd. `headless-demo.sh --get-token` gọi urllib tới
    `XT_AUTH_BASE`): công cụ trỏ vào proxy, proxy gửi qua gateway thật như Cloudflare Tunnel.
    """

    def __init__(self, host: str) -> None:
        import http.server
        import threading

        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _go(self) -> None:
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else None
                hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")}
                with client() as c:
                    r = c.request(self.command, f"https://{outer.host}{self.path}", headers=hdr, content=body)
                self.send_response(r.status_code)
                for k, v in r.headers.multi_items():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                        self.send_header(k, v)
                self.send_header("content-length", str(len(r.content)))
                self.end_headers()
                self.wfile.write(r.content)

            do_GET = do_POST = do_HEAD = _go

            def log_message(self, *a: Any) -> None:
                pass

        self.host = host
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self) -> HostProxy:
        self.th.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.srv.shutdown()


class TlsGateway:
    """Đầu cuối TLS loopback cho công cụ dùng URL https thật (curl theo `jwks_uri` của discovery):
    `curl -k --connect-to ::127.0.0.1:<cổng>` -> TLS cục bộ (chứng chỉ tự ký, chỉ trong lần chạy) ->
    APISIX theo `Host`, giống vai trò Cloudflare Tunnel ở production. Host lạ -> 421."""

    def __init__(self) -> None:
        import datetime
        import http.server
        import ssl
        import tempfile
        import threading

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        hosts = list(settings().hosts.values())
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "xt-e2e-local")])
        now = datetime.datetime.now(datetime.UTC)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
                .not_valid_after(now + datetime.timedelta(hours=2))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
                .sign(key, hashes.SHA256()))
        self.dir = tempfile.mkdtemp(prefix="xt-e2e-tls-")
        cf, kf = f"{self.dir}/c.pem", f"{self.dir}/k.pem"
        with open(cf, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(kf, "wb") as f:
            f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))

        class H(http.server.BaseHTTPRequestHandler):
            def _go(self) -> None:
                host = (self.headers.get("host") or "").split(":")[0]
                if host not in hosts:
                    self.send_response(421)
                    self.end_headers()
                    return
                n = int(self.headers.get("content-length") or 0)
                body = self.rfile.read(n) if n else None
                hdr = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "connection")}
                with client() as c:
                    r = c.request(self.command, f"https://{host}{self.path}", headers=hdr, content=body)
                self.send_response(r.status_code)
                for k, v in r.headers.multi_items():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                        self.send_header(k, v)
                self.send_header("content-length", str(len(r.content)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(r.content)

            do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_OPTIONS = _go

            def log_message(self, *a: Any) -> None:
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cf, kf)
        self.srv.socket = ctx.wrap_socket(self.srv.socket, server_side=True)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)

    def __enter__(self) -> TlsGateway:
        self.th.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        import shutil

        self.srv.shutdown()
        shutil.rmtree(self.dir, ignore_errors=True)
