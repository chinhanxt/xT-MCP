# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Optional pack schema must not hide permission, schema or connection failures."""

from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import Database
from xt.application.model_engine.ports import DisabledApps, StoreError
from xt.domain.model.context import ModelActor, TenantRef


def test_missing_pack_table_rolls_back_and_does_not_cache_absence() -> None:
    db = MagicMock(spec=Database)
    tx = db.tenant_tx.return_value
    cur = tx.__enter__.return_value
    missing = psycopg.errors.UndefinedTable('relation "xt_meta.pack_install" does not exist')
    cur.execute.side_effect = missing
    reader = PgCatalogReader(db)
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("admin",))

    assert reader.disabled(actor) == DisabledApps()
    assert tx.__exit__.call_args.args[1] is missing

    # Installing/enabling/disabling packs later must take effect on the same reader.
    cur.execute.side_effect = None
    cur.execute.return_value.fetchall.return_value = [{"pack": "crm", "models": ["lead"]}]
    assert reader.disabled(actor) == DisabledApps({"lead": "crm"}, frozenset({"crm"}))
    cur.execute.return_value.fetchall.return_value = []
    assert reader.disabled(actor) == DisabledApps()


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.InsufficientPrivilege("denied"),
        psycopg.errors.UndefinedColumn("models missing"),
        psycopg.errors.QueryCanceled("timeout"),
        psycopg.OperationalError("connection lost"),
    ],
)
def test_other_pack_query_errors_propagate(error: psycopg.Error) -> None:
    db = MagicMock(spec=Database)
    db.tenant_tx.return_value.__enter__.return_value.execute.side_effect = error
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ())

    with pytest.raises(StoreError) as exc:
        PgCatalogReader(db).disabled(actor)
    assert exc.value.__cause__ is error


def test_missing_context_table_is_not_treated_as_missing_pack_table() -> None:
    db = MagicMock(spec=Database)
    missing = psycopg.errors.UndefinedTable('relation "xt_meta.session_ctx" does not exist')
    db.tenant_tx.return_value.__enter__.side_effect = missing
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ())

    with pytest.raises(StoreError) as exc:
        PgCatalogReader(db).disabled(actor)
    assert exc.value.__cause__ is missing
