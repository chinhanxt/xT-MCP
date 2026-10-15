# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lỗi của process engine, mỗi lỗi mang `code` theo contract lỗi chung (tool-contracts-v1 §8).

- `SpecError`: đặc tả không hợp lệ (`rule_violation`), danh sách lỗi tiếng Việt có gợi ý.
- `UnsafeExpression`: biểu thức ngoài whitelist (`invalid_request`).
- `DecisionError`: hành động bị từ chối TRƯỚC khi chạm engine, state không đổi. `code`:
  `invalid_request` (dữ liệu sai), `access_denied` (không được giao, SoD, đã nghỉ),
  `precondition_failed` (trạng thái không cho phép).
- `EngineError`: engine lỗi khi chạy; runtime đã khôi phục state trước hành động (`internal_error`).
"""

from __future__ import annotations

from typing import ClassVar


class ProcessError(Exception):
    """Gốc của mọi lỗi process. `code` ánh xạ thẳng vào `error.code` của tool."""

    default_code: ClassVar[str] = "internal_error"

    def __init__(self, message: str, *, code: str | None = None, reason: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.reason = reason


class UnsafeExpression(ProcessError, ValueError):
    default_code = "invalid_request"


class SpecError(ProcessError, ValueError):
    default_code = "rule_violation"

    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__(
            "Đặc tả quy trình không hợp lệ:\n" + "\n".join(f"  - {e}" for e in self.errors),
            reason="invalid_process_spec",
        )


class DecisionError(ProcessError, ValueError):
    default_code = "precondition_failed"


class EngineError(ProcessError, RuntimeError):
    default_code = "internal_error"
