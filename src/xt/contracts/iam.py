# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hợp đồng IAM cho bề mặt tool: JSON Schema của policy (agent đề xuất policy dưới dạng dữ liệu)
và dạng `details` của lỗi `access_denied` (tool-contracts-v1 §8)."""

from __future__ import annotations

from typing import Any

from pydantic import JsonValue

from xt.domain.iam import Explanation, policy_json_schema

__all__ = ["access_denied_details", "explanation_json_schema", "policy_json_schema"]


def explanation_json_schema() -> dict[str, Any]:
    schema = Explanation.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "xT IAM explanation v1"
    return schema


def access_denied_details(explanation: Explanation, explain_ref: str) -> dict[str, JsonValue]:
    """`details` gọn cho agent: lý do, tầng chặn, statement quyết định và gợi ý near-miss.

    Vết đầy đủ (`traces`) chỉ lấy qua `get_history(view="explain", ref=explain_ref)`.
    """
    return {
        "reason": explanation.reason,
        "stage": explanation.stage,
        "statements": list(explanation.statements),
        "near_misses": list(explanation.near_misses),
        "detail": dict(explanation.detail),
        "explain_ref": explain_ref,
    }
