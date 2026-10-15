# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import importlib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from types import ModuleType

import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import LocalIdp, start_idp
from tests.integration.composition.mcphttp import free_port
from xt.adapters.db.connection import Database
from xt.adapters.db.tenancy import bootstrap, provision_tenant
from xt.domain.model.context import TenantRef

pytestmark = pytest.mark.integration

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
    """`xt.entrypoints.composition` (đang được viết song song). Chưa có thì bỏ qua test cần nó."""
    try:
        return importlib.import_module(COMPOSITION)
    except ModuleNotFoundError as e:
        if e.name and COMPOSITION.startswith(e.name):
            pytest.skip(f"{COMPOSITION} chưa có")
        raise
