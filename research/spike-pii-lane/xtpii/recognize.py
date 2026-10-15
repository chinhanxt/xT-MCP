# SPDX-License-Identifier: Apache-2.0
"""Bộ nhận dạng PII Việt Nam cho chuỗi tự do (ghi chú, lỗi, text ngoài schema).

Chỉ là lưới an toàn thứ hai; lưới thứ nhất là nhãn `pii` trên field + danh sách giá trị đã biết.
Cố ý chấp nhận dương tính giả (chuỗi 12 số bất kỳ có mã tỉnh hợp lệ bị che) trên tier cloud.
"""
from __future__ import annotations

import re

# CCCD 12 số: 3 số mã tỉnh 001..096, 1 số giới tính/thế kỷ, 2 số năm sinh, 6 số ngẫu nhiên
_CCCD = re.compile(r"(?<!\d)(0(?:0[1-9]|[1-8]\d|9[0-6]))(\d{9})(?!\d)")
# Di động VN: +84/84/0 + đầu số 3x/5x/7x/8x/9x + 7 số, cho phép dấu cách/chấm/gạch
_PHONE = re.compile(r"(?<![\d+])(?:\+84|84|0)[ .\-]?[35789]\d(?:[ .\-]?\d){7}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("national_id", _CCCD, "basic"),
    ("phone", _PHONE, "basic"),
    ("email", _EMAIL, "basic"),
]


def find(text: str) -> list[tuple[int, int, str, str]]:
    """Trả [(start, end, kind, level)] không chồng lấn, ưu tiên theo thứ tự PATTERNS."""
    spans: list[tuple[int, int, str, str]] = []
    for kind, rx, level in PATTERNS:
        for m in rx.finditer(text):
            s, e = m.span()
            if any(s < e2 and s2 < e for s2, e2, *_ in spans):
                continue
            spans.append((s, e, kind, level))
    return sorted(spans)
