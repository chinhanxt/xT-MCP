# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`SingleUseStore` trong bộ nhớ: cho test và chạy một tiến trình. Prod dùng bản Postgres
(UNIQUE(jti), cùng transaction với hành động; DPoP: `xt.adapters.db.dpop_replay`).

Dọn khóa hết hạn bằng heap theo hạn (DPA-04): mỗi `consume` chỉ pop các khóa đã hết hạn ở đỉnh
heap, O(log n) khấu hao, không quét toàn bộ dict dưới lock.
"""

from __future__ import annotations

import heapq
import threading
import time
from collections.abc import Callable


class InMemorySingleUseStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._used: dict[str, int | None] = {}
        self._expiry: list[tuple[int, str]] = []  # (hạn, khóa); khóa None hạn không vào heap
        self._lock = threading.Lock()
        self._clock = clock

    def _evict(self, now: float) -> None:
        heap = self._expiry
        while heap and heap[0][0] <= now:
            exp, key = heapq.heappop(heap)
            if self._used.get(key, -1) == exp:  # bỏ qua mục cũ của khóa đã được thay
                del self._used[key]

    def consume(self, key: str, expires_at: int | None) -> bool:
        with self._lock:
            # Token/biên nhận/proof hết hạn bị từ chối trước khi tới đây nên dọn là an toàn.
            self._evict(self._clock())
            if key in self._used:
                return False
            self._used[key] = expires_at
            if expires_at is not None:
                heapq.heappush(self._expiry, (expires_at, key))
            return True

    def __len__(self) -> int:
        return len(self._used)
