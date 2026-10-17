# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# ruff: noqa: F811  (reuse module-scoped database fixtures)
"""R4 regressions for context relation identity and catalog fingerprint coverage."""

from __future__ import annotations

import psycopg
import pytest

from tests.integration.model_audit_r3.test_model_engine_audit_r3_pg import (  # noqa: F401
    Env,
    cluster,
    envs,
    new_tenant,
)
from xt.adapters.db.audit import diff_security_catalog, take_checkpoint

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("db", ["xt", "xt_up"])
@pytest.mark.parametrize("table", ["session_ctx", "login_tenant"])
def test_context_relations_cannot_leave_protected_schema(
    envs: dict[str, Env], db: str, table: str
) -> None:
    e = envs[db]
    with e.mig() as m, m.transaction(force_rollback=True):
        m.execute("CREATE SCHEMA r4_context_move")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), m.transaction():
            m.execute(f"ALTER TABLE xt_meta.{table} SET SCHEMA r4_context_move")


@pytest.mark.parametrize("db", ["xt", "xt_up"])
def test_sod_trigger_cannot_be_replaced_with_false_predicate(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    with (
        e.mig() as m,
        m.transaction(force_rollback=True),
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        m.transaction(),
    ):
        m.execute(
            "CREATE OR REPLACE TRIGGER decision_sod_guard BEFORE INSERT OR UPDATE OR DELETE"
            " ON xt_meta.decision FOR EACH ROW WHEN (false)"
            " EXECUTE FUNCTION xt_meta.decision_sod_guard()"
        )


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("ALTER FUNCTION xt.tenant() STRICT", "function:xt.tenant()"),
        (
            "CREATE OR REPLACE VIEW xt.r4_catalog_view AS SELECT false AS allowed",
            "view:xt.r4_catalog_view",
        ),
        (
            "CREATE OR REPLACE TRIGGER r4_catalog_trigger BEFORE UPDATE ON xt_meta.session_ctx"
            " FOR EACH ROW WHEN (false) EXECUTE FUNCTION xt.r4_catalog_trigger()",
            "trigger:xt_meta.session_ctx.r4_catalog_trigger",
        ),
        (
            "ALTER DOMAIN xt.r4_catalog_domain ADD CONSTRAINT r4_check CHECK (VALUE <> 'secret')",
            "constraint:xt.r4_catalog_domain.r4_check",
        ),
    ],
)
def test_catalog_detects_executable_properties(
    envs: dict[str, Env], mutation: str, expected: str
) -> None:
    e = envs["xt"]
    new_tenant(e)
    with e.su() as su, su.transaction(force_rollback=True):
        su.execute("CREATE VIEW xt.r4_catalog_view AS SELECT true AS allowed")
        su.execute("CREATE DOMAIN xt.r4_catalog_domain AS text")
        su.execute(
            "CREATE FUNCTION xt.r4_catalog_trigger() RETURNS trigger LANGUAGE plpgsql"
            " AS $$ BEGIN RETURN NEW; END $$"
        )
        su.execute(
            "CREATE TRIGGER r4_catalog_trigger BEFORE UPDATE ON xt_meta.session_ctx"
            " FOR EACH ROW EXECUTE FUNCTION xt.r4_catalog_trigger()"
        )
        doc = take_checkpoint(su)
        assert diff_security_catalog(su, doc).ok
        su.execute(mutation)
        # Inspect through this transaction so its catalog mutation is visible.
        diff = diff_security_catalog(su, doc)
        changes = diff.added if expected.startswith("constraint:") else diff.changed
        assert expected in changes, diff
