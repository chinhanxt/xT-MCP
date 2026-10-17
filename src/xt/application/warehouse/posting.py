# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Posting runs under a tenant lock and an outer approval transaction, never commits itself."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from xt.domain.warehouse import after_post, invalid, parse_lines


class StockData(Protocol):
    def get(self, model: str, rid: UUID) -> dict[str, Any]: ...
    def rows(self, model: str, where: Mapping[str, Any]) -> list[dict[str, Any]]: ...
    def create(self, model: str, values: Mapping[str, Any]) -> dict[str, Any]: ...
    def write(
        self, model: str, row: Mapping[str, Any], patch: Mapping[str, Any]
    ) -> dict[str, Any]: ...


def post_stock(db: StockData, request_id: UUID, approver: UUID) -> dict[str, Any]:
    row = db.get("stock_request", request_id)
    lines = parse_lines(row.get("lines"))
    if str(row["owner_id"]) == str(approver):
        raise invalid("Người tạo phiếu không được duyệt phiếu của mình.")
    existing = db.rows("stock_movement", {"field": "request", "op": "=", "value": str(request_id)})
    if existing:
        expected = {f"{request_id}:{i}" for i in range(len(lines))}
        if {r["key"] for r in existing} != expected:
            raise invalid("Sổ biến động không đầy đủ; cần đối soát trước khi thử lại.")
        if row["state"] not in ("in_review", "approved"):
            raise invalid("Trạng thái phiếu không khớp sổ kho.")
        return {"request": str(request_id), "posted": len(lines), "already": True}
    if row["state"] != "in_review":
        raise invalid("Chỉ ghi nhận phiếu đang chờ duyệt.")
    if row["kind"] not in ("receive", "issue"):
        raise invalid("Loại phiếu không hợp lệ.")
    db.get("warehouse", UUID(str(row["warehouse"])))
    planned = []
    for i, line in enumerate(lines):
        db.get("stock_item", line.item)
        location = db.get("warehouse_location", line.location)
        if str(location["warehouse"]) != str(row["warehouse"]):
            raise invalid("Vị trí không thuộc kho của phiếu.")
        delta = line.quantity_milli * (1 if row["kind"] == "receive" else -1)
        balances = db.rows("stock_balance", {"field": "key", "op": "=", "value": line.key})
        balance = balances[0] if balances else None
        total = after_post(Decimal(str(balance["quantity_milli"])) if balance else 0, delta)
        planned.append((i, line, delta, balance, total))
    posted_at = datetime.now(UTC).isoformat()
    # Validate every line before the first write. Outer UoW also rolls back late DB failures.
    for i, line, delta, balance, total in planned:
        if balance is None:
            balance = db.create(
                "stock_balance",
                {"key": line.key, "item": str(line.item), "location": str(line.location)},
            )
        db.write("stock_balance", balance, {"quantity_milli": total})
        db.create(
            "stock_movement",
            {
                "key": f"{request_id}:{i}",
                "request": str(request_id),
                "item": str(line.item),
                "location": str(line.location),
                "quantity_milli": delta,
                "posted_at": posted_at,
                "approved_by": str(approver),
            },
        )
    return {"request": str(request_id), "posted": len(lines), "already": False}
