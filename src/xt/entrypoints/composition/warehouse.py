# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Warehouse operation only callable from the bound human approval workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from xt.adapters.db.warehouse import lock_stock
from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.application.process.ports import ServiceFn
from xt.application.warehouse.posting import post_stock
from xt.domain.warehouse import invalid
from xt.entrypoints.composition.pack_ops import ACTING, _Data, record_id

if TYPE_CHECKING:
    from xt.adapters.db.integration import ProcessDefinition
    from xt.entrypoints.composition.process import ProcessContext


def post_stock_operation(pc: ProcessContext, definition: ProcessDefinition) -> ServiceFn:
    def op(request_id: str, idempotency_key: str) -> dict[str, object]:
        rid = record_id(request_id, "stock_request")
        acting = ACTING.get()
        iid = idempotency_key.split(":", 1)[0]
        store = PgWorkflowStore(pc.rt.uow)
        person = pc.rt.store.person(pc.actor, pc.actor.actor_id)
        if (
            definition.process_id != "stock_approval"
            or acting is None
            or acting[0] != iid
            or store.ref_for(iid) != f"stock_request:{rid}"
            or person is None
            or not person.active
            or "warehouse_manager" not in person.roles
        ):
            raise invalid("Ghi kho cần quyết định của quản lý kho trên đúng phiếu đã gửi.")
        lock_stock(pc.rt.uow, pc.actor)
        return post_stock(_Data(pc), rid, pc.actor.actor_id)

    return op


WAREHOUSE_OPS = {"post_stock": (("request_id",), post_stock_operation)}
