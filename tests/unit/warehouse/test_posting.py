# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from xt.application.warehouse.posting import post_stock
from xt.application.warehouse.validation import validate_write
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.warehouse import MAX_QUANTITY, after_post, parse_lines


class Data:
    def __init__(self):
        self.rows_by_model = {
            m: {}
            for m in (
                "stock_request",
                "stock_item",
                "warehouse",
                "warehouse_location",
                "stock_balance",
                "stock_movement",
            )
        }
        self.writes = 0

    def get(self, model, rid):
        return deepcopy(self.rows_by_model[model][str(rid)])

    def rows(self, model, where):
        return [
            deepcopy(r)
            for r in self.rows_by_model[model].values()
            if str(r[where["field"]]) == str(where["value"])
        ]

    def create(self, model, values):
        row = {"id": str(uuid4()), "revision": 1, **values}
        self.rows_by_model[model][row["id"]] = row
        self.writes += 1
        return deepcopy(row)

    def write(self, model, row, patch):
        saved = self.rows_by_model[model][str(row["id"])]
        saved.update(patch, revision=saved["revision"] + 1)
        self.writes += 1
        return deepcopy(saved)


def fixture(kind="receive", amount=1000):
    db = Data()
    warehouse = db.create("warehouse", {})
    item = db.create("stock_item", {})
    location = db.create("warehouse_location", {"warehouse": warehouse["id"]})
    request = db.create(
        "stock_request",
        {
            "owner_id": str(uuid4()),
            "state": "in_review",
            "kind": kind,
            "warehouse": warehouse["id"],
            "lines": [{"item": item["id"], "location": location["id"], "quantity_milli": amount}],
        },
    )
    db.writes = 0
    return db, UUID(request["id"]), uuid4()


@pytest.mark.parametrize(
    "value", [True, False, 0, -1, 1.5, "1000", MAX_QUANTITY + 1, Decimal("1.1")]
)
def test_quantity_rejects_invalid_line(value):
    with pytest.raises(ModelError):
        parse_lines([{"item": str(uuid4()), "location": str(uuid4()), "quantity_milli": value}])


def test_duplicate_or_empty_lines_rejected():
    line = {"item": str(uuid4()), "location": str(uuid4()), "quantity_milli": 10}
    for rows in ([], [line, line], [{**line, "extra": 1}], [line] * 101):
        with pytest.raises(ModelError):
            parse_lines(rows)


def test_receive_retry_and_issue_reconcile():
    db, rid, manager = fixture()
    assert post_stock(db, rid, manager)["already"] is False
    assert post_stock(db, rid, manager)["already"] is True
    assert len(db.rows_by_model["stock_movement"]) == 1
    request = db.get("stock_request", rid)
    request.update(id=str(uuid4()), kind="issue")
    request["lines"][0]["quantity_milli"] = 400
    db.rows_by_model["stock_request"][request["id"]] = request
    next(iter(db.rows_by_model["stock_balance"].values()))["quantity_milli"] = "1000"
    post_stock(db, UUID(request["id"]), manager)
    balance = next(iter(db.rows_by_model["stock_balance"].values()))
    assert balance["quantity_milli"] == 600
    assert sum(r["quantity_milli"] for r in db.rows_by_model["stock_movement"].values()) == 600


def test_insufficient_stock_and_sod_write_nothing():
    db, rid, manager = fixture("issue")
    with pytest.raises(ModelError, match="Không đủ tồn"):
        post_stock(db, rid, manager)
    assert db.writes == 0
    with pytest.raises(ModelError, match="không được duyệt"):
        post_stock(db, rid, UUID(db.get("stock_request", rid)["owner_id"]))
    assert db.writes == 0


def test_cross_warehouse_and_incomplete_ledger_fail_closed():
    db, rid, manager = fixture()
    location = next(iter(db.rows_by_model["warehouse_location"].values()))
    location["warehouse"] = str(uuid4())
    with pytest.raises(ModelError, match="không thuộc kho"):
        post_stock(db, rid, manager)
    assert db.writes == 0
    db.create("stock_movement", {"request": str(rid), "key": "wrong"})
    with pytest.raises(ModelError, match="không đầy đủ"):
        post_stock(db, rid, manager)


def test_multi_line_failure_writes_nothing():
    db, rid, manager = fixture("issue", 500)
    req = db.rows_by_model["stock_request"][str(rid)]
    line = req["lines"][0]
    db.create(
        "stock_balance", {"key": f"{line['item']}:{line['location']}", "quantity_milli": 1000}
    )
    second = db.create("stock_item", {})
    req["lines"].append({**line, "item": second["id"]})
    db.writes = 0
    with pytest.raises(ModelError):
        post_stock(db, rid, manager)
    assert db.writes == 0


def test_overflow_does_not_lose_precision():
    assert after_post(MAX_QUANTITY - 1, 1) == MAX_QUANTITY
    with pytest.raises(ModelError):
        after_post(MAX_QUANTITY, 1)


@pytest.mark.parametrize("model", ["stock_balance", "stock_movement"])
def test_generic_writes_cannot_modify_ledger(model):
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("admin", "warehouse_manager"))
    with pytest.raises(ModelError, match="chỉ được ghi"):
        validate_write(None, actor, model, {})


@pytest.mark.parametrize("state", ["in_review", "approved", "rejected", "cancelled"])
def test_request_fields_locked_after_submission(state):
    with pytest.raises(ModelError):
        validate_write(None, None, "stock_request", {}, previous={"state": state})


def test_location_cannot_change_warehouse():
    with pytest.raises(ModelError, match="Không được chuyển"):
        validate_write(
            None, None, "warehouse_location", {"warehouse": "b"}, previous={"warehouse": "a"}
        )


def test_request_owner_cannot_be_forged_or_changed():
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("warehouse_manager",))
    with pytest.raises(ModelError, match="chủ phiếu"):
        validate_write(None, actor, "stock_request", {"owner_id": str(uuid4())})
    with pytest.raises(ModelError, match="đổi chủ"):
        validate_write(
            None,
            actor,
            "stock_request",
            {"owner_id": str(uuid4())},
            previous={"owner_id": str(actor.actor_id), "state": "draft"},
        )
