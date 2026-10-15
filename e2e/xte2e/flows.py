# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dữ liệu nghiệp vụ dùng chung cho kịch bản (gói de-xuat-phe-duyet, model advance_request)."""

from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from typing import Any

# Bình Minh: người đề nghị (Kinh doanh), trưởng phòng Kinh doanh, giám đốc, kế toán.
REQUESTER, REQUESTER2, REQUESTER3 = "NV-0013", "NV-0014", "NV-0020"
DEPT_HEAD, DEPT_HEAD_ACCT, DIRECTOR, ACCOUNTANT = "NV-0010", "NV-0004", "NV-0001", "NV-0005"
A, B = "binhminh", "hoamai"
# Hoa Mai: chủ cửa hàng (giám đốc), trưởng nhóm Bán hàng, nhân viên Bán hàng.
B_DIRECTOR, B_HEAD, B_EMPLOYEE = "NV-0001", "NV-0004", "NV-0007"


def advance(amount: int, **over: Any) -> dict[str, Any]:
    start = date.today() + timedelta(days=4)
    v: dict[str, Any] = {
        "prepared_on": date.today().isoformat(),
        "amount": amount,
        "trip_start": start.isoformat(),
        "trip_end": (start + timedelta(days=2)).isoformat(),
        "destination": "Đà Nẵng",
        "settlement_due": (start + timedelta(days=12)).isoformat(),
        "payout_method": "cash",
        "settled": False,
    }
    v.update(over)
    return v


def key(prefix: str) -> str:
    return f"e2e-{prefix}-{secrets.token_hex(4)}"


def dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def err(env: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = env.get("error") or {}
    return e
