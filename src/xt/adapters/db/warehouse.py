# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Serialize posting per tenant inside the caller's existing transaction."""

from __future__ import annotations

from xt.adapters.db.confirmations import PgUnitOfWork
from xt.domain.model.context import ModelActor


def lock_stock(uow: PgUnitOfWork, actor: ModelActor) -> None:
    # Namespace separate from catalog/audit locks. No connection or transaction of our own:
    # releasing this lock commits/rolls back approval, movements and balances together.
    if uow.actor.tenant != actor.tenant:
        raise RuntimeError("warehouse transaction tenant mismatch")
    uow.current_cursor.execute(
        "SELECT pg_advisory_xact_lock(%s, %s)", (0x58545748, actor.tenant.seq)
    )
