# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from xt.application.warehouse.posting import MAX_SCAN, post_stock, scan_pages
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
                "stock_level",
            )
        }
        self.writes = 0

    def get(self, model, rid):
        return deepcopy(self.rows_by_model[model][str(rid)])

    @staticmethod
    def _match(row, where):
        value = row.get(where["field"])
        if where["op"] == "is_null":
            return value is None
        if where["op"] == ">":
            return value is not None and Decimal(str(value)) > Decimal(str(where["value"]))
        return str(value) == str(where["value"])

    def rows(self, model, where):
        where = {"op": "=", **where}
        return [deepcopy(r) for r in self.rows_by_model[model].values() if self._match(r, where)]

    def scan(self, model, where):
        matched = self.rows(model, where)  # pages of 2 so callers cross page boundaries
        return scan_pages(lambda n, o: (matched[o : o + n], o + n < len(matched)), 2)

    def create(self, model, values):
        row = {"id": str(uuid4()), "revision": 1, **values}
        self.rows_by_model[model][row["id"]] = row
        self.writes += 1
        return deepcopy(row)

    def write(self, model, row, patch):
        saved = self.rows_by_model[model][str(row["id"])]
        if row["revision"] != saved["revision"]:
            raise AssertionError(f"stale write to {model}")
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


def test_posting_tags_ledger_rows_with_warehouse():
    db, rid, manager = fixture()
    line = db.rows_by_model["stock_request"][str(rid)]["lines"][0]
    legacy = db.create(
        "stock_balance", {"key": f"{line['item']}:{line['location']}", "quantity_milli": 0}
    )
    post_stock(db, rid, manager)
    warehouse = db.get("stock_request", rid)["warehouse"]
    assert db.get("stock_balance", UUID(legacy["id"]))["warehouse"] == warehouse
    assert [m["warehouse"] for m in db.rows_by_model["stock_movement"].values()] == [warehouse]


@pytest.mark.parametrize("model", ["stock_balance", "stock_movement"])
def test_workflow_ledger_write_requires_warehouse(model):
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("xt_workflow",))
    with pytest.raises(ModelError, match="kho"):
        validate_write(None, actor, model, {"quantity_milli": 0}, workflow=True)
    validate_write(None, actor, model, {"quantity_milli": 0, "warehouse": str(uuid4())})


def test_movement_keeps_business_date_apart_from_posting_time():
    db, rid, manager = fixture()
    db.rows_by_model["stock_request"][str(rid)]["doc_date"] = "2026-10-01"
    post_stock(db, rid, manager)
    (movement,) = db.rows_by_model["stock_movement"].values()
    assert movement["occurred_on"] == "2026-10-01"
    assert movement["posted_at"][:10] != "2026-10-01"
    db, rid, manager = fixture()
    post_stock(db, rid, manager)
    (movement,) = db.rows_by_model["stock_movement"].values()
    assert movement["occurred_on"] is None and movement["posted_at"]


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


def posted(kind="receive", amount=1000, stock=0):
    db, rid, manager = fixture(kind, amount)
    line = db.rows_by_model["stock_request"][str(rid)]["lines"][0]
    if stock:
        db.create(
            "stock_balance",
            {
                "key": f"{line['item']}:{line['location']}",
                "item": line["item"],
                "location": line["location"],
                "quantity_milli": stock,
            },
        )
    post_stock(db, rid, manager)
    db.rows_by_model["stock_request"][str(rid)]["state"] = "approved"
    return db, rid, manager


def reversal(db, original):
    row = db.get("stock_request", original)
    return UUID(
        db.create(
            "stock_request",
            {
                "owner_id": str(uuid4()),
                "state": "in_review",
                "kind": "reversal",
                "reverses": str(original),
                "warehouse": row["warehouse"],
                "lines": list(reversed(row["lines"])),
            },
        )["id"]
    )


def on_hand(db):
    return sum(int(r["quantity_milli"]) for r in db.rows_by_model["stock_balance"].values())


@pytest.mark.parametrize(("kind", "stock", "after"), [("receive", 0, 0), ("issue", 1500, 1500)])
def test_reversal_posts_opposite_movements_once(kind, stock, after):
    db, original, manager = posted(kind, 1000, stock)
    rev = reversal(db, original)
    assert post_stock(db, rev, manager) == {"request": str(rev), "posted": 1, "already": False}
    assert on_hand(db) == after
    ledger = db.rows("stock_movement", {"field": "request", "value": str(rev)})
    assert [r["key"] for r in ledger] == [f"{original}:0:rev"]
    source = db.rows("stock_movement", {"field": "request", "value": str(original)})[0]
    assert ledger[0]["quantity_milli"] == -source["quantity_milli"]
    assert post_stock(db, rev, manager)["already"] is True
    assert len(db.rows_by_model["stock_movement"]) == 2


