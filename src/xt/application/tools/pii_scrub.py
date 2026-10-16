# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lưới PII cho envelope lỗi (spike-pii-lane §2 điểm 4, §3.5; audit MCP R2-M1/M2).

Engine thật echo giá trị trong lỗi (pydantic `input_value`, Postgres `Key (so_cccd)=(…)`). Lưới
chạy theo ba nguồn, theo thứ tự:
1. **Phân loại field**: khóa dict hoặc `violations[].field` là field `pii` của model -> che cả
   giá trị/thông điệp đi kèm.
2. **Giá trị đã biết** trong phạm vi lời gọi (do làn PII cung cấp) -> thay mọi lần xuất hiện.
3. **Bộ nhận dạng đã kiểm định dạng** (chỉ chữ số ASCII, sau NFKC + bỏ ký tự Cf): CCCD 12 số
   bắt đầu bằng mã tỉnh `0xx`, di động VN `(+84|0)[35789]` + 8 số, email (kể cả `%40`). Không
   che số tiền/revision (số không bắt đầu bằng 0, không có tiền tố điện thoại).

Không đụng khóa giao thức (`code`, `retryable`, `continuation`, `explain_ref`...): dispatcher
không đưa `continuation` vào lưới (args do agent tự gửi; che sẽ làm hỏng việc gọi lại).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from typing import Any

_SEP = r"[ .\-_/]?"  # NBSP đã thành space sau NFKC; thêm `_`, `/` (R3-L1)
_D = r"[0-9]"
CCCD = re.compile(rf"(?<![0-9A-Za-z])0{_D}{{2}}(?:{_SEP}{_D}){{9}}(?![0-9])", re.ASCII)
PHONE = re.compile(
    rf"(?<![0-9A-Za-z])(?:\+?84|0){_SEP}[35789](?:{_SEP}{_D}){{8}}(?![0-9])", re.ASCII
)
EMAIL = re.compile(r"[A-Za-z0-9._+-]+(?:@|%40)[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.ASCII)
# Số tài khoản/thẻ ngân hàng: 13-19 chữ số, không bắt đầu bằng 0 (tiền VND trong lỗi < 10^12).
BANK = re.compile(rf"(?<![0-9A-Za-z])[1-9](?:{_SEP}{_D}){{12,18}}(?![0-9])", re.ASCII)
RECOGNIZERS = (CCCD, PHONE, BANK, EMAIL)

# Số nguyên mất số 0 đầu khi lưu dạng int: SĐT 9 số, CCCD 11 số.
_INT_PII = re.compile(r"[35789][0-9]{8}|[0-9]{11}", re.ASCII)
# Khóa mang số nghiệp vụ, không phải định danh người.
NUMERIC_BUSINESS_KEYS = frozenset(
    {
        "amount",
        "total",
        "limit",
        "count",
        "revision",
        "current_revision",
        "expected_revision",
        "size_bytes",
        "limit_bytes",
        "retry_after_seconds",
        "open_confirmations",
        "max",
        "min",
        "quantity",
        "price",
    }
)
PROTOCOL_KEYS = frozenset({"code", "retryable", "explain_ref", "continuation"})


def normalize(text: str) -> str:
    """NFKC (chữ số fullwidth -> ASCII) và bỏ ký tự định dạng Cf (zero-width) trước khi so."""
    folded = unicodedata.normalize("NFKC", text)
    return "".join(c for c in folded if unicodedata.category(c) != "Cf")


class ErrorScrubber:
    def __init__(
        self,
        mask: Callable[[str], str],
        *,
        pii_fields: Iterable[str] = (),
        known_values: Iterable[str] = (),
    ) -> None:
        self._mask = mask
        self._fields = frozenset(pii_fields)
        known = {normalize(str(v)) for v in known_values if v is not None}
        self._known = sorted((k for k in known if len(k) >= 4), key=len, reverse=True)
        self.hits = 0

    def _hit(self, value: str) -> str:
        self.hits += 1
        return self._mask(value)

    def text(self, s: str) -> str:
        out = normalize(s)
        for k in self._known:
            if k in out:
                out = out.replace(k, self._hit(k))
        for rx in RECOGNIZERS:
            out = rx.sub(lambda m: self._hit(m.group(0)), out)
        return out

    def value(self, node: Any, key: str | None = None) -> Any:
        if key is not None and key in self._fields and node is not None:
            return self._hit(str(node)) if not isinstance(node, Mapping | list) else self._all(node)
        if isinstance(node, str):
            return self.text(node)
        if isinstance(node, bool) or node is None:
            return node
        if isinstance(node, int | float):
            if key in NUMERIC_BUSINESS_KEYS:
                return node
            num = node
            if isinstance(node, float):
                if not node.is_integer():
                    return node
                num = int(node)
            if _INT_PII.fullmatch(str(abs(num))):
                return self._hit(str(node))
            return node
        if isinstance(node, list):
            return [self.value(x) for x in node]
        if isinstance(node, Mapping):
            # Vi phạm trên field PII: thông điệp thường echo giá trị -> che toàn bộ thông điệp.
            pii_violation = isinstance(node.get("field"), str) and node["field"] in self._fields
            out: dict[str, Any] = {}
            for k, v in node.items():
                nk = self.text(k) if isinstance(k, str) and k not in PROTOCOL_KEYS else k
                if pii_violation and k in ("message", "value", "input_value"):
                    out[nk] = self._hit(str(v)) if v is not None else v
                elif k in PROTOCOL_KEYS:
                    out[nk] = v
                else:
                    out[nk] = self.value(v, k if isinstance(k, str) else None)
            return out
        return node

    def _all(self, node: Any) -> Any:
        if isinstance(node, Mapping):
            return {k: self._all(v) for k, v in node.items()}
        if isinstance(node, list):
            return [self._all(v) for v in node]
        return self._hit(str(node)) if node is not None else None

    def error(self, error: Mapping[str, Any]) -> dict[str, Any]:
        """Quét object `error` (trừ khóa giao thức)."""
        out: dict[str, Any] = dict(self.value(dict(error)))
        return out
