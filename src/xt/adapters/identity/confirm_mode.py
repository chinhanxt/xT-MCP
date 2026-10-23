# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chế độ OTP của step-up đọc từ tệp JSON dùng chung (chỉ môi trường DEMO).

`XT_CONFIRM_MODE_FILE` (mặc định không đặt => hành vi cũ, đọc env) trỏ tới tệp `{"otp": true|false}`
do dịch vụ `deploy/demo-ops` ghi. Đọc ở mỗi request, cache theo (mtime, size, inode) nên chỉ stat
một lần. Tệp thiếu/hỏng => `None` => caller dùng giá trị env (không bao giờ tự hạ mức).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

OTP_ACR = "urn:xt:acr:otp"
PWD_ACR = "urn:xt:acr:pwd"
MAX_MODE_FILE_BYTES = 4_096

_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int, int], bool | None]] = {}


def read_confirm_otp(path: Path) -> bool | None:
    """`True`/`False` theo khóa `otp` của tệp; `None` nếu tệp thiếu, quá lớn hoặc sai định dạng."""
    key = os.fspath(path)
    try:
        st = path.stat()
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    with _lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    value = _parse(path, st.st_size)
    with _lock:
        _cache[key] = (sig, value)
    return value


def _parse(path: Path, size: int) -> bool | None:
    if size > MAX_MODE_FILE_BYTES:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    otp = doc.get("otp") if isinstance(doc, dict) else None
    return otp if isinstance(otp, bool) else None


def mode_acr_amr(otp: bool) -> tuple[str, frozenset[str]]:
    return (OTP_ACR, frozenset({"otp"})) if otp else (PWD_ACR, frozenset({"pwd"}))
