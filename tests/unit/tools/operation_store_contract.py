# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hợp đồng khóa của `OperationStore` + `TransactionRunner` (docs/integration-notes.md).

Mọi adapter (in-memory, Postgres) phải qua `run_operation_store_contract(new_pair)`, trong đó
`new_pair()` trả `(store, transactions)` sạch, dùng được từ nhiều thread. Bản Postgres gọi hàm này
ở `tests/integration` với hai connection thật.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from xt.application.tools import Actor, ClientClass, PersonRef
from xt.application.tools.ports import (
    IN_PROGRESS,
    OperationKey,
    OperationStore,
    PendingOperation,
    PendingStatus,
    Receipt,
    TransactionRunner,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import PrincipalKind

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
Pair = tuple[OperationStore, TransactionRunner]


def _key(n: str = "k-0000001") -> OperationKey:
    return OperationKey("acme", "agent|c|sub-viet|sub-viet", "update_record", n)


def _receipt(key: OperationKey) -> Receipt:
    return Receipt(key, "fp", "op_1", "committed", RiskLevel.L2, {"ok": 1}, NOW)


def _pending(key: OperationKey, cf: str = "cf_1") -> PendingOperation:
    return PendingOperation(
        confirmation_id=cf,
        key=key,
        tool=key.tool,
        arguments={"idempotency_key": key.idempotency_key},
        fingerprint="fp",
        actor=Actor("acme", "sub-viet", PrincipalKind.AGENT, "c", "sub-viet"),
        client_class=ClientClass.CLOUD,
        plan_digest="sha256:" + "0" * 64,
        plan_summary="x",
        level=RiskLevel.L2,
        confirmers=(PersonRef("person:viet", "Việt", "sub-viet"),),
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def _bg(fn: Callable[[], Any]) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    th = threading.Thread(target=lambda: out.append(fn()))
    th.start()
    return th, out


def claim_then_receipt_then_replay(new_pair: Callable[[], Pair]) -> None:
    store, tx = new_pair()
    k = _key()

    def first() -> Any:
        assert store.claim(k, "fp", NOW) is None
        store.save_receipt(_receipt(k))

    tx.run(first)
    got = tx.run(lambda: store.claim(k, "fp", NOW))
    assert isinstance(got, Receipt) and got.operation_ref == "op_1"


def rollback_releases_claim(new_pair: Callable[[], Pair]) -> None:
    store, tx = new_pair()
    k = _key()

    def boom() -> None:
        assert store.claim(k, "fp", NOW) is None
        raise RuntimeError("crash after mutation")

    with contextlib.suppress(RuntimeError):
        tx.run(boom)
    assert tx.run(lambda: store.claim(k, "fp", NOW)) is None


def concurrent_claim_waits_for_holder(new_pair: Callable[[], Pair]) -> None:
    store, tx = new_pair()
    k = _key()
    holding, release = threading.Event(), threading.Event()

    def holder() -> None:
        assert store.claim(k, "fp", NOW) is None
        holding.set()
        release.wait(5)
        store.save_receipt(_receipt(k))

    th, _ = _bg(lambda: tx.run(holder))
    holding.wait(5)
    th2, out = _bg(lambda: tx.run(lambda: store.claim(k, "fp", NOW)))
    time.sleep(0.1)
    assert out == []  # đang chờ, không đọc "chưa có"
    release.set()
    th.join(5)
    th2.join(5)
    assert isinstance(out[0], Receipt) or out[0] is IN_PROGRESS


def claim_waits_for_confirmation_lock(new_pair: Callable[[], Pair]) -> None:
    """R2-M3: ConfirmationService giữ `lock_pending` rồi ghi receipt; agent gọi lại đúng lúc hết
    hạn phải thấy receipt, không được ghi EXPIRED đè lên."""
    store, tx = new_pair()
    k = _key()
    op = _pending(k)

    def create() -> None:
        assert store.claim(k, "fp", NOW) is None
        store.save_pending(op)

    tx.run(create)
    locked, release = threading.Event(), threading.Event()

    def confirmer() -> None:
        cur = store.lock_pending(op.confirmation_id)
        assert cur is not None and cur.status.open
        locked.set()
        release.wait(5)
        store.save_receipt(_receipt(k))
        assert store.update_pending(replace(cur, status=PendingStatus.CONFIRMED))

    th, _ = _bg(lambda: tx.run(confirmer))
    locked.wait(5)

    def agent_retry() -> Any:
        seen = store.claim(k, "fp", NOW + timedelta(minutes=11))
        if isinstance(seen, PendingOperation):
            expired = replace(seen, status=PendingStatus.EXPIRED)
            return ("pending", store.update_pending(expired))
        return seen

    th2, out = _bg(lambda: tx.run(agent_retry))
    time.sleep(0.1)
    release.set()
    th.join(5)
    th2.join(5)
    assert isinstance(out[0], Receipt), out
    final = store.get_pending(op.confirmation_id)
    assert final is not None and final.status is PendingStatus.CONFIRMED


def update_pending_never_overwrites_closed_rows(new_pair: Callable[[], Pair]) -> None:
    store, tx = new_pair()
    k = _key("k-0000002")
    op = _pending(k, "cf_2")

    def create() -> None:
        assert store.claim(k, "fp", NOW) is None
        store.save_pending(op)

    tx.run(create)
    declined = replace(op, status=PendingStatus.DECLINED)
    assert tx.run(lambda: store.update_pending(declined)) is True
    expired = replace(op, status=PendingStatus.EXPIRED)
    assert tx.run(lambda: store.update_pending(expired)) is False
    final = store.get_pending("cf_2")
    assert final is not None and final.status is PendingStatus.DECLINED


CHECKS = (
    claim_then_receipt_then_replay,
    rollback_releases_claim,
    concurrent_claim_waits_for_holder,
    claim_waits_for_confirmation_lock,
    update_pending_never_overwrites_closed_rows,
)


def run_operation_store_contract(new_pair: Callable[[], Pair]) -> None:
    for check in CHECKS:
        check(new_pair)
