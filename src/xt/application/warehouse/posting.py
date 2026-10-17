# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Posting runs under a tenant lock and an outer approval transaction, never commits itself."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from xt.domain.warehouse import (
    REQUEST_KINDS,
    after_post,
    check_reversal,
    invalid,
    parse_lines,
    reversal_key,
)


class StockData(Protocol):
    def get(self, model: str, rid: UUID) -> dict[str, Any]: ...
    def rows(self, model: str, where: Mapping[str, Any]) -> list[dict[str, Any]]: ...
    def create(self, model: str, values: Mapping[str, Any]) -> dict[str, Any]: ...
    def write(
        self, model: str, row: Mapping[str, Any], patch: Mapping[str, Any]
    ) -> dict[str, Any]: ...


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


def post_stock(db: StockData, request_id: UUID, approver: UUID) -> dict[str, Any]:
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
    db.get("warehouse", UUID(str(row["warehouse"])))
    planned = []
    for key, item, location_id, delta in sources:
        if db.rows("stock_movement", {"field": "key", "op": "=", "value": key}):
            raise invalid("Phiếu gốc đã được đảo; không đảo lần thứ hai.")
        db.get("stock_item", item)
        location = db.get("warehouse_location", location_id)
        if str(location["warehouse"]) != str(row["warehouse"]):
            raise invalid("Vị trí không thuộc kho của phiếu.")
        balance_key = f"{item}:{location_id}"
        balances = db.rows("stock_balance", {"field": "key", "op": "=", "value": balance_key})
        balance = balances[0] if balances else None
        total = after_post(Decimal(str(balance["quantity_milli"])) if balance else 0, delta)
        planned.append((key, item, location_id, delta, balance_key, balance, total))
    posted_at = datetime.now(UTC).isoformat()
    # Validate every line before the first write. Outer UoW also rolls back late DB failures.
    warehouse = str(row["warehouse"])
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
        db.create(
            "stock_movement",
            {
                "key": key,
                "request": str(request_id),
                "item": str(item),
                "location": str(location_id),
                "warehouse": warehouse,
                "quantity_milli": delta,
                "posted_at": posted_at,
                "approved_by": str(approver),
            },
        )
    return {"request": str(request_id), "posted": len(planned), "already": False}
