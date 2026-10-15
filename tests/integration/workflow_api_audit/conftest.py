# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit REST /workflow/v1: Postgres 17 thật (1 container `xt-it-composition-pg-<hex>`, `--rm`),
composition thật, uvicorn loopback, token ký bằng khóa test qua JWKS tĩnh.

Fixture giống `tests/integration/composition/test_workflow_api.py::wstack`, thêm
`XT_PII_REVEAL_ORIGINS`/`XT_PII_REVEAL_CLIENTS` (composition mới bắt buộc) và thêm người thứ tư
(`OTHER`, không phải người duyệt) để thử SoD/inbox.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import serve
from tests.integration.composition.test_e2e import (
    DEPT_HEAD,
    DIRECTOR,
    ORG,
    PACK,
    REQUESTER,
    Stack,
    _write,
    run_admin,
    sub,
)
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.entrypoints.serve import prepare
from xt.entrypoints.workflow_http import with_workflow_api

A, B = "wxa", "wxb"
OTHER = "NV-0005"  # kế toán, khác phòng: không có trong tuyến duyệt của đề nghị Kinh doanh
OTHER_HEAD = (
    "NV-0004"  # trưởng phòng Kế toán: dept_head nhưng không phải trưởng phòng của requester
)
PEOPLE = (REQUESTER, DEPT_HEAD, DIRECTOR, OTHER, OTHER_HEAD)


def stack_env(pg: pgfixture.Pg, idp: LocalIdp, mcp_port: int, d: Any) -> dict[str, str]:
    receipt_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    base = f"http://127.0.0.1:{mcp_port}"
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
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
        "XT_WORKFLOW_PUBLIC_URL": base,
        "XT_PII_REVEAL_ORIGINS": base,
        "XT_PII_REVEAL_CLIENTS": "xt-desk-agent",
        "XT_PII_REVEAL_TICKETS_PER_MINUTE": "60",
    }


@contextlib.contextmanager
def build_stack(
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    d: Path,
    aliases: tuple[str, str] = (A, B),
    people: tuple[str, ...] = PEOPLE,
    autonomy: int = 3,
) -> Iterator[Stack]:
    with pg.migrator() as m:
        migrate(m)
    env = stack_env(pg, idp, mcp_port, d)
    tenants: dict[str, Any] = {}
    for alias in aliases:
        rc, out = run_admin(
            [
                "tenant", "create", "--alias", alias, "--name", f"Audit {alias}",
                "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked",
            ],
            env,
        )  # fmt: skip
        assert rc == 0, out
        links = {
            "subjects": {k: sub(alias, k) for k in people},
            "agent_grants": [
                {"person": k, "client_id": AGENT_CLIENT, "autonomy": autonomy, "roles": ["agent"]}
                for k in people
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
        common = ["pack", "install", "--tenant", alias, str(PACK)]
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
    app = serve(with_workflow_api(prepared, env, prepared.http_app()), mcp_port)
    try:
        yield Stack(idp, app, prepared, env, tenants, {})
    finally:
        app.stop()
        prepared.close()


@pytest.fixture(scope="session")  # một stack cho cả thư mục (tenant wxa/wxb chỉ tạo một lần)
def wstack(
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Stack]:
    with build_stack(pg, idp, mcp_port, tmp_path_factory.mktemp("xt-wf-audit")) as s:
        yield s
