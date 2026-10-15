# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chuẩn hóa thuộc tính bản ghi đọc từ DB trước khi đánh giá (datetime, date, Decimal)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal
from typing import Any


def normalize_value(v: Any) -> Any:
    """datetime có múi giờ -> ISO-8601 UTC; date -> ISO; Decimal hữu hạn -> int/float.

    Ném `ValueError` với giá trị không chuẩn hóa được (datetime không múi giờ, Decimal NaN/Inf):
    caller coi là thuộc tính không hợp lệ (fail-closed).
    """
    if isinstance(v, dt.datetime):
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("datetime không có múi giờ")
        return v.astimezone(dt.UTC).isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise ValueError("Decimal không hữu hạn")
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def json_default(v: Any) -> Any:
    """`default=` của json.dumps cho fingerprint, khớp `normalize_value`."""
    out = normalize_value(v)
    if out is v:
        raise TypeError(f"không tuần tự hóa được {type(v).__name__}")
    return out


def normalize_resource(resource: Mapping[str, Any]) -> dict[str, Any]:
    """Bản sao với mọi giá trị đã chuẩn hóa. Ném `ValueError` kèm tên khóa lỗi."""
    out: dict[str, Any] = {}
    for k, v in resource.items():
        try:
            out[k] = normalize_value(v)
        except ValueError as e:
            raise ValueError(str(k)) from e
    return out
