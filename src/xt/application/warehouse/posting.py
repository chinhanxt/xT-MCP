# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Posting runs under a tenant lock and an outer approval transaction, never commits itself."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from itertools import islice
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from xt.domain.warehouse import (
    REQUEST_KINDS,
    after_post,
    check_reversal,
    invalid,
    parse_lines,
    quantity,
    reversal_key,
)


class StockData(Protocol):
    def get(self, model: str, rid: UUID) -> dict[str, Any]: ...
    def rows(self, model: str, where: Mapping[str, Any]) -> list[dict[str, Any]]: ...
    def create(self, model: str, values: Mapping[str, Any]) -> dict[str, Any]: ...
    def write(
        self, model: str, row: Mapping[str, Any], patch: Mapping[str, Any]
    ) -> dict[str, Any]: ...
    def scan(self, model: str, where: Mapping[str, Any]) -> Iterator[dict[str, Any]]: ...


BACKFILL_BATCH = 100
MAX_SCAN = 10_000


def scan_pages(
    search: Callable[[int, int], tuple[list[dict[str, Any]], bool]], page: int
) -> Iterator[dict[str, Any]]:
    """Every row of a stable-sorted search, page by page (`search(limit, offset)`).

    Offsets stay consistent because posting holds `lock_stock` and only postings write the
    ledger models; fails closed past the engine's offset cap instead of silently truncating.
    """
    offset = 0
    while True:
        if offset > MAX_SCAN:
            raise invalid(f"Quá {MAX_SCAN} bản ghi cần đọc trong một lần ghi kho.")
        rows, more = search(page, offset)
        yield from rows
        if not more:
            return
        offset += len(rows)


