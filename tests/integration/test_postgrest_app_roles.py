# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Exercise PostgREST's SQL GET transaction on one real PostgreSQL container."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from tests.integration import pg as pgmod
from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.data_setup import install_data_prerequisites
from xt.adapters.db.integration import register_tenant
from xt.adapters.db.migrations import LEDGER, available, migrate
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bootstrap, install_postgrest_role, postgrest_sql
from xt.application.model_engine.service import RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.spec import Catalog

pytestmark = pytest.mark.integration
MIGRATION = "0019_postgrest_app_roles.sql"
ISSUER = "https://idp.example/realms/xt"


@pytest.fixture
def pg() -> Iterator[pgmod.Pg]:
    try:
        yield pgmod.start()
    finally:
        pgmod.stop()  # docker rm -f -v, including failed setup


def test_postgrest_effective_roles_and_disabled_pack_rows(pg: pgmod.Pg) -> None:
    password = secrets.token_urlsafe(18)
    alice, bob, approver = uuid4(), uuid4(), uuid4()
    key = AuditKey(1, pg.pepper)
    migrator = PgMigrator(pg.migrator, audit_key=key)
    tenants: dict[str, TenantRef] = {}
    cat = Catalog.model_validate(
        {
            "version": 1,
            "models": [
                {
                    "id": name,
                    "name": name,
                    "label": name,
                    "fields": [
                        {"id": "f1", "name": "title", "type": "text", "label": "Title"},
                        {
                            "id": "f2",
                            "name": "secret",
                            "type": "text",
                            "label": "Secret",
                            "pii": "sensitive",
                        },
                    ],
                    "permissions": {
                        role: {"actions": ["read", "create"], "scope": "all"}
                        for role in (
                            ("employee", "reader", "admin")
                            if name == "memo"
                            else ("reader", "admin")
                        )
                    },
                }
                for name in ("lead", "memo")
            ],
        }
    )
    with pg.su() as su:
        bootstrap(
            su,
            migrator_password=pg.migrator_password,
            app_password=pg.app_password,
            pepper=pg.pepper,
        )
        # An existing deployment: apply only 0001..0018, then build legacy views/gate.
        with su.transaction():
            su.execute("SET LOCAL ROLE xt_migrator")
            su.execute(LEDGER)
            for name, body in available():
                if name < MIGRATION:
                    su.execute(body.encode())
                    su.execute("INSERT INTO xt_meta.schema_migration (name) VALUES (%s)", (name,))
        with pg.migrator() as m:
            for alias in ("acme", "other"):
                t = register_tenant(m, alias=alias, name=alias, timezone="UTC", llm_egress="masked")
                tenants[alias] = t
                for pid, person, roles in (
                    (alice, "alice", ["employee"]),
                    (bob, "bob", []),
                    (approver, "approver", ["admin"]),
                ):
                    m.execute(
                        "INSERT INTO xt_meta.person (tenant_id,id,key,name,subject,roles)"
                        " VALUES (%s,%s,%s,%s,%s,%s)",
                        (t.id, pid, person, person, person, roles),
                    )
                plan = migrator.plan(t, cat, rehearse=False)
                migrator.apply(
                    t, cat, plan.hash, set(), proposed_by="builder", approved_by="reviewer"
                )
                stats = migrator.run_jobs(t.seq)
                assert stats.failed == stats.dead == 0 and not stats.pending_versions
        app = Database(pg.app_dsn, audit_key=key)
        try:
            records = RecordService(PgCatalogReader(app), PgRecordRepository(app))
            for t in tenants.values():
                for model in ("lead", "memo"):
                    records.create(ModelActor(t, approver, ("admin",)), model, {"title": model})
        finally:
            app.close()
        t = tenants["acme"]
        view = f"api_t{t.seq}.lead"
        before = su.execute(
            "SELECT oid, relowner, relacl FROM pg_class WHERE oid=%s::regclass", (view,)
        ).fetchone()
        assert before is not None
        for tenant in tenants.values():
            for model in ("lead", "memo"):
                columns = su.execute(
                    "SELECT string_agg(quote_ident(attname), ', ' ORDER BY attnum)"
                    " FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0"
                    " AND NOT attisdropped",
                    (f"api_t{tenant.seq}.{model}",),
                ).fetchone()
                assert columns is not None
                su.execute(
                    sql.SQL("CREATE OR REPLACE VIEW {} AS SELECT {} FROM {}").format(
                        sql.Identifier(f"api_t{tenant.seq}", model),
                        sql.SQL(columns[0]),
                        sql.Identifier(f"xt_t{tenant.seq}", model),
                    )
                )
        source = postgrest_sql()
        gate = source[
            source.index("CREATE OR REPLACE FUNCTION xt.pgrst_gate(") : source.index(
                "-- Wrapper SECURITY DEFINER"
            )
        ]
        role_start = gate.index("    SELECT pe.id,")
        role_end = gate.index("      FROM xt_meta.person pe", role_start)
        legacy = (
            gate[:role_start] + "    SELECT pe.id, pe.roles, pe.unit INTO p\n" + gate[role_end:]
        )
        disabled_start = legacy.index("    -- Lọc ở từng view")
        disabled_end = legacy.index("    'd', CASE", disabled_start)
        legacy = legacy[:disabled_start] + legacy[disabled_end:]
        su.execute(legacy.encode())
        with pg.migrator() as m, pytest.raises(psycopg.errors.InsufficientPrivilege):
            migrate(m)  # Audit protection must still reject an ordinary login changing the gate.
        assert install_data_prerequisites(su) == [
            name for name, _ in available() if name >= MIGRATION
        ]
        upgraded = su.execute("SELECT pg_get_viewdef(%s::regclass)", (view,)).fetchone()
        assert upgraded is not None and "disabled_models" in upgraded[0]
        assert (
            su.execute(
                "SELECT oid, relowner, relacl FROM pg_class WHERE oid=%s::regclass", (view,)
            ).fetchone()
            == before
        )
        with pg.migrator() as m, m.transaction():
            m.execute(dict(available())[MIGRATION].encode())  # Exact replay is idempotent.
            assert m.execute("SELECT current_user").fetchone() == ("xt_migrator",)
        install_postgrest_role(
            su, authenticator_password=password, issuer=ISSUER, clients=["xt-cli"]
        )
        with pg.migrator() as m:
            assert migrate(m) == []
        # Future compiled views must have the same gate as views upgraded by 0019.
        future = cat.dump()
        future["version"] = 2
        future["models"].append({**future["models"][0], "id": "future", "name": "future"})
        new = Catalog.model_validate(future)
        plan = migrator.plan(t, new, rehearse=False)
        migrator.apply(t, new, plan.hash, set(), proposed_by="builder", approved_by="reviewer")
        stats = migrator.run_jobs(t.seq)
        assert stats.failed == stats.dead == 0 and not stats.pending_versions
        app = Database(pg.app_dsn, audit_key=key)
        try:
            RecordService(PgCatalogReader(app), PgRecordRepository(app)).create(
                ModelActor(t, approver, ("admin",)), "future", {"title": "future"}
            )
        finally:
            app.close()

        def grant(tenant: TenantRef, person: Any, role: str, ref: str) -> None:
            su.execute(
                "INSERT INTO xt_meta.person_role_grant"
                " (tenant_id,grant_ref,person,role,requested_by,approved_by)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (tenant.id, ref, person, role, alice, approver),
            )

        def read(
            alias: str = "acme",
            subject: str = "alice",
            *,
            pre: bool = True,
            method: str = "GET",
            model: str = "lead",
        ) -> tuple[Any, ...]:
            tenant = tenants[alias]
            profile = f"api_t{tenant.seq}"
            claims = {
                "typ": "Bearer",
                "aud": "xt-data",
                "iss": ISSUER,
                "azp": "xt-cli",
                "organization": alias,
                "sub": subject,
                "exp": time.time() + 300,
                "roles": ["admin", "reader"],
            }  # Untrusted JWT roles cannot grant access.
            with psycopg.connect(f"{pgmod.DSN} user=xt_authenticator password={password}") as c:
                c.execute("SET TRANSACTION READ ONLY")
                c.execute("SET LOCAL ROLE xt_web")
                for name, value in (
                    ("request.jwt.claims", json.dumps(claims)),
                    ("request.headers", json.dumps({"accept-profile": profile})),
                    ("request.method", method),
                    ("request.path", "/memo"),
                    ("search_path", profile),
                ):
                    c.execute("SELECT set_config(%s,%s,true)", (name, value))
                if pre:
                    c.execute("SELECT xt.pgrst_pre()")
                # A disabled relation nested under an enabled resource still returns zero rows.
                row = c.execute(
                    sql.SQL(
                        "SELECT (SELECT count(*) FROM {}),"
                        " (SELECT count(*) FROM {}.memo),"
                        " (SELECT roles FROM xt.cur())"
                    ).format(sql.Identifier(profile, model), sql.Identifier(profile))
                ).fetchone()
                assert row is not None
                return row

        assert read(pre=False)[:2] == (0, 0)
        grant(tenants["other"], alice, "reader", "other-reader")
        grant(t, bob, "reader", "bob-reader")
        assert read() == (0, 1, ["employee"])
        assert read("other")[:2] == (1, 1)
        grant(t, alice, "reader", "reader")
        grant(t, alice, "employee", "duplicate")
        assert read() == (1, 1, ["employee", "reader"])
        su.execute(
            "UPDATE xt_meta.person_role_grant SET revoked_at=clock_timestamp()"
            " WHERE tenant_id=%s AND grant_ref='reader'",
            (t.id,),
        )
        assert read() == (0, 1, ["employee"])
        su.execute(
            "UPDATE xt_meta.person_role_grant SET revoked_at=NULL"
            " WHERE tenant_id=%s AND grant_ref='reader'",
            (t.id,),
        )
        su.execute(
            "INSERT INTO xt_meta.pack_install"
            " (tenant_id,pack,version,digest,proposed_by,approved_by,status,models)"
            " VALUES (%s,'crm','1','test','builder','reviewer','disabled',%s)",
            (t.id, ["lead", "future"]),
        )
        assert read()[:2] == (0, 1)
        assert read(method="HEAD")[:2] == (0, 1)
        assert read("other")[:2] == (1, 1)
        assert read(subject="approver")[:2] == (1, 1)
        grant(t, alice, "admin", "admin")
        assert read()[:2] == (1, 1)
        su.execute(
            "UPDATE xt_meta.person_role_grant SET revoked_at=clock_timestamp()"
            " WHERE tenant_id=%s AND grant_ref='admin'",
            (t.id,),
        )
        assert read()[:2] == (0, 1)
        assert read(model="future")[:2] == (0, 1)
        su.execute("UPDATE xt_meta.pack_install SET status='enabled' WHERE tenant_id=%s", (t.id,))
        assert read()[:2] == read(model="future")[:2] == (1, 1)
        # Privilege/RLS invariants must survive the upgrade and installer replay.
        rows = su.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class"
            " WHERE oid IN (%s::regclass,%s::regclass,%s::regclass)",
            (f"xt_t{t.seq}.lead", "xt_meta.pack_install", "xt_meta.person_role_grant"),
        ).fetchall()
        assert rows == [(True, True)] * 3
        for table in (f"xt_t{t.seq}.lead", "xt_meta.pack_install", "xt_meta.person_role_grant"):
            assert su.execute(
                "SELECT has_table_privilege('xt_web',%s,'SELECT')", (table,)
            ).fetchone() == (False,)
        assert su.execute(
            "SELECT has_table_privilege('xt_web',%s,'INSERT,UPDATE,DELETE')", (view,)
        ).fetchone() == (False,)
        assert su.execute(
            "SELECT has_function_privilege('xt_web', 'xt.pgrst_gate(text,text)','EXECUTE')"
        ).fetchone() == (False,)
        assert su.execute(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema=%s"
            " AND column_name='secret'",
            (f"api_t{t.seq}",),
        ).fetchone() == (0,)
