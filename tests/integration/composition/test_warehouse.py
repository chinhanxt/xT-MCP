# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Real PostgreSQL and generic MCP dispatcher/confirmation path; no external IdP container."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.integration.composition.fixtures import ROOT, run_admin
from tests.integration.composition.idp import DESK_CLIENT
from tests.integration.composition.test_pack_ops import Ops
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.application.tools.context import Actor
from xt.application.tools.ports import PendingStatus
from xt.domain.identity import PrincipalKind
from xt.domain.model.errors import ModelError
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration


class Warehouse(Ops):
    def agent(self, key):
        # The UI is a human OIDC client; decide still requires actual confirmation.
        return Actor(self.alias, self.sub(key), PrincipalKind.HUMAN, DESK_CLIENT, None)

    def caller(self, key):
        actor = self.agent(key)
        return self.rt.resolve(actor, f"wh-{uuid.uuid4().hex}"), actor

    def request(self, values):
        result = self.tool(
            "NV-0002",
            "create_record",
            {
                "model_id": "stock_request",
                "values": {"code": uuid.uuid4().hex, **values},
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert result["ok"], result
        row = result["data"]["record"]
        result = self.tool(
            "NV-0002",
            "submit_request",
            {
                "ref": row["ref"],
                "expected_revision": row["revision"],
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert result["ok"], result
        return result["data"]["request"]

    def approval(self, row, decision="approve"):
        args = {
            "ref": row["ref"],
            "expected_revision": row["revision"],
            "decision": decision,
            "reason": "Warehouse review",
            "idempotency_key": uuid.uuid4().hex,
        }
        pending = self.tool("NV-0001", "decide_request", args)
        assert not pending["ok"] and pending["error"]["code"] == "confirmation_required", pending
        return pending, args


def install_warehouse(env, tmp_path_factory, alias):
    folder = tmp_path_factory.mktemp("warehouse-org")
    rc, out = run_admin(
        [
            "tenant",
            "create",
            "--alias",
            alias,
            "--name",
            "Warehouse integration",
            "--timezone",
            "Asia/Ho_Chi_Minh",
            "--llm-egress",
            "masked",
        ],
        env,
    )
    assert rc == 0, out
    people = [("NV-0001", ["warehouse_manager", "admin"]), ("NV-0002", [])]
    org = {
        "departments": [{"id": "WH", "name": "Kho", "parent_id": None}],
        "positions": [],
        "employees": [
            {
                "id": key,
                "full_name": key,
                "email": f"{key.lower()}@warehouse.example",
                "department_id": "WH",
                "position_id": None,
                "manager_id": None,
                "active": True,
                "roles": roles,
            }
            for key, roles in people
        ],
    }
    (folder / "org.json").write_text(json.dumps(org))
    (folder / "links.json").write_text(
        json.dumps(
            {"subjects": {key: f"kc-{alias}-{key}" for key, _ in people}, "agent_grants": []}
        )
    )
    rc, out = run_admin(
        [
            "org",
            "import",
            "--tenant",
            alias,
            str(folder / "org.json"),
            "--links",
            str(folder / "links.json"),
            "--base-roles",
            "employee",
        ],
        env,
    )
    assert rc == 0, out
    common = [
        "pack",
        "install",
        "--tenant",
        alias,
        str(ROOT / "packages/kho-hang"),
        "--proposed-by",
        "agent:test",
        "--approved-by",
        "person:admin",
    ]
    rc, plan = run_admin(common, env)
    assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
    digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
    assert digest, plan
    rc, out = run_admin([*common, "--confirm", digest.group(1)], env)
    assert rc == 0, out
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        tenant = tenant_of_alias(conn, alias)
    return tenant


@pytest.fixture(scope="module")
def warehouse(stack_base, mcp_port, tmp_path_factory):
    env = stack_base.env
    alias = "warehouseit"
    tenant = install_warehouse(env, tmp_path_factory, alias)
    prepared = prepare(
        env, argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    )
    comp = prepared.services
    try:
        yield Warehouse(comp.runtime, comp.engine, tenant, {}, prepared.dispatcher, alias=alias)
    finally:
        prepared.close()


@pytest.fixture(scope="module")
def other_warehouse(warehouse, stack_base, tmp_path_factory):
    alias = "warehouseother"
    tenant = install_warehouse(stack_base.env, tmp_path_factory, alias)
    return Warehouse(warehouse.rt, warehouse.engine, tenant, {}, warehouse.dispatcher, alias=alias)


@pytest.fixture
def stock(warehouse):
    wh = warehouse.create("NV-0001", "warehouse", {"code": uuid.uuid4().hex, "name": "Kho"})
    loc = warehouse.create(
        "NV-0001",
        "warehouse_location",
        {"code": uuid.uuid4().hex, "name": "Kệ", "warehouse": wh["id"]},
    )
    item = warehouse.create(
        "NV-0001", "stock_item", {"sku": uuid.uuid4().hex, "name": "Hàng", "unit": "cái"}
    )
    return {
        "warehouse": wh["id"],
        "kind": "receive",
        "lines": [{"item": item["id"], "location": loc["id"], "quantity_milli": 1000}],
    }


def movements(w, ref):
    return w.search("stock_movement", {"field": "request", "op": "=", "value": ref.split(":")[1]})


def balance(w, stock):
    line = stock["lines"][0]
    return w.search(
        "stock_balance", {"field": "key", "op": "=", "value": f"{line['item']}:{line['location']}"}
    )


def confirm(w, row, decision="approve"):
    pending, args = w.approval(row, decision)
    result = w.confirm(pending, "NV-0001", "NV-0001")
    return result, pending, args


def test_generic_approval_posting_retry_and_immutability(warehouse, stock):
    w = warehouse
    row = w.request(stock)
    changed = w.tool(
        "NV-0002",
        "update_record",
        {
            "ref": row["ref"],
            "set": {"note": "changed"},
            "expected_revision": row["revision"],
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert not changed["ok"], changed
    result, pending, args = confirm(w, row)
    assert result.status is PendingStatus.CONFIRMED, result
    stored = w.get("stock_request", row["ref"].split(":")[1])
    assert stored["state"] == "approved"
    assert int(balance(w, stock)[0]["quantity_milli"]) == 1000
    ledger = movements(w, row["ref"])
    assert len(ledger) == 1 and ledger[0]["approved_by"] == w.pid("NV-0001")
    replay = w.tool("NV-0001", "decide_request", args)
    assert replay["ok"], replay
    assert len(movements(w, row["ref"])) == 1
    again = w.confirm(pending, "NV-0001", "NV-0001")
    assert again.status is PendingStatus.CONFIRMED, again
    assert len(movements(w, row["ref"])) == 1
    denied = w.tool(
        "NV-0001",
        "update_record",
        {
            "ref": row["ref"],
            "set": {"note": "tamper"},
            "expected_revision": stored["revision"],
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert not denied["ok"], denied
    for model in ("stock_balance", "stock_movement"):
        denied = w.tool(
            "NV-0001",
            "create_record",
            {"model_id": model, "values": {}, "idempotency_key": uuid.uuid4().hex},
        )
        assert not denied["ok"], denied
        existing = balance(w, stock)[0] if model == "stock_balance" else ledger[0]
        denied = w.tool(
            "NV-0001",
            "update_record",
            {
                "ref": f"{model}:{existing['id']}",
                "set": {"quantity_milli": 1},
                "expected_revision": existing["revision"],
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert not denied["ok"], denied
        with pytest.raises(ModelError):
            w.run(
                "NV-0001",
                lambda c, _, model=model, existing=existing: w.rt.records.delete(
                    c.actor, model, uuid.UUID(existing["id"]), existing["revision"]
                ),
            )


def test_failed_issue_rolls_back_decision_and_workflow(warehouse, stock):
    w = warehouse
    row = w.request({**stock, "kind": "issue"})
    result, _, _ = confirm(w, row)
    assert result.status is PendingStatus.INVALIDATED and result.error, result
    assert result.error["code"] == "precondition_failed", result
    assert result.error["details"]["reason"] == "insufficient_stock", result
    stored = w.get("stock_request", row["ref"].split(":")[1])
    assert stored["state"] == "in_review" and stored["revision"] == row["revision"]
    assert movements(w, row["ref"]) == [] and balance(w, stock) == []
    assert w.inbox_item("NV-0001", row["ref"])["revision"] == row["revision"]
    received = w.request(stock)
    assert confirm(w, received)[0].status is PendingStatus.CONFIRMED
    assert confirm(w, row)[0].status is PendingStatus.CONFIRMED
    assert int(balance(w, stock)[0]["quantity_milli"]) == 0


def test_competing_issues_cannot_overspend(warehouse, stock):
    w = warehouse
    assert confirm(w, w.request(stock))[0].status is PendingStatus.CONFIRMED
    issue = {**stock, "kind": "issue", "lines": [{**stock["lines"][0], "quantity_milli": 700}]}
    requests = [w.request(issue), w.request(issue)]
    pending = [w.approval(row)[0] for row in requests]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: w.confirm(p, "NV-0001", "NV-0001"), pending))
    assert sum(r.status is PendingStatus.CONFIRMED for r in results) == 1, results
    assert int(balance(w, stock)[0]["quantity_milli"]) == 300
    assert sum(len(movements(w, r["ref"])) for r in requests) == 1


def test_invalid_references_rejection_and_sod(warehouse, stock):
    w = warehouse
    for bad in (
        {**stock, "lines": stock["lines"] * 2},
        {**stock, "lines": [{**stock["lines"][0], "item": str(uuid.uuid4())}]},
        {**stock, "lines": [{**stock["lines"][0], "quantity_milli": 1.5}]},
    ):
        out = w.tool(
            "NV-0002",
            "create_record",
            {
                "model_id": "stock_request",
                "values": {"code": uuid.uuid4().hex, **bad},
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert not out["ok"], out
    row = w.request(stock)
    assert confirm(w, row, "reject")[0].status is PendingStatus.CONFIRMED
    assert w.get("stock_request", row["ref"].split(":")[1])["state"] == "rejected"
    assert movements(w, row["ref"]) == []
    own = w.tool(
        "NV-0001",
        "create_record",
        {
            "model_id": "stock_request",
            "values": {"code": uuid.uuid4().hex, **stock},
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert own["ok"], own
    record = own["data"]["record"]
    reassigned = w.tool(
        "NV-0001",
        "update_record",
        {
            "ref": record["ref"],
            "expected_revision": record["revision"],
            "set": {"owner_id": w.pid("NV-0002")},
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert not reassigned["ok"] and reassigned["error"]["code"] == "rule_violation", reassigned
    submitted = w.tool(
        "NV-0001",
        "submit_request",
        {
            "ref": record["ref"],
            "expected_revision": record["revision"],
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    # Runtime may reject submission when no distinct manager is available; either way no posting.
    if submitted["ok"]:
        denied = w.tool(
            "NV-0001",
            "decide_request",
            {
                "ref": record["ref"],
                "expected_revision": submitted["data"]["request"]["revision"],
                "decision": "approve",
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert not denied["ok"] and denied["error"]["code"] != "confirmation_required", denied
    assert movements(w, record["ref"]) == []


def test_cross_tenant_references_and_rls(warehouse, other_warehouse, stock):
    w, other = warehouse, other_warehouse
    row = w.request(stock)
    assert confirm(w, row)[0].status is PendingStatus.CONFIRMED
    for model, rid in (
        ("warehouse", stock["warehouse"]),
        ("stock_item", stock["lines"][0]["item"]),
        ("stock_request", row["ref"].split(":")[1]),
        ("stock_movement", movements(w, row["ref"])[0]["id"]),
        ("stock_balance", balance(w, stock)[0]["id"]),
    ):
        result = other.tool("NV-0001", "get_record", {"ref": f"{model}:{rid}"})
        assert not result["ok"] and result["error"]["code"] == "not_found", result
    own_warehouse = other.create(
        "NV-0001", "warehouse", {"code": uuid.uuid4().hex, "name": "Other"}
    )
    bad = other.tool(
        "NV-0002",
        "create_record",
        {
            "model_id": "stock_request",
            "values": {"code": uuid.uuid4().hex, **stock, "warehouse": own_warehouse["id"]},
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    assert not bad["ok"] and bad["error"]["code"] == "not_found", bad

    def inspect(_, pc):
        cur = other.rt.uow.current_cursor
        for model in ("stock_request", "stock_balance", "stock_movement"):
            cur.execute(f"SELECT id FROM xt_t{w.tenant.seq}.{model}")
            assert cur.fetchall() == []

    other.run("NV-0001", inspect)


def test_late_posting_failure_rolls_back_rows_and_decision(warehouse, stock, monkeypatch):
    from xt.entrypoints.composition.pack_ops import _Data

    w = warehouse
    row = w.request(stock)
    create = _Data.create
    writes = []

    def fail_after_ledger_write(data, model, values):
        result = create(data, model, values)
        if model == "stock_movement":
            writes.append(result["id"])
            raise RuntimeError("Injected failure after the ledger insert")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(_Data, "create", fail_after_ledger_write)
        result = confirm(w, row)[0]
    assert writes and result.status is PendingStatus.INVALIDATED, result
    stored = w.get("stock_request", row["ref"].split(":")[1])
    assert stored["state"] == "in_review" and stored["revision"] == row["revision"]
    assert movements(w, row["ref"]) == [] and balance(w, stock) == []
    assert w.inbox_item("NV-0001", row["ref"])["revision"] == row["revision"]

    def decisions(_, pc):
        return w.rt.uow.current_cursor.execute(
            "SELECT id FROM xt_meta.decision WHERE ref = %s", (row["ref"],)
        ).fetchall()

    assert w.run("NV-0001", decisions) == []
    assert confirm(w, row)[0].status is PendingStatus.CONFIRMED
    assert len(movements(w, row["ref"])) == 1
    assert int(balance(w, stock)[0]["quantity_milli"]) == 1000
