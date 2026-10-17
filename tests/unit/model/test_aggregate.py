# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""aggregate_records tool output: periods, keys, totals, paging, contract schema (no DB)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from tests.unit.model.support import catalog
from tests.unit.model.test_aggregate_engine import (
    AS_OF,
    actor,
    movement,
    warehouse_ref,
)
from tests.unit.model.test_aggregate_engine import (
    Repo as _Repo,
)
from xt.application.model_engine.ports import AggregateQuery, DisabledApps
from xt.application.model_engine.service import RecordService
from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.application.tools.errors import ToolError
from xt.contracts.tools.catalog import BY_NAME
from xt.domain.identity import PrincipalKind
from xt.entrypoints.composition.aggregate import period_bounds
from xt.entrypoints.composition.records import RecordServiceAdapter

Repo: Any = _Repo


@pytest.mark.parametrize(
    ("period", "bounds"),
    [
        ("2026", (date(2026, 1, 1), date(2027, 1, 1))),
        ("2026-12", (date(2026, 12, 1), date(2027, 1, 1))),
        ("2026-Q4", (date(2026, 10, 1), date(2027, 1, 1))),
        ("2026-02-01..2026-02-28", (date(2026, 2, 1), date(2026, 3, 1))),
    ],
)
def test_period_bounds_are_half_open(period: str, bounds: tuple[date, date]) -> None:
    assert period_bounds(period) == bounds


def adapter(repo: Any, max_groups: int | None = None) -> tuple[Any, CallContext]:
    ma = actor()
    saves: list[Any] = []
    rt: Any = SimpleNamespace(
        caller=lambda ctx: SimpleNamespace(
            actor=ma,
            person=SimpleNamespace(id=ma.actor_id),
            profile=SimpleNamespace(timezone="Asia/Ho_Chi_Minh"),
        ),
        lineage=SimpleNamespace(save=lambda *args: saves.append(args), saves=saves),
        catalogs=SimpleNamespace(
            current=lambda a: (3, catalog(warehouse_ref(), movement())),
            disabled=lambda a: DisabledApps(),
        ),
        repo=repo,
    )
    rt.records = RecordService(rt.catalogs, repo)
    ctx = CallContext(
        Actor("t1", "sub", PrincipalKind.HUMAN, "desk", None),
        ClientClass.HUMAN,
        "trace-1",
        AS_OF,
    )
    return RecordServiceAdapter(rt, cursor_key=b"k" * 32, max_aggregate_groups=max_groups), ctx


def test_tool_output_rows_totals_refs_and_as_of() -> None:
    w1, w2 = uuid4(), uuid4()
    repo = Repo(
        [
            ((str(w1), date(2026, 11, 1)), 2, (Decimal("1500.000"),)),
            ((str(w2), date(2026, 11, 1)), 1, (None,)),
        ]
    )
    a, ctx = adapter(repo)
    out = a.aggregate(
        ctx,
        {
            "model_id": "customer",
            "metrics": ["count", "sum:credit"],
            "group_by": ["warehouse"],
            "time_bucket": "month",
            "date_field": "occurred_on",
            "period": "2026-11",
            "where": {"tier": "gold"},
            "sort": "-sum:credit",
        },
    )
    assert out["as_of"] == "2026-11-30T03:00:00Z" and out["scope"] == "authorized_records"
    assert out["rows"][0] == {
        "keys": {"warehouse": f"warehouse:{w1}", "occurred_on:month": "2026-11-01"},
        "values": {"count": 2, "sum:credit": "1500"},
    }
    assert out["rows"][1]["values"] == {"count": 1, "sum:credit": "0"}
    assert out["totals"] == {"count": 3, "sum:credit": "1500"}
    assert out["lineage_ref"] == "explain:trace-1" and out["next_cursor"] is None
    assert "2026-11" in out["summary"]
    schema = Draft202012Validator(BY_NAME["aggregate_records"].output_schema)
    assert [e.message for e in schema.iter_errors(out)] == []
    expr, query, _, _ = repo.calls[0]
    where = expr.model_dump(by_alias=True, exclude_none=True)
    assert query == AggregateQuery(
        ("warehouse",), ("credit",), ("month", "occurred_on", "Asia/Ho_Chi_Minh")
    )
    assert {"field": "occurred_on", "op": ">=", "value": "2026-11-01"} in where["and"]
    assert {"field": "occurred_on", "op": "<", "value": "2026-12-01"} in where["and"]
    assert {"field": "tier", "op": "=", "value": "gold"} in where["and"]


