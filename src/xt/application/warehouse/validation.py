# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Rules shared by MCP and HTTP record paths; references use the caller's RLS scope."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from xt.domain.model.errors import ModelError
from xt.domain.warehouse import REQUEST_KINDS, check_reversal, invalid, parse_lines, quantity

WAREHOUSE_MODELS = frozenset(
    {
        "warehouse",
        "warehouse_location",
        "stock_item",
        "stock_request",
        "stock_balance",
        "stock_movement",
    }
)


def validate_write(
    service: Any,
    actor: Any,
    model: str,
    row: Mapping[str, Any],
    *,
    previous: Mapping[str, Any] | None = None,
    workflow: bool = False,
) -> None:
    if model not in WAREHOUSE_MODELS:
        return
    if model in ("stock_balance", "stock_movement"):
        if actor.roles != ("xt_workflow",):
            raise ModelError(
                "access_denied", "Tồn kho và biến động chỉ được ghi bởi quy trình duyệt."
            )
        if previous is not None and (model == "stock_movement" or not workflow):
            raise ModelError("access_denied", "Không được sửa lịch sử kho hoặc ghi tồn trực tiếp.")
    if model == "stock_item":
        quantity(row.get("reorder_level_milli", 0))
    if model == "stock_balance":
        quantity(row.get("quantity_milli", 0))
    if (
        model == "warehouse_location"
        and previous is not None
        and str(row.get("warehouse")) != str(previous.get("warehouse"))
    ):
        raise invalid("Không được chuyển vị trí sang kho khác; hãy tạo vị trí mới.")
    if model != "stock_request":
        return
    if previous is None:
        if row.get("owner_id") is not None and str(row["owner_id"]) != str(actor.actor_id):
            raise invalid("Người tạo phải là chủ phiếu kho.")
    elif str(row.get("owner_id")) != str(previous.get("owner_id")):
        raise invalid("Không được đổi chủ phiếu kho.")
    if previous is not None:
        if previous.get("state") == "approved":
            raise invalid("Phiếu đã ghi nhận không được sửa; cần nghiệp vụ đảo hoặc điều chỉnh.")
        if not workflow and previous.get("state") not in ("draft", "returned"):
            raise invalid("Chỉ sửa được phiếu nháp hoặc phiếu được trả lại.")
    lines = parse_lines(row.get("lines"))
    if row.get("kind") not in REQUEST_KINDS:
        raise invalid("Loại phiếu phải là receive, issue hoặc reversal.")
    if (row.get("kind") == "reversal") != bool(row.get("reverses")):
        raise invalid("Phiếu đảo phải chỉ rõ phiếu gốc; phiếu khác không được có phiếu gốc.")
    service.get(actor, "warehouse", UUID(str(row["warehouse"])))
    for line in lines:
        service.get(actor, "stock_item", line.item)
        location = service.get(actor, "warehouse_location", line.location)["record"]
        if str(location["warehouse"]) != str(row["warehouse"]):
            raise invalid("Vị trí dòng hàng không thuộc kho của phiếu.")
    if row.get("kind") == "reversal":
        original = service.get(actor, "stock_request", UUID(str(row["reverses"])))["record"]
        check_reversal(dict(row), dict(original))
    if workflow and row.get("state") == "approved":
        found = service.search(
            actor,
            "stock_movement",
            {"field": "request", "op": "=", "value": str(row["id"])},
            limit=1,
        )
        if found["total"] != len(lines):
            raise invalid("Chưa ghi đủ biến động kho; không thể đánh dấu đã ghi nhận.")
