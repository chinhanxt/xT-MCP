# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Aggregate lineage opens through the MCP dispatcher and replays an authorized search."""

from __future__ import annotations

from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator

from tests.integration.composition.test_warehouse import (  # noqa: F401 (shared fixtures)
    confirm,
    movements,
    stock,
    warehouse,
)
from xt.application.tools.ports import PendingStatus
from xt.contracts.tools import BY_NAME

pytestmark = pytest.mark.integration


def test_aggregate_lineage_replays_posted_movement_refs(warehouse: Any, stock: Any) -> None:  # noqa: F811
    w = warehouse
    dated = {**stock, "doc_date": "2026-09-15"}
    receive = w.request(dated)
    assert cast(Any, confirm)(w, receive)[0].status is PendingStatus.CONFIRMED
    issue = w.request(
        {**dated, "kind": "issue", "lines": [{**stock["lines"][0], "quantity_milli": 400}]}
    )
    assert cast(Any, confirm)(w, issue)[0].status is PendingStatus.CONFIRMED
    expected = {
        f"stock_movement:{r['id']}"
        for request in (receive, issue)
        for r in cast(Any, movements)(w, request["ref"])
    }
    args: dict[str, Any] = {
        "model_id": "stock_movement",
        "metrics": ["count", "sum:quantity_milli"],
        "where": {"warehouse": f"warehouse:{stock['warehouse']}"},
        "group_by": ["warehouse"],
        "time_bucket": "month",
        "date_field": "occurred_on",
        "period": "2026-09",
    }
    aggregate = w.tool("NV-0002", "aggregate_records", args)
    assert aggregate["ok"], aggregate
    assert aggregate["data"]["rows"][0]["values"] == {"count": 2, "sum:quantity_milli": "600"}
    explained = w.tool(
        "NV-0002", "get_history", {"ref": aggregate["data"]["lineage_ref"], "view": "explain"}
    )
    assert explained["ok"], explained
    Draft202012Validator(BY_NAME["get_history"].output_schema).validate(explained["data"])
    query = explained["data"]["explanation"]["query"]
    assert query["model_id"] == "stock_movement"
    assert query["group_by"] == args["group_by"] and query["metrics"] == args["metrics"]
    assert query["as_of"] == aggregate["data"]["as_of"]
    search = query["search_records"]
    assert search["where"]["warehouse"] == args["where"]["warehouse"]
    assert search["where"]["occurred_on"] == {"gte": "2026-09-01", "lt": "2026-10-01"}
    found = w.tool("NV-0002", "search_records", {**search, "limit": 1})
    assert found["ok"], found
    refs = {r["ref"] for r in found["data"]["items"]}
    assert found["data"]["next_cursor"]
    rest = w.tool(
        "NV-0002", "search_records", {**search, "limit": 1, "cursor": found["data"]["next_cursor"]}
    )
    assert rest["ok"], rest
    assert refs | {r["ref"] for r in rest["data"]["items"]} == expected
    assert rest["data"]["next_cursor"] is None