def test_tool_uses_configured_group_cap() -> None:
    repo = Repo()
    a, ctx = adapter(repo, max_groups=42)
    a.aggregate(ctx, {"model_id": "customer", "group_by": ["tier"]})
    assert repo.calls[0][2] == 42
    repo = Repo()
    a, ctx = adapter(repo)
    a.aggregate(ctx, {"model_id": "customer", "group_by": ["tier"]})
    assert repo.calls[0][2] == 1000


def test_tool_paginates_groups_and_bounds_datetime_period_in_tenant_time() -> None:
    repo = Repo([((t,), 1, ()) for t in ("a", "b", "c")])
    a, ctx = adapter(repo)
    args = {"model_id": "customer", "group_by": ["tier"], "limit": 2, "period": "2026"}
    first = a.aggregate(ctx, args)
    assert [r["keys"]["tier"] for r in first["rows"]] == ["a", "b"]
    assert first["totals"] == {"count": 3}
    second = a.aggregate(ctx, {**args, "cursor": first["next_cursor"]})
    assert [r["keys"]["tier"] for r in second["rows"]] == ["c"] and second["next_cursor"] is None
    where = repo.calls[0][0].model_dump(by_alias=True, exclude_none=True)
    lo = {"field": "created_at", "op": ">=", "value": "2026-01-01T00:00:00+07:00"}
    assert lo in where["and"]


@pytest.mark.parametrize(
    "bad",
    [{"metric": "advance_total"}, {"metrics": ["avg:credit"]}, {"sort": "nope"}],
)
def test_tool_rejects_unsupported_requests(bad: Any) -> None:
    a, ctx = adapter(Repo())
    with pytest.raises(ToolError):
        a.aggregate(ctx, {"model_id": "customer", **bad})


def test_lineage_persists_caller_bound_query_with_replayable_refs() -> None:
    a, ctx = adapter(Repo())
    warehouse = f"warehouse:{uuid4()}"
    a.aggregate(
        ctx,
        {
            "model_id": "customer",
            "group_by": ["warehouse"],
            "where": {"warehouse": warehouse, "owner": "me"},
        },
    )
    saved_actor, key, trace, query = a.rt.lineage.saves[0]
    where = {"warehouse": warehouse, "owner": f"person:{saved_actor.actor_id}"}
    assert (key, trace) == (ctx.actor.key, ctx.trace_id)
    assert query == {
        "model_id": "customer",
        "model_revision": 3,
        "where": where,
        "group_by": ["warehouse"],
        "metrics": ["count"],
        "date_field": "created_at",
        "time_bucket": None,
        "timezone": "Asia/Ho_Chi_Minh",
        "as_of": "2026-11-30T03:00:00Z",
        "search_records": {"model_id": "customer", "where": where},
    }


@pytest.mark.parametrize(
    "where", [{"email": "secret@example.test"}, {"email": {"contains": "secret"}}]
)
def test_lineage_rejects_pii_filters_before_querying_or_persisting(where: Any) -> None:
    repo = Repo()
    a, ctx = adapter(repo)
    with pytest.raises(ToolError) as exc:
        a.aggregate(ctx, {"model_id": "customer", "where": where})
    assert exc.value.code == "invalid_request"
    assert repo.calls == [] and a.rt.lineage.saves == []
    assert "secret" not in exc.value.message


@pytest.mark.parametrize(
    ("field", "original", "expected"),
    [
        (
            "occurred_on",
            {"gte": "2026-11-15", "lt": "2026-12-15"},
            {"gte": "2026-11-15", "lt": "2026-12-01"},
        ),
        (
            "created_at",
            {"gte": "2026-10-31T18:00:00Z"},
            {"gte": "2026-10-31T18:00:00Z", "lt": "2026-12-01T00:00:00+07:00"},
        ),
    ],
)
def test_lineage_period_intersects_existing_bounds_in_tenant_timezone(
    field: str, original: Any, expected: Any
) -> None:
    a, ctx = adapter(Repo())
    a.aggregate(
        ctx,
        {
            "model_id": "customer",
            "period": "2026-11",
            "date_field": field,
            "where": {field: original},
        },
    )
    query = a.rt.lineage.saves[0][3]
    assert query["where"] == {field: expected}
    assert query["search_records"] == {"model_id": "customer", "where": {field: expected}}


def test_lineage_ref_in_filter_can_be_replayed() -> None:
    a, ctx = adapter(Repo())
    original = {"model_id": "customer", "where": {"warehouse": {"in": [str(uuid4())]}}}
    a.aggregate(ctx, original)
    replay = a.rt.lineage.saves[0][3]["search_records"]
    a.aggregate(ctx, replay)
    assert replay == original
