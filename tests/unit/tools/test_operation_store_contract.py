# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kho in-memory qua đúng bộ hợp đồng khóa mà adapter Postgres cũng phải qua."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests.unit.tools.fakes import MemoryOperations, MemoryTransactions
from tests.unit.tools.operation_store_contract import CHECKS, Pair


def _memory() -> Pair:
    ops = MemoryOperations(wait_seconds=2.0)
    return ops, MemoryTransactions(ops)


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.__name__)
def test_memory_operation_store_contract(check: Callable[[Callable[[], Pair]], None]) -> None:
    check(_memory)
