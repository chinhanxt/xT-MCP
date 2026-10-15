# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""HMAC cho giá trị field sensitive trong audit (G-M2), tính trong tiến trình xt-api/xt-worker.

Khóa không bao giờ vào Postgres (R2-01). Dạng chuẩn hóa của giá trị ổn định giữa giá trị app gửi
và giá trị đọc lại từ DB (Decimal bỏ số 0 thừa, datetime quy về UTC), nên người kiểm có khóa
xác minh được `hmac:<kid>:<hex>` trong audit bằng `verify_hmac(key, kid, giá_trị)`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

KEY_MIN_BYTES = 32


def audit_canon(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        s = format(value, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    if isinstance(value, datetime):
        return (value.astimezone(UTC) if value.tzinfo else value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict | list):
        return json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str
        )
    if isinstance(value, str):
        # chuỗi dạng thời điểm ISO có múi giờ: chuẩn hóa như datetime
        if len(value) >= 20 and value[10:11] == "T":
            try:
                dt = datetime.fromisoformat(value)
            except ValueError:
                return value
            if dt.tzinfo is not None:
                return audit_canon(dt)
        return value
    return str(value)


def audit_hmac(key: bytes, kid: int, value: object) -> str:
    """`hmac:<kid>:<HMAC-SHA256 cắt 128 bit>` của dạng chuẩn hóa."""
    if len(key) < KEY_MIN_BYTES:
        raise ValueError("khóa HMAC audit phải ≥ 32 byte")
    mac = hmac.new(key, audit_canon(value).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"hmac:{kid}:{mac}"


def verify_hmac(key: bytes, kid: int, value: object, stored: str) -> bool:
    return hmac.compare_digest(audit_hmac(key, kid, value), stored)


def sensitive_hmacs(
    key: bytes,
    kid: int,
    sensitive: Iterable[str],
    *,
    new: Mapping[str, Any] | None = None,
    old: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Bản đồ {"new"/"old": {cột: hmac}} cho các cột sensitive có giá trị khác NULL."""
    cols = set(sensitive)
    out: dict[str, dict[str, str]] = {}
    for part, row in (("new", new), ("old", old)):
        if not row:
            continue
        got = {c: audit_hmac(key, kid, v) for c, v in row.items() if c in cols and v is not None}
        if got:
            out[part] = got
    return out
