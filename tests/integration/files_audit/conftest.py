# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit REST /files/v1 (docs/audit-files.md): Postgres 17 thật (fixture phiên `pg`) + MỘT
SeaweedFS 4.48 (`xt-it-seaweed-<hex>`, cổng loopback ngẫu nhiên, tmpfs, `docker rm -f -v`),
composition thật, uvicorn loopback, token ký bằng khóa test qua JWKS tĩnh.

Giống `tests/integration/composition/test_files_api.py::fstack` nhưng tenant riêng (`fxa`, `fxb`),
CẢ HAI tenant cài gói `tep-dinh-kem` (để thử tham chiếu chéo tenant bằng tệp có thật).
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
from collections.abc import Iterator
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

A, B = "fxa", "fxb"
COLLEAGUE = "NV-0014"  # cùng phòng với REQUESTER, chỉ employee
FILES_PACK = ROOT / "packages" / "tep-dinh-kem"
TTL = 10
MAX = 256 * 1024


@pytest.fixture(scope="module")
def sw() -> Iterator[seaweed.Seaweed]:
    s = seaweed.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="module")
def xstack(
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    sw: seaweed.Seaweed,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Stack]:
    d = tmp_path_factory.mktemp("xt-files-audit")
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
        "XT_FILES_S3_BUCKET": f"xt-audit-{secrets.token_hex(3)}",
        "XT_FILES_S3_ACCESS_KEY": sw.access_key,
        "XT_FILES_S3_SECRET_KEY": sw.secret_key,
        "XT_FILES_PUBLIC_URL": base,
        "XT_FILES_MAX_BYTES": str(MAX),
        "XT_FILES_UPLOAD_TTL_SECONDS": str(TTL),
        "XT_FILES_MAX_PENDING_PER_USER": "40",
    }
    tenants: dict[str, Any] = {}
    for alias, name in ((A, "Files audit A"), (B, "Files audit B")):
        rc, out = run_admin(
            [
                "tenant", "create", "--alias", alias, "--name", name,
                "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked",
            ],
            env,
        )  # fmt: skip
        assert rc == 0, out
        keys = (REQUESTER, DEPT_HEAD, DIRECTOR, COLLEAGUE)
        links = {
            "subjects": {k: sub(alias, k) for k in keys},
            "agent_grants": [
                {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
                for k in keys
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
        common = ["pack", "install", "--tenant", alias, str(FILES_PACK)]
        who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, out
        with connect(pg.migrator_dsn) as c:
            tenants[alias] = tenant_of_alias(c, alias)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    app = with_files_api(prepared, env, with_workflow_api(prepared, env, prepared.http_app()))
    running = serve(app, mcp_port)
    try:
        yield Stack(idp, running, prepared, env, tenants, {"seaweed": sw.name})
    finally:
        running.stop()
        prepared.close()
