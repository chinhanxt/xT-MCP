# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`SingleUseStore` trong bộ nhớ: cho test và chạy một tiến trình. Prod dùng bản Postgres
(UNIQUE(jti), cùng transaction với hành động)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class InMemorySingleUseStore:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._used: dict[str, int | None] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def consume(self, key: str, expires_at: int | None) -> bool:
        with self._lock:
            now = self._clock()
            # Dọn khóa đã hết hạn: token/biên nhận hết hạn bị từ chối trước khi tới đây nên an toàn.
            for k in [k for k, exp in self._used.items() if exp is not None and exp <= now]:
                del self._used[k]
            if key in self._used:
                return False
            self._used[key] = expires_at
            return True

    def __len__(self) -> int:
        return len(self._used)
