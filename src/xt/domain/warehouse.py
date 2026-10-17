# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Warehouse quantities are integer thousandths; no floating-point stock arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from xt.domain.model.errors import ModelError

MAX_QUANTITY = 9_000_000_000_000_000
MAX_LINES = 100


def invalid(message: str) -> ModelError:
    return ModelError("rule_violation", message, details={"reason": "warehouse_validation"})


def quantity(value: object, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise invalid("Số lượng phải là số nguyên theo đơn vị phần nghìn.")
    if isinstance(value, Decimal) and (not value.is_finite() or value != value.to_integral_value()):
        raise invalid("Số lượng phải là số nguyên theo đơn vị phần nghìn.")
    number = int(value)
    if not (1 if positive else 0) <= number <= MAX_QUANTITY:
        raise invalid("Số lượng nằm ngoài phạm vi cho phép.")
    return number


@dataclass(frozen=True)
class StockLine:
    item: UUID
    location: UUID
    quantity_milli: int

    @property
    def key(self) -> str:
        return f"{self.item}:{self.location}"


def parse_lines(raw: object) -> tuple[StockLine, ...]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LINES:
        raise invalid(f"Phiếu cần từ 1 đến {MAX_LINES} dòng hàng.")
    result = []
    seen = set()
    for line in raw:
        if not isinstance(line, dict) or set(line) != {"item", "location", "quantity_milli"}:
            raise invalid("Dòng hàng cần item, location và quantity_milli.")
        try:
            item, location = UUID(str(line["item"])), UUID(str(line["location"]))
        except (ValueError, TypeError):
            raise invalid("Mã hàng và vị trí phải là UUID hợp lệ.") from None
        entry = StockLine(item, location, quantity(line["quantity_milli"], positive=True))
        if entry.key in seen:
            raise invalid("Không được lặp lại cùng mã hàng và vị trí trong phiếu.")
        seen.add(entry.key)
        result.append(entry)
    return tuple(result)


def after_post(current: object, delta: int) -> int:
    result = quantity(current) + delta
    if result < 0:
        raise ModelError(
            "precondition_failed",
            "Không đủ tồn kho để xuất.",
            details={"reason": "insufficient_stock"},
        )
    return quantity(result)
