# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Log lỗi không chứa dữ liệu (audit R2-L3): chỉ kiểu ngoại lệ + chuỗi vị trí `file:dòng:hàm`.

`str(exc)` có thể chứa DSN, CCCD, DETAIL của Postgres (`Failing row contains (…)`); traceback
mặc định in nó. Ở đây không in message, không in locals; tra cứu theo `trace_id`.
"""

from __future__ import annotations

import logging
import os
import traceback


def frames(exc: BaseException, limit: int = 12) -> str:
    tb = traceback.extract_tb(exc.__traceback__)[-limit:]
    return " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in reversed(tb))


def log_failure(logger: logging.Logger, what: str, exc: BaseException, **ids: object) -> None:
    chain: list[str] = []
    cur: BaseException | None = exc
    while cur is not None and len(chain) < 4:
        chain.append(type(cur).__name__)
        cur = cur.__cause__ or cur.__context__
    tags = " ".join(f"{k}={v}" for k, v in ids.items())
    logger.error("%s %s exc=%s at %s", what, tags, "<-".join(chain), frames(exc))
