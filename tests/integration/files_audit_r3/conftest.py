# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit vòng 3 REST /files/v1 (docs/audit-files.md "Vòng 3"): kiểm chứng sâu các bản sửa vòng 2.

MỘT Postgres 17 phiên (`pg`, dùng chung) + MỘT SeaweedFS 4.48 riêng của module (`sw`, cổng loopback
ngẫu nhiên, tmpfs, `docker rm -f -v`). Hai tầng chạy app:
- `r3stack`: composition thật + uvicorn loopback (như vòng 2) nhưng QUOTA nhỏ để ép tranh chấp
  quota giữa 2 người cùng tenant, và `files.maintain` gọi trực tiếp cho đua purge/finalize.
- `r3worker`: hàng đợi thật (role xt_control) + tiến trình `xt worker` thật để chứng minh
  `files.maintain` chạy QUA đường worker (không gọi hàm trực tiếp).

Không sửa `src/` hay `deploy/`.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration import seaweed
from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import (
    DEPT_HEAD,
    DIRECTOR,
    ORG,
    REQUESTER,
    ROOT,
    Stack,
    Worker,
    _write,
    run_admin,
    sub,
)
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import serve
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.entrypoints.files_http import with_files_api
from xt.entrypoints.serve import prepare
from xt.entrypoints.workflow_http import with_workflow_api

A = "fza"
COLLEAGUE = "NV-0014"  # cùng phòng REQUESTER
KEYS = (REQUESTER, DEPT_HEAD, DIRECTOR, COLLEAGUE)
FILES_PACK = ROOT / "packages" / "tep-dinh-kem"

# Quota nhỏ để ép tranh chấp: mỗi người 256 KiB, cả tenant 384 KiB, tệp tối đa 64 KiB.
MAX = 64 * 1024
USER_Q = 256 * 1024
TENANT_Q = 384 * 1024
PENDING = 64
TTL = 30


def _files_env(d: Path, pg: pgfixture.Pg, idp: LocalIdp, base: str, sw: seaweed.Seaweed) -> dict:
    receipt_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return {
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
        "XT_MCP_ALLOWED_CLIENTS": AGENT_CLIENT,
        "XT_PII_REVEAL_ORIGINS": base,
        "XT_PII_REVEAL_CLIENTS": AGENT_CLIENT,
        "XT_PII_REVEAL_TICKETS_PER_MINUTE": "60",
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
        "XT_MCP_CAPABILITIES": "upload_file",
        "XT_WORKFLOW_PUBLIC_URL": base,
        "XT_FILES_S3_ENDPOINT": sw.endpoint,
        "XT_FILES_S3_BUCKET": f"xt-audit3-{secrets.token_hex(3)}",
        "XT_FILES_S3_ACCESS_KEY": sw.access_key,
        "XT_FILES_S3_SECRET_KEY": sw.secret_key,
        "XT_FILES_PUBLIC_URL": base,
        "XT_FILES_MAX_BYTES": str(MAX),
        "XT_FILES_UPLOAD_TTL_SECONDS": str(TTL),
        "XT_FILES_MAX_PENDING_PER_USER": str(PENDING),
        "XT_FILES_USER_QUOTA_BYTES": str(USER_Q),
        "XT_FILES_TENANT_QUOTA_BYTES": str(TENANT_Q),
        "XT_FILES_RETENTION_SECONDS": "0",
        "XT_FILES_UNSCANNED_DOWNLOAD": "allow",
    }


def _provision(env: dict, pg: pgfixture.Pg, d: Path) -> dict[str, Any]:
    rc, out = run_admin(
        ["tenant", "create", "--alias", A, "--name", "Files audit R3",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    links = {
        "subjects": {k: sub(A, k) for k in KEYS},
        "agent_grants": [
            {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
            for k in KEYS
        ],
    }
    lf = d / "links.json"
    lf.write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", A, str(ORG), "--links", str(lf), "--base-roles", "employee"],
        env,
    )
    assert rc == 0, out
    common = ["pack", "install", "--tenant", A, str(FILES_PACK)]
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    rc, plan = run_admin([*common, *who], env)
    assert rc == 0, plan
    digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
    assert digest, plan
    rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], env)
    assert rc == 0, out
    with connect(pg.migrator_dsn) as c:
        return {A: tenant_of_alias(c, A)}


@pytest.fixture(scope="module")
def sw() -> Iterator[seaweed.Seaweed]:
    s = seaweed.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="module")
def r3stack(
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    sw: seaweed.Seaweed,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Stack]:
    d = tmp_path_factory.mktemp("xt-files-audit-r3")
    with pg.migrator() as m:
        migrate(m)
    base = f"http://127.0.0.1:{mcp_port}"
    env = _files_env(d, pg, idp, base, sw)
    tenants = _provision(env, pg, d)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    app = with_files_api(prepared, env, with_workflow_api(prepared, env, prepared.http_app()))
    running = serve(app, mcp_port)
    try:
        yield Stack(idp, running, prepared, env, tenants, {"seaweed": sw.name})
    finally:
        running.stop()
        prepared.close()


@pytest.fixture(scope="module")
def r3worker(r3stack: Stack, worker: Worker) -> Worker:
    """Hàng đợi thật + env cho tiến trình `xt worker` (handlers composition), cùng Postgres/
    SeaweedFS của `r3stack`. Dùng lại schema hàng đợi + role xt_control của fixture phiên `worker`
    (cài lại schema trên cùng Postgres -> DuplicateObject; đổi mật khẩu xt_control làm hỏng
    `worker`). Test tự `bind-queue` rồi chạy `xt worker` để chứng minh files.maintain qua worker."""
    queue_keys = (
        "XT_WORKER_DATABASE_URL",
        "XT_QUEUE_DATABASE_URL",
        "XT_QUEUE_MIGRATOR_DATABASE_URL",
        "XT_DB_OWNER_URL",
        "XT_WORKER_CONCURRENCY",
        "XT_WORKER_STALLED_SECONDS",
        "XT_WORKER_RECOVER_SECONDS",
        "XT_WORKER_MAX_ATTEMPTS",
        "XT_WORKER_RETRY_SECONDS",
        "XT_WORKER_HANDLERS",
        "XT_WORKFLOW_RELAY_SECONDS",
    )
    wenv = {**r3stack.env, **{k: worker.env[k] for k in queue_keys}}
    return Worker(wenv, worker.xt_bin)
