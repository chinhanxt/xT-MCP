# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Fixture dùng chung MỘT lần cho cả phiên `make test-integration` (đăng ký ở
`tests/integration/conftest.py`): Postgres 17 (container/cổng riêng, `docker rm -f -v`), IdP test,
cổng MCP, nền tenant acme/beta + tổ chức + gói (`stack_base`), hàng đợi thật (`worker`).

Không import các fixture này vào module/conftest khác: mỗi lần import là một định nghĩa riêng
(pytest dựng lại -> tạo tenant trùng, dừng nhầm container của nhau)."""

from __future__ import annotations

import concurrent.futures
import contextlib
import importlib
import io
import json
import re
import secrets
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp, start_idp
from tests.integration.composition.mcphttp import RunningApp, envelope, free_port
from xt.adapters.db.connection import Database
from xt.adapters.db.integration import AdminError, connect, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.adapters.db.tenancy import bootstrap, provision_tenant
from xt.domain.model.context import TenantRef
from xt.entrypoints import admin
from xt.entrypoints.serve import Prepared

COMPOSITION = "xt.entrypoints.composition"


@pytest.fixture(scope="session")
def pg() -> Iterator[pgfixture.Pg]:
    p = pgfixture.start()
    try:
        with p.su() as su:
            assert bootstrap(
                su,
                migrator_password=p.migrator_password,
                app_password=p.app_password,
                pepper=p.pepper,
            )
        yield p
    finally:
        pgfixture.stop()


@dataclass
class Tenants:
    a: TenantRef
    b: TenantRef


@pytest.fixture(scope="session")
def tenants(pg: pgfixture.Pg) -> Tenants:
    with pg.migrator() as m:
        return Tenants(
            provision_tenant(m, uuid.uuid4(), "Acme (A)"),
            provision_tenant(m, uuid.uuid4(), "Beta (B)"),
        )


@pytest.fixture(scope="session")
def app_db(pg: pgfixture.Pg) -> Iterator[Database]:
    db = Database(pg.app_dsn, max_size=4)
    yield db
    db.close()


@pytest.fixture(scope="session")
def mcp_port() -> int:
    return free_port()


@pytest.fixture(scope="session")
def idp(mcp_port: int) -> Iterator[LocalIdp]:
    i = start_idp(f"http://127.0.0.1:{mcp_port}/mcp")
    yield i
    i.close()


@pytest.fixture(scope="session")
def composition() -> ModuleType:
    """`xt.entrypoints.composition`. Chưa có thì bỏ qua test cần nó."""
    try:
        return importlib.import_module(COMPOSITION)
    except ModuleNotFoundError as e:
        if e.name and COMPOSITION.startswith(e.name):
            pytest.skip(f"{COMPOSITION} chưa có")
        raise


ROOT = Path(__file__).resolve().parents[3]
ORG = ROOT / "seed" / "binh-minh" / "org.json"
PACK = ROOT / "packages" / "de-xuat-phe-duyet"
# Tier `local` chỉ cho client confidential phía server (decisions.md "Tier PII của Desk chat");
# `xt-desk-agent` (public) luôn ở tier masked.
LOCAL_AGENT = "it-bff-confidential"
DESK_AGENT = "xt-desk-agent"  # client public của Desk chat: tier masked, kênh người /pii/reveal
PII_PACK = ROOT / "packages" / "kenh-pii"
# Nhân sự của tệp seed: người đề nghị (Kinh doanh), trưởng phòng Kinh doanh, giám đốc.
REQUESTER, DEPT_HEAD, DIRECTOR = "NV-0013", "NV-0010", "NV-0001"
IT_ADMIN = "NV-0031"  # vai trò `it`: người duyệt thứ hai của thay đổi model PHÁ HỦY
LINKED = (REQUESTER, DEPT_HEAD, DIRECTOR, IT_ADMIN)
MODEL_PACK = ROOT / "packages" / "quan-tri-mo-hinh"


def sub(alias: str, key: str) -> str:
    return f"kc-{alias}-{key}"


def _run_admin(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = admin.run(argv, env)
        except AdminError as e:  # như `xt` (admin.main): rc 2 + thông điệp
            return 2, str(e)
    return rc, buf.getvalue()


def run_admin(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    """`xt <lệnh quản trị>` trong thread riêng: lệnh dùng `asyncio.run` (bind-queue) chạy được cả
    khi fixture được dựng từ test async (đã có event loop ở thread hiện tại)."""

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        return ex.submit(_run_admin, argv, env).result()


@dataclass
class Stack:
    idp: LocalIdp
    app: RunningApp
    prepared: Prepared
    env: dict[str, str]
    tenants: dict[str, Any]
    outputs: dict[str, str]

    def token(self, alias: str, key: str, client: str = AGENT_CLIENT) -> str:
        return self.idp.access_token(sub(alias, key), org=alias, azp=client)

    async def call(
        self, alias: str, key: str, tool: str, args: dict[str, Any], client: str = AGENT_CLIENT
    ) -> dict[str, Any]:
        async with self.app.session(self.token(alias, key, client)) as s:
            return envelope(await s.call_tool(tool, args))


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    path.chmod(0o600)
    return str(path)


@dataclass
class StackBase:
    """Phần dùng chung cả phiên pytest: DB đã migrate, khóa, tenant acme/beta + tổ chức + gói.
    Dựng MỘT lần cho mọi module dùng `stack` (make test-integration chạy tất cả một lượt)."""

    env: dict[str, str]
    tenants: dict[str, Any]
    outputs: dict[str, str]


@pytest.fixture(scope="session")
def stack_base(
    pg: pgfixture.Pg, idp: LocalIdp, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> StackBase:
    d = tmp_path_factory.mktemp("xt-e2e")
    with pg.migrator() as m:
        migrate(m)
    receipt_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    base = f"http://127.0.0.1:{mcp_port}"
    env = {
        "XT_DB_APP_URL": pg.app_dsn,
        "XT_DB_MIGRATOR_URL": pg.migrator_dsn,
        "XT_AUDIT_KEY_FILE": _write(d / "audit.key", pg.pepper),
        "XT_AUDIT_KEY_ID": "1",
        "XT_CURSOR_KEY_FILE": _write(d / "cursor.key", secrets.token_hex(32).encode()),
        "XT_PII_KEY_FILE": _write(d / "pii.key", secrets.token_hex(32).encode()),
        "XT_DESK_URL": base,
        "XT_CONFIRM_AUTHORIZATION_URL": f"{idp.base}/auth",
        "XT_CONFIRM_TOKEN_URL": f"{idp.base}/token",
        "XT_CONFIRM_CLIENT_ID": DESK_CLIENT,
        "XT_CONFIRM_ACR": "urn:xt:acr:otp",
        "XT_CONFIRM_REQUIRED_AMR": "otp",
        "XT_CONFIRM_MAX_AUTH_AGE_SECONDS": "300",
        "XT_RECEIPT_ISSUER": f"{base}/desk",
        "XT_RECEIPT_SIGNING_KEY_FILE": _write(d / "receipt.pem", receipt_key),
        "XT_IDP_ISSUER": idp.issuer,
        "XT_IDP_JWKS_URL": idp.jwks_url,
        "XT_IDP_ALLOW_HTTP": "1",
        "XT_IDP_INTROSPECTION": "off",
        "XT_MCP_RESOURCE": f"{base}/mcp",
        "XT_MCP_ALLOWED_CLIENTS": f"{AGENT_CLIENT},{LOCAL_AGENT},{DESK_AGENT}",
        "XT_PII_REVEAL_ORIGINS": base,
        "XT_PII_REVEAL_CLIENTS": DESK_AGENT,
        "XT_PII_REVEAL_TICKETS_PER_MINUTE": "100",
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_LOCAL_CLIENTS": LOCAL_AGENT,
        "XT_MCP_CONFIDENTIAL_CLIENTS": LOCAL_AGENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
        "XT_DPOP_REPLAY_STORE": "postgres",  # cache jti DPoP chung (DPA-02); bộ nhớ chỉ ở unit
    }
    outputs: dict[str, str] = {}
    tenants: dict[str, Any] = {}
    for alias, name in (("acme", "Bình Minh (A)"), ("beta", "Beta (B)")):
        rc, out = run_admin(
            [
                "tenant", "create", "--alias", alias, "--name", name,
                "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked",
            ],
            env,
        )  # fmt: skip
        assert rc == 0, out
        links = {
            "subjects": {k: sub(alias, k) for k in LINKED},
            "agent_grants": [
                {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
                for k in LINKED
                for c in (AGENT_CLIENT, LOCAL_AGENT, DESK_AGENT)
            ],
        }
        lf = d / f"links-{alias}.json"
        lf.write_text(json.dumps(links), encoding="utf-8")
        rc, out = run_admin(
            ["org", "import", "--tenant", alias, str(ORG), "--links", str(lf),
             "--base-roles", "employee"],
            env,
        )  # fmt: skip
        assert rc == 0, out
        outputs[f"org-{alias}"] = out
        common = ["pack", "install", "--tenant", alias, str(PACK)]
        who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, bad = run_admin([*common, *who, "--confirm", "sha256:" + "0" * 64], env)
        assert rc == 2 and "mã xác nhận không khớp" in bad
        rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, out
        outputs[f"plan-{alias}"] = plan
        outputs[f"install-{alias}"] = out
        for extra_pack in (MODEL_PACK, PII_PACK):
            mp = ["pack", "install", "--tenant", alias, str(extra_pack)]
            rc, mplan = run_admin([*mp, *who], env)
            assert rc == 0, mplan
            mdigest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", mplan)
            assert mdigest, mplan
            rc, out = run_admin([*mp, *who, "--confirm", mdigest.group(1)], env)
            assert rc == 0, out
        with connect(pg.migrator_dsn) as c:
            tenants[alias] = tenant_of_alias(c, alias)
    return StackBase(env, tenants, outputs)


def _url(role: str, password: str) -> str:
    return f"postgresql://{role}:{password}@127.0.0.1:{pgfixture.PORT}/xt"


@dataclass
class Worker:
    env: dict[str, str]
    xt_bin: str


@pytest.fixture(scope="session")
def worker(stack_base: StackBase, pg: pgfixture.Pg) -> Worker:
    """Hàng đợi thật: role xt_control/xt_job, `xt worker --install-schema`, `xt tenant
    bind-queue` (job role + chuỗi relay) cho tenant acme."""
    import subprocess
    import sys

    from psycopg import sql

    control_pw = secrets.token_urlsafe(16)
    with pg.su() as su:
        if not su.execute("SELECT 1 FROM pg_roles WHERE rolname='xt_control'").fetchone():
            su.execute(
                sql.SQL("CREATE ROLE xt_control LOGIN NOBYPASSRLS PASSWORD {}").format(
                    sql.Literal(control_pw)
                )
            )
            su.execute("GRANT CONNECT ON DATABASE xt TO xt_control")
        else:
            su.execute(sql.SQL("ALTER ROLE xt_control PASSWORD {}").format(sql.Literal(control_pw)))
    owner = _url("xt_owner", pg.owner_password)
    wenv = {
        **stack_base.env,
        "XT_WORKER_DATABASE_URL": _url("xt_app", pg.app_password),
        "XT_QUEUE_DATABASE_URL": _url("xt_control", control_pw),
        "XT_QUEUE_MIGRATOR_DATABASE_URL": owner,
        "XT_DB_OWNER_URL": owner,
        "XT_WORKER_CONCURRENCY": "1",
        "XT_WORKER_STALLED_SECONDS": "6",
        "XT_WORKER_RECOVER_SECONDS": "1",
        "XT_WORKER_MAX_ATTEMPTS": "2",
        "XT_WORKER_RETRY_SECONDS": "1",
        "XT_WORKER_HANDLERS": "xt.entrypoints.composition.worker:handlers",
        "XT_WORKFLOW_RELAY_SECONDS": "60",
    }
    xt_bin = str(Path(sys.executable).with_name("xt"))
    inst = subprocess.run(
        [xt_bin, "worker", "--install-schema"], env=wenv, capture_output=True, timeout=120
    )
    assert inst.returncode == 0, inst.stderr.decode()
    rc, out = run_admin(["tenant", "bind-queue", "--tenant", "acme"], wenv)
    assert rc == 0, out
    stack_base.outputs["bind-queue"] = out
    return Worker(wenv, xt_bin)
