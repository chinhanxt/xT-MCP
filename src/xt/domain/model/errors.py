# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lỗi của model engine theo bộ mã chung tool-contracts-v1 §8, thông điệp tiếng Việt."""

from __future__ import annotations

from typing import Any, Literal

ErrorCode = Literal[
    "invalid_request",
    "access_denied",
    "not_found",
    "revision_conflict",
    "rule_violation",
    "precondition_failed",
    "capability_unavailable",
    "resource_constraint",
    "timeout",
    "internal_error",
]
RETRYABLE: frozenset[str] = frozenset({"resource_constraint", "timeout", "internal_error"})


class ModelError(Exception):
    """Lỗi trả cho agent: `message` chuyển nguyên cho người dùng, `remediation` là bước tiếp."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        remediation: str = "",
        details: dict[str, Any] | None = None,
        continuation: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.remediation = remediation or _DEFAULT_REMEDIATION[code]
        self.details = details or {}
        self.continuation = continuation

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def to_error(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "remediation": self.remediation,
            "details": self.details,
        }
        if self.continuation is not None:
            out["continuation"] = self.continuation
        return out


_DEFAULT_REMEDIATION: dict[str, str] = {
    "invalid_request": "Sửa các trường nêu trong details rồi gọi lại; không đoán giá trị thay người"
    " dùng.",
    "access_denied": "Không tự thử lại; hỏi người có quyền.",
    "not_found": "Kiểm tra lại id/ref (có thể bản ghi không tồn tại hoặc ngoài phạm vi của bạn).",
    "revision_conflict": "Đọc lại bản ghi/model, cho người dùng xem thay đổi, rồi gửi lại với"
    " revision mới.",
    "rule_violation": "Sửa giá trị theo thông điệp của từng vi phạm rồi gọi lại.",
    "precondition_failed": "Dùng đúng đường đi được gợi ý trong continuation.",
    "capability_unavailable": "Tính năng chưa có ở bản này; chọn cách khác.",
    "resource_constraint": "Thu hẹp yêu cầu rồi thử lại.",
    "timeout": "Hệ thống đang bận (khóa/timeout); thử lại sau ít giây với cùng tham số.",
    "internal_error": "Thử lại một lần; nếu vẫn lỗi, báo trace_id cho quản trị.",
}