def _eq(field: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": "=", "value": value}


def _sources(
    db: StockData, row: dict[str, Any], request_id: UUID
) -> list[tuple[str, UUID, UUID, int]]:
    """(movement key, item, location, delta) planned for this slip, in a stable order."""
    lines = parse_lines(row.get("lines"))
    if row["kind"] != "reversal":
        sign = 1 if row["kind"] == "receive" else -1
        return [
            (f"{request_id}:{i}", line.item, line.location, sign * line.quantity_milli)
            for i, line in enumerate(lines)
        ]
    original = db.get("stock_request", UUID(str(row["reverses"])))
    check_reversal(row, original)
    ledger = db.rows(
        "stock_movement", {"field": "request", "op": "=", "value": str(original["id"])}
    )
    if {r["key"] for r in ledger} != {f"{original['id']}:{i}" for i in range(len(lines))}:
        raise invalid("Sổ biến động của phiếu gốc không đầy đủ; cần đối soát trước khi đảo.")
    return [
        (
            reversal_key(r["key"]),
            UUID(str(r["item"])),
            UUID(str(r["location"])),
            -int(Decimal(str(r["quantity_milli"]))),
        )
        for r in sorted(ledger, key=lambda r: str(r["key"]))
    ]


def post_stock(
    db: StockData, request_id: UUID, approver: UUID, timezone: str = "UTC"
) -> dict[str, Any]:
    row = db.get("stock_request", request_id)
    if str(row["owner_id"]) == str(approver):
        raise invalid("Người tạo phiếu không được duyệt phiếu của mình.")
    if row.get("kind") not in REQUEST_KINDS:
        raise invalid("Loại phiếu không hợp lệ.")
    sources = _sources(db, row, request_id)
    existing = db.rows("stock_movement", {"field": "request", "op": "=", "value": str(request_id)})
    if existing:
        if {r["key"] for r in existing} != {s[0] for s in sources}:
            raise invalid("Sổ biến động không đầy đủ; cần đối soát trước khi thử lại.")
        if row["state"] not in ("in_review", "approved"):
            raise invalid("Trạng thái phiếu không khớp sổ kho.")
        return {"request": str(request_id), "posted": len(sources), "already": True}
    if row["state"] != "in_review":
        raise invalid("Chỉ ghi nhận phiếu đang chờ duyệt.")
    warehouse = str(row["warehouse"])
    db.get("warehouse", UUID(warehouse))
    planned = []
    items: dict[UUID, dict[str, Any]] = {}
    level_delta: dict[UUID, int] = {}
    for key, item, location_id, delta in sources:
        # Only reversal keys can pre-exist under another slip (`<original>:<i>:rev`).
        if row["kind"] == "reversal" and db.rows("stock_movement", _eq("key", key)):
            raise invalid("Phiếu gốc đã được đảo; không đảo lần thứ hai.")
        items[item] = db.get("stock_item", item)
        location = db.get("warehouse_location", location_id)
        if str(location["warehouse"]) != warehouse:
            raise invalid("Vị trí không thuộc kho của phiếu.")
        balance_key = f"{item}:{location_id}"
        balances = db.rows("stock_balance", {"field": "key", "op": "=", "value": balance_key})
        balance = balances[0] if balances else None
        total = after_post(Decimal(str(balance["quantity_milli"])) if balance else 0, delta)
        planned.append((key, item, location_id, delta, balance_key, balance, total))
        level_delta[item] = level_delta.get(item, 0) + delta
    levels = []
    for item, delta in level_delta.items():
        level_key = f"{item}:{warehouse}"
        found = db.rows("stock_level", {"field": "key", "op": "=", "value": level_key})
        current = found[0]["on_hand_milli"] if found else _opening(db, item, warehouse)
        on_hand = after_post(Decimal(str(current)), delta)
        reorder = quantity(Decimal(str(items[item].get("reorder_level_milli") or 0)))
        levels.append((item, level_key, found[0] if found else None, on_hand, reorder))
    now = datetime.now(UTC)
    posted_at = now.isoformat()
    # Slip without a business date: it happened when it was posted (tenant-local date).
    occurred_on = row.get("doc_date") or now.astimezone(ZoneInfo(timezone)).date().isoformat()
    # Validate every line before the first write. Outer UoW also rolls back late DB failures.
    last_movement: dict[UUID, str] = {}
    for key, item, location_id, delta, balance_key, balance, total in planned:
        if balance is None:
            balance = db.create(
                "stock_balance",
                {
                    "key": balance_key,
                    "item": str(item),
                    "location": str(location_id),
                    "warehouse": warehouse,
                },
            )
        # Also backfills balances created before the warehouse reference existed.
        db.write("stock_balance", balance, {"quantity_milli": total, "warehouse": warehouse})
        movement = db.create(
            "stock_movement",
            {
                "key": key,
                "request": str(request_id),
                "item": str(item),
                "location": str(location_id),
                "warehouse": warehouse,
                "quantity_milli": delta,
                "posted_at": posted_at,  # received/posted by the system
                "occurred_on": occurred_on,  # slip business date, else the posting date
                "approved_by": str(approver),
            },
        )
        last_movement[item] = str(movement["id"])
    for item, level_key, level, on_hand, reorder in levels:
        if level is None:
            level = db.create(
                "stock_level", {"key": level_key, "item": str(item), "warehouse": warehouse}
            )
        db.write(
            "stock_level",
            level,
            {
                "on_hand_milli": on_hand,
                "reorder_level_milli": reorder,
                "below_reorder": on_hand < reorder,
                "last_movement": last_movement[item],
            },
        )
    _refresh_levels(db, warehouse, set(level_delta))
    _backfill_warehouse(db)
    return {"request": str(request_id), "posted": len(planned), "already": False}


def _refresh_levels(db: StockData, warehouse: str, posted: set[UUID]) -> None:
    """D-UNI-STOCK-1: keep every reorder-tracked item of this warehouse visible in stock_level.

    Creates the missing rows (never posted here, or posted before stock_level existed) and
    re-applies the item's current reorder level; writes only on change.
    """
    levels = {
        str(r["item"]): r
        for r in db.scan("stock_level", _eq("warehouse", warehouse))
        if UUID(str(r["item"])) not in posted
    }
    tracked = {
        str(r["id"]): r
        for r in db.scan("stock_item", {"field": "reorder_level_milli", "op": ">", "value": 0})
        if UUID(str(r["id"])) not in posted
    }
    for item_id in sorted(set(levels) | set(tracked)):
        item = tracked.get(item_id)
        reorder = quantity(Decimal(str(item.get("reorder_level_milli") or 0))) if item else 0
        level = levels.get(item_id)
        if level is None:
            on_hand = _opening(db, UUID(item_id), warehouse)
            level = db.create(
                "stock_level",
                {"key": f"{item_id}:{warehouse}", "item": item_id, "warehouse": warehouse},
            )
            patch = {"on_hand_milli": on_hand, "reorder_level_milli": reorder}
        else:
            on_hand = quantity(Decimal(str(level["on_hand_milli"])))
            patch = {"reorder_level_milli": reorder}
        patch["below_reorder"] = on_hand < reorder
        if level.get("on_hand_milli") is None or any(level.get(k) != v for k, v in patch.items()):
            db.write("stock_level", level, patch)


def _backfill_warehouse(db: StockData) -> None:
    """Bounded, idempotent backfill of `warehouse` on ledger rows posted before it existed."""
    for model in ("stock_movement", "stock_balance"):
        legacy = db.scan(model, {"field": "warehouse", "op": "is_null"})
        for row in list(islice(legacy, BACKFILL_BATCH)):
            location = db.get("warehouse_location", UUID(str(row["location"])))
            db.write(model, row, {"warehouse": str(location["warehouse"])})


def _opening(db: StockData, item: UUID, warehouse: str) -> int:
    """On-hand of an item in a warehouse before its stock_level row existed (older postings)."""
    total = 0
    for balance in db.scan("stock_balance", _eq("item", str(item))):
        where = balance.get("warehouse")
        if not where:
            where = db.get("warehouse_location", UUID(str(balance["location"])))["warehouse"]
        if str(where) == warehouse:
            total += quantity(Decimal(str(balance["quantity_milli"])))
    return total
