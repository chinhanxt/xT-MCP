# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Engine RecordService.aggregate validation and the PostgreSQL statement shape (no DB)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from tests.unit.model.support import catalog, customer
from xt.adapters.db.records import PgRecordRepository
from xt.application.model_engine.ports import AggregatePage, AggregateQuery, DisabledApps
from xt.application.model_engine.service import MAX_GROUPS, RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.spec import ModelSpec

T = TenantRef(1, uuid4())
AS_OF = datetime(2026, 11, 30, 3, 0, tzinfo=UTC)


def actor(*roles: str) -> ModelActor:
    return ModelActor(T, uuid4(), roles or ("manager",))


def warehouse_ref() -> dict[str, Any]:
    return {
        "id": "m2", "name": "warehouse", "label": "Kho",
        "fields": [{"id": "w1", "name": "code", "type": "text", "label": "Mã"}],
        "permissions": {"manager": {"actions": ["read"], "scope": "all"}},
    }  # fmt: skip


def movement() -> dict[str, Any]:
    m = customer()
    m["fields"] += [
        {"id": "f7", "name": "warehouse", "type": "ref", "ref": "warehouse", "label": "Kho"},
        {"id": "f8", "name": "occurred_on", "type": "date", "label": "Ngày"},
        {"id": "f9", "name": "extra", "type": "json", "label": "Khác"},
    ]
    return m


class Repo:
    def __init__(self, rows=(), truncated=False):
        self.rows, self.truncated, self.calls = list(rows), truncated, []

    def aggregate(self, actor, m, where, query, max_groups, scope_hint):
        self.calls.append((where, query, max_groups, scope_hint))
        return AggregatePage(AS_OF, self.rows, self.truncated)


def service(repo):
    cats = SimpleNamespace(
        current=lambda a: (3, catalog(warehouse_ref(), movement())),
        disabled=lambda a: DisabledApps(),
    )
    return RecordService(cats, repo)


@pytest.mark.parametrize(
    "query",
    [
        AggregateQuery(group_by=("nope",)),
        AggregateQuery(group_by=("extra",)),  # json
        AggregateQuery(group_by=("email",)),  # PII
        AggregateQuery(group_by=("tier", "tier")),
        AggregateQuery(sums=("tier",)),
        AggregateQuery(bucket=("month", "tier", "UTC")),
        AggregateQuery(bucket=("hour", "created_at", "UTC")),
    ],
)
def test_engine_rejects_unusable_fields(query):
    repo = Repo()
    with pytest.raises(ModelError) as ex:
        service(repo).aggregate(actor(), "customer", None, query)
    assert ex.value.code == "invalid_request" and repo.calls == []


def test_engine_requires_read_and_keeps_scope_and_filter():
    repo = Repo([(("gold",), 2, (Decimal("5"),))])
    with pytest.raises(ModelError) as ex:
        service(repo).aggregate(actor("guest"), "customer", None, AggregateQuery())
    assert ex.value.code == "access_denied"
    query = AggregateQuery(("tier",), ("credit",), ("month", "occurred_on", "Asia/Ho_Chi_Minh"))
    where = {"field": "tier", "op": "=", "value": "gold"}
    out = service(repo).aggregate(actor("sales"), "customer", where, query)
    assert out == {"model_revision": 3, "as_of": AS_OF, "rows": repo.rows}
    expr, passed, cap, scope = repo.calls[0]
    assert expr is not None and passed == query and cap == MAX_GROUPS and scope == "scoped"
    with pytest.raises(ModelError, match="Bộ lọc"):
        service(repo).aggregate(
            actor(), "customer", {"field": "nope", "op": "=", "value": 1}, query
        )


def test_engine_refuses_truncated_groups():
    with pytest.raises(ModelError) as ex:
        service(Repo(truncated=True)).aggregate(actor(), "customer", None, AggregateQuery())
    assert ex.value.code == "resource_constraint"


class Cursor:
    def __init__(self, rows):
        self.rows, self.sql, self.params = rows, [], []

    def execute(self, q, params=None):
        self.sql.append(q if isinstance(q, str) else q.as_string(None))
        self.params.append(params)
        return self

    def fetchone(self):
        return {"t": AS_OF}

    def fetchall(self):
        return self.rows


def pg(rows):
    cur = Cursor(rows)

    @contextmanager
    def tx(actor, workflow=False):
        yield cur

    return PgRecordRepository(SimpleNamespace(tenant_tx=tx)), cur


def test_pg_aggregate_is_one_grouped_statement_in_tenant_tx():
    m = ModelSpec.model_validate(movement())
    rows = [{"k0": "gold", "k1": date(2026, 11, 1), "n": 2, "s0": Decimal("7")}] * 3
    repo, cur = pg(rows)
    query = AggregateQuery(("tier",), ("credit",), ("month", "created_at", "Asia/Ho_Chi_Minh"))
    page = repo.aggregate(actor(), m, None, query, 2, "scoped")
    assert page.as_of == AS_OF and page.truncated and len(page.rows) == 2
    assert page.rows[0] == (("gold", date(2026, 11, 1)), 2, (Decimal("7"),))
    q = cur.sql[1]
    assert "date_trunc('month', (\"created_at\" AT TIME ZONE 'Asia/Ho_Chi_Minh'))::date" in q
    assert 'sum("credit") AS "s0"' in q and "GROUP BY 1, 2 ORDER BY 1, 2 LIMIT %s" in q
    assert "owner_id = %s" in q and cur.params[1][-1] == 3
    repo, cur = pg([{"n": 0, "s0": None}])
    page = repo.aggregate(actor(), m, None, AggregateQuery(sums=("credit",)), 2, "all")
    assert page.rows == [((), 0, (None,))] and "GROUP BY" not in cur.sql[1]
    repo, cur = pg([])
    day = AggregateQuery(bucket=("day", "occurred_on", "UTC"))
    repo.aggregate(actor(), m, None, day, 2, "all")
    assert "date_trunc('day', \"occurred_on\"::timestamp)::date" in cur.sql[1]
