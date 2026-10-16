# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm chứng hạ tầng test: Postgres thật, JWKS HTTP thật, token ký thật qua verifier của sản phẩm,
RLS hai tenant, và MCP client qua HTTP thật (port giả của tests/unit, verifier thật)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from tests.integration.composition.conftest import Tenants
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import RunningApp, envelope, serve
from xt.adapters.db.connection import Database
from xt.adapters.identity.step_up import ConfirmationContext
from xt.domain.identity import InvalidTokenError, PrincipalKind, StepUpRejectedError

pytestmark = pytest.mark.integration


# ---- Postgres + RLS ---------------------------------------------------------------------------
def test_pg_roles_and_two_tenants(pg: Any, tenants: Tenants) -> None:
    assert tenants.a.id != tenants.b.id and tenants.a.seq != tenants.b.seq
    with pg.app() as c:
        row = c.execute("SELECT current_user, version()").fetchone()
        assert row is not None and row[0] == "xt_app" and "PostgreSQL 17" in row[1]


def test_rls_two_tenants_fail_closed(pg: Any, tenants: Tenants) -> None:
    a, b = tenants.a, tenants.b
    with pg.migrator() as m:
        for t in (a, b):
            m.execute(
                # mọi bảng thường trong xt_t<seq> tự có trigger audit (R3-01) => cần id uuid
                f"CREATE TABLE IF NOT EXISTS xt_t{t.seq}.it_note"
                " (id uuid NOT NULL DEFAULT gen_random_uuid(), tenant_id uuid, t text)"
            )
            m.execute(f"GRANT SELECT, INSERT ON xt_t{t.seq}.it_note TO xt_app")
            m.execute(f"ALTER TABLE xt_t{t.seq}.it_note ENABLE ROW LEVEL SECURITY")
            m.execute(f"ALTER TABLE xt_t{t.seq}.it_note FORCE ROW LEVEL SECURITY")
            m.execute(f"DROP POLICY IF EXISTS p ON xt_t{t.seq}.it_note")
            m.execute(
                f"CREATE POLICY p ON xt_t{t.seq}.it_note USING"
                " (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid)"
                " WITH CHECK (tenant_id = nullif(current_setting('xt.tenant_id', true), '')::uuid)"
            )
    with pg.app() as c:
        for t in (a, b):
            with c.transaction():
                c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(t.id),))
                c.execute(
                    f"INSERT INTO xt_t{t.seq}.it_note (tenant_id, t) VALUES (%s, 'x')", (t.id,)
                )
        with c.transaction():  # không đặt tenant -> 0 dòng
            assert c.execute(f"SELECT count(*) FROM xt_t{a.seq}.it_note").fetchone() == (0,)
        with c.transaction():
            c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(a.id),))
            assert c.execute(f"SELECT count(*) FROM xt_t{a.seq}.it_note").fetchone() == (1,)
            assert c.execute(f"SELECT count(*) FROM xt_t{b.seq}.it_note").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(a.id),))
            c.execute(f"INSERT INTO xt_t{a.seq}.it_note (tenant_id, t) VALUES (%s, 'x')", (b.id,))


def test_app_db_pool(app_db: Database) -> None:
    with app_db.connection() as c:
        assert c.execute("SELECT 1").fetchone() == (1,)


# ---- IdP cục bộ + verifier thật ----------------------------------------------------------------
def test_access_token_verified_via_http_jwks(idp: LocalIdp) -> None:
    v = idp.access_verifier()
    p = v.verify(idp.access_token("sub-mai", org="acme"))
    assert p.kind is PrincipalKind.AGENT and p.client_id == AGENT_CLIENT
    assert p.organizations == ("acme",) and p.on_behalf_of == "sub-mai"
    assert len(idp.fetches) >= 1


def test_access_token_rejections(idp: LocalIdp) -> None:
    v = idp.access_verifier()
    cases = {
        "client_not_allowed": idp.access_token("s", org="acme", azp="intruder"),
        "audience": idp.access_token("s", org="acme", aud=["https://other/mcp"]),
        "expired": idp.access_token(
            "s", org="acme", iat=int(time.time()) - 900, exp=int(time.time()) - 600
        ),
    }
    for reason, tok in cases.items():
        with pytest.raises(InvalidTokenError) as ex:
            v.verify(tok)
        assert ex.value.reason == reason
    with pytest.raises(InvalidTokenError):
        v.verify(idp.access_token("s", org="acme")[:-4] + "AAAA")


def ctx(
    nonce: str, requester: str = "sub-mai", confirmer: str | None = "sub-viet"
) -> ConfirmationContext:
    return ConfirmationContext(
        "cf-1", nonce, int(time.time()) - 30, requester, confirmer_subject=confirmer
    )


def test_step_up_real_id_token_single_use(idp: LocalIdp) -> None:
    v = idp.step_up_verifier()
    tok = idp.id_token("sub-viet", nonce="n1")
    ev = v.verify(tok, ctx("n1"))
    assert ev.acr == "urn:xt:acr:otp" and ev.client_id == DESK_CLIENT
    with pytest.raises(StepUpRejectedError):  # jti đã tiêu
        v.verify(tok, ctx("n1"))
    with pytest.raises(StepUpRejectedError):  # SoD: người đề nghị = người duyệt
        v.verify(idp.id_token("sub-mai", nonce="n2"), ctx("n2", confirmer=None))
    with pytest.raises(StepUpRejectedError):  # thiếu OTP
        v.verify(idp.id_token("sub-viet", nonce="n3", amr=("pwd",)), ctx("n3"))


# ---- MCP client qua HTTP thật, verifier thật, port giả ------------------------------------------
@pytest.fixture
async def running(idp: LocalIdp, mcp_port: int) -> AsyncIterator[RunningApp]:
    from tests.unit.tools.fakes import make_dispatcher, make_world
    from xt.adapters.mcp import ClientClassifier, XtMcpServer, build_http_app, http_caller
    from xt.contracts.prompt_catalog import default_prompts
    from xt.contracts.tools import Profile
    from xt.entrypoints.serve import IdentityHttpAuth

    world = make_world()
    verifier = idp.access_verifier()
    server = XtMcpServer(
        make_dispatcher(world),
        http_caller,
        prompts=default_prompts(),
        profile=Profile.FULL,
        classify=ClientClassifier(frozenset()),
        ensure_active=lambda p, t, level: None,
    )
    app = build_http_app(
        server,
        IdentityHttpAuth(idp.rs_settings(), verifier),
        mcp_path="/mcp",
        allowed_hosts=[f"127.0.0.1:{mcp_port}"],
    )
    r = serve(app, mcp_port)
    try:
        yield r
    finally:
        r.stop()


@pytest.mark.anyio
async def test_mcp_http_real_token(running: RunningApp, idp: LocalIdp) -> None:
    import httpx

    r = httpx.get(running.mcp_url)
    assert r.status_code == 401 and "resource_metadata" in r.headers["www-authenticate"]
    prm = httpx.get(f"{running.base}/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == idp.resource
    async with running.session(idp.access_token("sub-mai", org="acme")) as s:
        tools = await s.list_tools()
        assert len(tools.tools) >= 10
        res = await s.call_tool("inspect_workspace", {})
        assert "ok" in envelope(res)