def test_second_reversal_rejected_without_writes():
    db, original, manager = posted()
    post_stock(db, reversal(db, original), manager)
    second = reversal(db, original)
    db.writes = 0
    with pytest.raises(ModelError, match="đã được đảo"):
        post_stock(db, second, manager)
    assert db.writes == 0


@pytest.mark.parametrize("state", ["draft", "in_review", "rejected", "cancelled"])
def test_reversal_of_unposted_slip_rejected(state):
    db, original, manager = fixture()
    db.rows_by_model["stock_request"][str(original)]["state"] = state
    rev = reversal(db, original)
    db.writes = 0
    with pytest.raises(ModelError, match="đã ghi nhận"):
        post_stock(db, rev, manager)
    assert db.writes == 0


def test_reversal_of_reversal_and_mismatched_lines_rejected():
    db, original, manager = posted()
    rev = reversal(db, original)
    post_stock(db, rev, manager)
    db.rows_by_model["stock_request"][str(rev)]["state"] = "approved"
    with pytest.raises(ModelError, match="không đảo phiếu đảo"):
        post_stock(db, reversal(db, rev), manager)
    other = reversal(db, original)
    db.rows_by_model["stock_request"][str(other)]["lines"][0]["quantity_milli"] = 999
    with pytest.raises(ModelError, match="trùng toàn bộ"):
        post_stock(db, other, manager)


def test_reversal_cannot_drive_stock_negative():
    db, original, manager = posted("receive", 1000)
    balance = next(iter(db.rows_by_model["stock_balance"].values()))
    balance["quantity_milli"] = 400  # 600 already issued after the receipt
    rev = reversal(db, original)
    db.writes = 0
    with pytest.raises(ModelError, match="Không đủ tồn"):
        post_stock(db, rev, manager)
    assert db.writes == 0


def test_reverses_only_on_reversal_slips():
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("employee",))
    line = [{"item": str(uuid4()), "location": str(uuid4()), "quantity_milli": 1}]
    for values in (
        {"kind": "receive", "reverses": str(uuid4()), "lines": line},
        {"kind": "reversal", "lines": line},
    ):
        with pytest.raises(ModelError, match="phiếu gốc"):
            validate_write(None, actor, "stock_request", values)


def level(db):
    (row,) = db.rows_by_model["stock_level"].values()
    return row


def test_stock_level_tracks_on_hand_and_reorder_flag():
    db, rid, manager = fixture("receive", 1000)
    request = db.get("stock_request", rid)
    line = request["lines"][0]
    db.rows_by_model["stock_item"][line["item"]]["reorder_level_milli"] = 1500
    post_stock(db, rid, manager)
    row = level(db)
    (movement,) = db.rows_by_model["stock_movement"].values()
    assert row["key"] == f"{line['item']}:{request['warehouse']}"
    assert (row["item"], row["warehouse"]) == (line["item"], request["warehouse"])
    assert (row["on_hand_milli"], row["reorder_level_milli"], row["below_reorder"]) == (
        1000,
        1500,
        True,
    )
    assert row["last_movement"] == movement["id"]
    copy = {k: v for k, v in request.items() if k not in ("id", "revision")}
    again = UUID(db.create("stock_request", copy)["id"])
    post_stock(db, again, manager)
    assert (level(db)["on_hand_milli"], level(db)["below_reorder"]) == (2000, False)


def test_stock_level_sums_locations_and_starts_from_existing_balances():
    db, rid, manager = fixture("issue", 300)
    request = db.rows_by_model["stock_request"][str(rid)]
    line = request["lines"][0]
    # Balances posted before stock_level existed, in two locations of the same warehouse.
    db.create(
        "stock_balance",
        {"key": f"{line['item']}:{line['location']}", "item": line["item"],
         "location": line["location"], "quantity_milli": 500},
    )  # fmt: skip
    other = db.create("warehouse_location", {"warehouse": request["warehouse"]})
    db.create(
        "stock_balance",
        {"key": f"{line['item']}:{other['id']}", "item": line["item"],
         "location": other["id"], "warehouse": request["warehouse"], "quantity_milli": 700},
    )  # fmt: skip
    foreign = db.create("warehouse_location", {"warehouse": str(uuid4())})
    db.create(
        "stock_balance",
        {"key": f"{line['item']}:{foreign['id']}", "item": line["item"],
         "location": foreign["id"], "quantity_milli": 9000},
    )  # fmt: skip
    post_stock(db, rid, manager)
    assert level(db)["on_hand_milli"] == 900


def test_failed_posting_leaves_stock_level_untouched():
    db, rid, manager = fixture("issue", 1000)
    with pytest.raises(ModelError):
        post_stock(db, rid, manager)
    assert db.rows_by_model["stock_level"] == {}


@pytest.mark.parametrize("previous", [None, {"on_hand_milli": 0}])
def test_generic_writes_cannot_touch_stock_level(previous):
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("admin", "warehouse_manager"))
    with pytest.raises(ModelError, match="chỉ được ghi"):
        validate_write(None, actor, "stock_level", {}, previous=previous)


