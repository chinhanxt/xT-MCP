# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cặp container riêng cho test Tầng 1 /data: `postgres:17-alpine` + PostgREST (image local).

- Không pull image (đĩa `/` gần đầy): thiếu image thì fail rõ ràng.
- Trước khi chạy docker: `free -g` cột available ≥ 4 GB (chờ tối đa `XT_IT_MEM_WAIT_S`).
- Tên container duy nhất `xt-it-hd-<hex>-{pg,rest}`, nhãn `xt.it=headless-data`; PostgREST dùng
  chung network namespace của container Postgres (không tạo network, không đụng stack đang
  chạy). Cổng publish chỉ trên 127.0.0.1, chọn ngẫu nhiên.
- Dọn dẹp bảo đảm: `stop()` trong finally của fixture + `atexit` (kể cả khi Ctrl-C).
- Mật khẩu DB, khóa HMAC audit, khóa RSA ký JWT đều sinh ngẫu nhiên mỗi lần chạy; token không bao
  giờ được ghi ra log/bằng chứng (chỉ tên biến `$TOKEN_…`).
"""

from __future__ import annotations

import atexit
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import jwt
import psycopg
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

PG_IMAGE = os.environ.get("XT_IT_PG_IMAGE", "postgres:17-alpine")
PGRST_IMAGE = os.environ.get(
    "XT_IT_PGRST_IMAGE",
    "postgrest/postgrest@sha256:d155c6718ed9a9f990d159a2ab7c0a3f16944dbb6d0a0344557421042acfe0df",
)
LABEL = "xt.it=headless-data"
AUTHENTICATOR = "xt_authenticator"
WEB_ROLE = "xt_web"
AUDIENCE = "xt-data"
ISSUER = "https://xt-auth.it.invalid/realms/xt"
DATA_CLIENT = "xt-desk"
# Như deploy: role DB lấy từ claim `data_role` (mapper hardcoded của scope xt-data = xt_web),
# không anon role. Token tự khai role khác thì SET ROLE thất bại (authenticator chỉ thuộc xt_web)
# và pre-request kiểm current_user = xt_web.
ROLE_CLAIM_KEY = os.environ.get("XT_IT_PGRST_ROLE_CLAIM_KEY", ".data_role")
ANON_ROLE = os.environ.get("XT_IT_PGRST_ANON_ROLE", "")
DEFAULT_SCHEMA = "xt_api_none"
MIN_AVAILABLE_GB = 4


def available_gb() -> int:
    """Cột `available` của `free -g` (GiB)."""
    out = subprocess.run(["free", "-g"], capture_output=True, text=True, check=True).stdout
    lines = out.splitlines()
    header = lines[0].split()
    mem = next(ln for ln in lines if ln.startswith("Mem:")).split()
    return int(mem[1 + header.index("available")])


def wait_for_memory(min_gb: int = MIN_AVAILABLE_GB) -> None:
    deadline = time.monotonic() + float(os.environ.get("XT_IT_MEM_WAIT_S", "600"))
    while True:
        have = available_gb()
        if have >= min_gb:
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"RAM available {have} GB < {min_gb} GB: không chạy stack docker")
        time.sleep(15)


def require_image(image: str) -> str:
    r = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"thiếu image {image} trên máy (không pull; kiểm `df -h /` trước)")
    return r.stdout.strip()


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ------------------------------------------------------------------------------ JWT ----
@dataclass
class Signer:
    kid: str
    key: rsa.RSAPrivateKey = field(repr=False)

    @classmethod
    def new(cls, kid: str | None = None) -> Signer:
        return cls(
            kid or f"it-{secrets.token_hex(6)}",
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
        )

    def jwk(self) -> dict[str, Any]:
        d: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        d.update(kid=self.kid, use="sig", alg="RS256")
        return d

    def sign(self, claims: Mapping[str, Any], **headers: Any) -> str:
        h: dict[str, Any] = {"kid": self.kid, "typ": "JWT", **headers}
        return jwt.encode(dict(claims), self.key, algorithm="RS256", headers=h)


_PATH_RE = re.compile(r'\.([A-Za-z_][A-Za-z0-9_]*)|\."([^"]+)"|\[(\d+)\]')


def put_claim(claims: dict[str, Any], path: str, value: Any) -> None:
    """Đặt claim theo JSPath kiểu PostgREST (`.a."b-c"[0]`)."""
    parts: list[str | int] = []
    pos = 0
    while pos < len(path):
        m = _PATH_RE.match(path, pos)
        if m is None:
            raise ValueError(f"JSPath không hỗ trợ: {path}")
        parts.append(int(m.group(3)) if m.group(3) else (m.group(1) or m.group(2)))
        pos = m.end()
    cur: Any = claims
    for here, nxt in zip(parts, [*parts[1:], None], strict=True):
        if nxt is None:
            if isinstance(here, int):
                while len(cur) <= here:
                    cur.append(None)
            cur[here] = value
        elif isinstance(here, int):
            while len(cur) <= here:
                cur.append(None)
            if cur[here] is None:
                cur[here] = [] if isinstance(nxt, int) else {}
            cur = cur[here]
        else:
            cur = cur.setdefault(here, [] if isinstance(nxt, int) else {})


def data_claims(org: Any, sub: str | None, **over: Any) -> dict[str, Any]:
    """Claim giống access token Keycloak cho client dữ liệu (`aud` ∋ xt-data). `over` giá trị
    None = bỏ claim đó."""
    now = int(time.time())
    c: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "azp": DATA_CLIENT,
        "typ": "Bearer",
        "iat": now - 5,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
        "scope": "openid xt-data",
        "realm_access": {"roles": ["default-roles-xt"]},
        "organization": org,
    }
    put_claim(c, ROLE_CLAIM_KEY, WEB_ROLE)
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


# --------------------------------------------------------------------------- HTTP ----
@dataclass
class Resp:
    status: int
    body: Any
    headers: Mapping[str, str]

    @property
    def rows(self) -> list[dict[str, Any]]:
        assert self.status == 200 and isinstance(self.body, list), (self.status, self.body)
        return self.body

    @property
    def code(self) -> str | None:
        return self.body.get("code") if isinstance(self.body, dict) else None


@dataclass
class Evidence:
    """Ghi lệnh curl tương đương + output THẬT (để docs/headless-data.md trích). Bật bằng
    `XT_IT_HEADLESS_EVIDENCE=<tệp>`; token chỉ hiện là `$TOKEN_<NHÃN>`."""

    path: Path | None

    def write(self, text: str) -> None:
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(text)

    def record(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        token_label: str | None,
        body: Any,
        resp: Resp,
    ) -> None:
        if self.path is None:
            return
        cmd = [f'curl -sS -i -X {method} "$XT_DATA_URL{path}"']
        if token_label:
            cmd.append(f'-H "Authorization: Bearer ${token_label}"')
        cmd += [f"-H '{k}: {v}'" for k, v in headers.items() if k.lower() != "authorization"]
        if body is not None:
            cmd.append(f"-d '{json.dumps(body, ensure_ascii=False)}'")
        out = json.dumps(resp.body, ensure_ascii=False)
        if len(out) > 600:
            out = out[:600] + "…"
        self.write(f"```console\n$ {' '.join(cmd)}\nHTTP {resp.status}\n{out}\n```\n\n")


class Rest:
    def __init__(self, base: str, evidence: Evidence) -> None:
        self.base = base
        self.client = httpx.Client(base_url=base, timeout=15.0)
        self.evidence = evidence

    def close(self) -> None:
        self.client.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        token_label: str | None = None,
        profile: str | None = None,
        content_profile: str | None = None,
        json_body: Any = None,
        headers: Mapping[str, str] | None = None,
        record: bool = False,
    ) -> Resp:
        h: dict[str, str] = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        if profile is not None:
            h["Accept-Profile"] = profile
        if content_profile is not None:
            h["Content-Profile"] = content_profile
        r = self.client.request(method, path, headers=h, json=json_body)
        try:
            body: Any = r.json() if r.content else None
        except ValueError:
            body = r.text
        resp = Resp(r.status_code, body, r.headers)
        if record:
            self.evidence.record(method, path, h, token_label, json_body, resp)
        return resp

    def get(self, path: str, **kw: Any) -> Resp:
        return self.call("GET", path, **kw)


# --------------------------------------------------------------------- containers ----
@dataclass
class Stack:
    name: str
    pg_port: int
    rest_port: int
    admin_port: int
    owner_password: str = field(repr=False)
    migrator_password: str = field(repr=False)
    app_password: str = field(repr=False)
    authenticator_password: str = field(repr=False)
    pepper: bytes = field(repr=False)
    signer: Signer
    image_ids: dict[str, str] = field(default_factory=dict)
    rest_started: bool = False
    jwks_dir: Path | None = field(default=None, repr=False)

    @property
    def pg_container(self) -> str:
        return f"{self.name}-pg"

    @property
    def rest_container(self) -> str:
        return f"{self.name}-rest"

    def dsn(self, user: str, password: str) -> str:
        return f"host=127.0.0.1 port={self.pg_port} dbname=xt user={user} password={password}"

    @property
    def app_dsn(self) -> str:
        return self.dsn("xt_app", self.app_password)

    @property
    def migrator_dsn(self) -> str:
        return self.dsn("xt_migrator", self.migrator_password)

    def su(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn("xt_owner", self.owner_password), autocommit=True)

    def migrator(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.migrator_dsn, autocommit=True)

    def connect(self, user: str, password: str) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn(user, password), autocommit=True)

    def jwks(self) -> str:
        return json.dumps({"keys": [self.signer.jwk()]})

    # ---- vòng đời
    def start_pg(self) -> None:
        wait_for_memory()
        subprocess.run(
            [
                "docker", "run", "-d", "--name", self.pg_container, "--label", LABEL,
                "--memory", "256m", "--cpus", "0.5",
                "-p", f"127.0.0.1:{self.pg_port}:5432",
                "-p", f"127.0.0.1:{self.rest_port}:3000",
                "-p", f"127.0.0.1:{self.admin_port}:3001",
                "-e", "POSTGRES_USER=xt_owner",
                "-e", f"POSTGRES_PASSWORD={self.owner_password}",
                "-e", "POSTGRES_DB=xt",
                PG_IMAGE,
            ],
            check=True,
            capture_output=True,
        )  # fmt: skip
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with self.su() as c:
                    c.execute("SELECT 1")
                return
            except psycopg.OperationalError:
                time.sleep(0.5)
        raise RuntimeError("Postgres không lên trong 60 s")

    def start_rest(self) -> None:
        """PostgREST chung netns với Postgres (DB ở 127.0.0.1:5432 trong container)."""
        wait_for_memory()
        self.jwks_dir = Path(tempfile.mkdtemp(prefix="xt-headless-jwks-"))
        self.jwks_dir.chmod(0o755)
        jwks_file = self.jwks_dir / "jwks.json"
        jwks_file.write_text(self.jwks(), encoding="utf-8")
        jwks_file.chmod(0o644)
        env = {
            "PGRST_DB_URI": (
                f"postgres://{AUTHENTICATOR}:{self.authenticator_password}@127.0.0.1:5432/xt"
            ),
            "PGRST_DB_CONFIG": "true",
            "PGRST_DB_PRE_CONFIG": "xt.pgrst_config",
            "PGRST_DB_PRE_REQUEST": "xt.pgrst_pre",
            # giá trị khởi đầu; db-pre-config phải ghi đè bằng danh sách tenant thật
            "PGRST_DB_SCHEMAS": DEFAULT_SCHEMA,
            "PGRST_DB_EXTRA_SEARCH_PATH": "",
            "PGRST_DB_POOL": "1",  # mọi request dùng chung MỘT kết nối: bắt rò ngữ cảnh
            "PGRST_DB_CHANNEL_ENABLED": "true",
            "PGRST_JWT_AUD": AUDIENCE,
            "PGRST_JWT_SECRET": "@/run/secrets/pgrst/jwks.json",
            "PGRST_JWT_ROLE_CLAIM_KEY": ROLE_CLAIM_KEY,
            "PGRST_OPENAPI_MODE": "disabled",
            "PGRST_SERVER_PORT": "3000",
            "PGRST_ADMIN_SERVER_PORT": "3001",
            "PGRST_LOG_LEVEL": "info",
        }
        if ANON_ROLE:
            env["PGRST_DB_ANON_ROLE"] = ANON_ROLE
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            self.rest_container,
            "--label",
            LABEL,
            "--memory",
            "128m",
            "--cpus",
            "0.5",
            "--user",
            "65534:65534",
            "--read-only",
        ]
        args += ["--mount", f"type=bind,source={self.jwks_dir},target=/run/secrets/pgrst,readonly"]
        args += ["--network", f"container:{self.pg_container}"]
        for k, v in env.items():
            args += ["-e", f"{k}={v}"]
        subprocess.run([*args, PGRST_IMAGE], check=True, capture_output=True)
        self.rest_started = True
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{self.admin_port}/ready", timeout=2)
                if r.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError("PostgREST không sẵn sàng trong 60 s:\n" + self.rest_logs())

    def rest_logs(self, tail: int = 80) -> str:
        r = subprocess.run(
            ["docker", "logs", "--tail", str(tail), self.rest_container],
            capture_output=True,
            text=True,
            check=False,
        )
        return r.stdout + r.stderr

    def stop(self) -> None:
        for c in (self.rest_container, self.pg_container):
            # postgres image tạo volume PGDATA ẩn danh; -v dọn cả volume của chính fixture.
            subprocess.run(["docker", "rm", "-f", "-v", c], capture_output=True, check=False)
        if self.jwks_dir is not None:
            shutil.rmtree(self.jwks_dir, ignore_errors=True)


def new_stack() -> Stack:
    """Kiểm image + RAM rồi khởi động Postgres (PostgREST bật sau khi seed qua `start_rest`)."""
    ids = {PG_IMAGE: require_image(PG_IMAGE), PGRST_IMAGE: require_image(PGRST_IMAGE)}
    wait_for_memory()
    st = Stack(
        name=f"xt-it-hd-{secrets.token_hex(4)}",
        pg_port=free_port(),
        rest_port=free_port(),
        admin_port=free_port(),
        owner_password=secrets.token_urlsafe(16),
        migrator_password=secrets.token_urlsafe(16),
        app_password=secrets.token_urlsafe(16),
        authenticator_password=secrets.token_urlsafe(16),
        pepper=secrets.token_bytes(32),
        signer=Signer.new(),
        image_ids=ids,
    )
    atexit.register(st.stop)
    try:
        st.start_pg()
    except BaseException:
        st.stop()
        raise
    return st


def wait_until(probe: Callable[[], Any], ok: Callable[[Any], bool], timeout: float = 20.0) -> Any:
    """Thăm dò tới khi `ok(probe())` (reload schema/config của PostgREST là bất đồng bộ)."""
    deadline = time.monotonic() + timeout
    last = probe()
    while not ok(last):
        if time.monotonic() > deadline:
            raise AssertionError(f"hết {timeout}s, kết quả cuối: {last!r}")
        time.sleep(0.25)
        last = probe()
    return last