def legacy_rows(db, rid):
    """A movement and a balance posted before `warehouse` existed, on another item."""
    request = db.get("stock_request", rid)
    location = request["lines"][0]["location"]
    old = db.create("stock_item", {})
    db.create(
        "stock_movement",
        {"key": f"{uuid4()}:0", "request": str(uuid4()), "item": old["id"],
         "location": location, "quantity_milli": 5},
    )  # fmt: skip
    db.create(
        "stock_balance",
        {"key": f"{old['id']}:{location}", "item": old["id"], "location": location,
         "quantity_milli": 5},
    )  # fmt: skip
    db.writes = 0
    return request["warehouse"]


def test_posting_backfills_legacy_warehouse_once(monkeypatch):
    import xt.application.warehouse.posting as posting

    db, rid, manager = fixture()
    warehouse = legacy_rows(db, rid)
    monkeypatch.setattr(posting, "BACKFILL_BATCH", 1)
    post_stock(db, rid, manager)
    for model in ("stock_movement", "stock_balance"):
        assert all(r.get("warehouse") == warehouse for r in db.rows_by_model[model].values())
    revisions = {r["id"]: r["revision"] for r in db.rows_by_model["stock_movement"].values()}
    request = db.get("stock_request", rid)
    again = db.create(
        "stock_request", {k: v for k, v in request.items() if k not in ("id", "revision")}
    )
    post_stock(db, UUID(again["id"]), manager)
    for movement_id, revision in revisions.items():
        assert db.rows_by_model["stock_movement"][movement_id]["revision"] == revision


def test_failed_issue_with_legacy_rows_writes_nothing():
    db, rid, manager = fixture("issue", 1000)
    legacy_rows(db, rid)
    with pytest.raises(ModelError, match="Không đủ tồn"):
        post_stock(db, rid, manager)
    assert db.writes == 0


class Locations:
    def __init__(self, warehouse):
        self.warehouse = warehouse

    def get(self, actor, model, rid):
        assert model == "warehouse_location"
        return {"record": {"id": str(rid), "warehouse": self.warehouse}}


def test_movement_edit_allowed_only_as_warehouse_backfill():
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ("xt_workflow",))
    wh = str(uuid4())
    old = {"id": str(uuid4()), "location": str(uuid4()), "quantity_milli": 5, "warehouse": None}
    service = Locations(wh)
    validate_write(service, actor, "stock_movement", {**old, "warehouse": wh},
                   previous=old, workflow=True)  # fmt: skip
    for row, previous in (
        ({**old, "warehouse": str(uuid4())}, old),  # not the location's warehouse
        ({**old, "warehouse": wh, "quantity_milli": 6}, old),  # other fields changed
        ({**old, "warehouse": wh}, {**old, "warehouse": str(uuid4())}),  # already set
    ):
        with pytest.raises(ModelError, match="Không được sửa"):
            validate_write(service, actor, "stock_movement", row, previous=previous,
                           workflow=True)  # fmt: skip
    with pytest.raises(ModelError, match="Không được sửa"):
        validate_write(service, actor, "stock_movement", {**old, "warehouse": wh},
                       previous=old)  # fmt: skip


def test_reorder_tracked_items_get_levels_without_postings():
    db, rid, manager = fixture()
    request = db.get("stock_request", rid)
    warehouse, location = request["warehouse"], request["lines"][0]["location"]
    idle = db.create("stock_item", {"reorder_level_milli": 300})
    stocked = db.create("stock_item", {"reorder_level_milli": 100})
    db.create(
        "stock_balance",
        {"key": f"{stocked['id']}:{location}", "item": stocked["id"], "location": location,
         "warehouse": warehouse, "quantity_milli": 250},
    )  # fmt: skip
    post_stock(db, rid, manager)
    levels = {r["item"]: r for r in db.rows_by_model["stock_level"].values()}
    assert (levels[idle["id"]]["on_hand_milli"], levels[idle["id"]]["below_reorder"]) == (0, True)
    assert (levels[stocked["id"]]["on_hand_milli"], levels[stocked["id"]]["below_reorder"]) == (
        250,
        False,
    )
    db.rows_by_model["stock_item"][stocked["id"]]["reorder_level_milli"] = 400
    again = db.create(
        "stock_request", {k: v for k, v in request.items() if k not in ("id", "revision")}
    )
    post_stock(db, UUID(again["id"]), manager)
    level = next(r for r in db.rows_by_model["stock_level"].values() if r["item"] == stocked["id"])
    assert (level["reorder_level_milli"], level["below_reorder"]) == (400, True)


def test_scan_fails_closed_past_offset_cap():
    rows = scan_pages(lambda n, o: ([{"o": o}] * n, True), 200)
    with pytest.raises(ModelError, match=str(MAX_SCAN)):
        for _ in rows:
            pass
