# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm input theo `input_schema` đầy đủ (Draft 2020-12). Tham số lạ bị từ chối (object đóng)."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, ValidationError, validators

from xt.contracts.tools.catalog import BY_NAME
from xt.contracts.tools.contract import ToolContract

MAX_ISSUES = 10


_DOLLAR = re.compile(r"(?<!\\)\$")


@cache
def ecma_pattern(pattern: str) -> re.Pattern[str]:
    r"""`$` của ECMA-262 chỉ khớp cuối chuỗi; `$` của Python còn khớp trước `\n` cuối. Dịch mọi
    `$` không escape thành `\Z` để `"task:t1\n"` không lọt qua `^...$` (M5)."""
    return re.compile(_DOLLAR.sub(r"\\Z", pattern), re.ASCII)  # \d chỉ ASCII (R2-L2)


def _pattern(
    validator: Any, pattern: str, instance: Any, schema: Mapping[str, Any]
) -> Iterator[ValidationError]:
    if validator.is_type(instance, "string") and not ecma_pattern(pattern).search(instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


StrictValidator = validators.extend(Draft202012Validator, {"pattern": _pattern})  # type: ignore[no-untyped-call]
"""Draft 2020-12 với `pattern` theo ngữ nghĩa ECMA-262; dùng cho mọi schema input (kể cả
`values_schema` của model engine)."""


@cache
def _validator(name: str) -> Any:
    contract = BY_NAME[name]
    schema = dict(contract.input_schema)
    Draft202012Validator.check_schema(schema)
    return StrictValidator(schema)


def input_issues(contract: ToolContract, args: Mapping[str, Any]) -> list[dict[str, str]]:
    """Danh sách lỗi gọn `{path, message}` (tối đa 10), rỗng nếu hợp lệ.

    `message` của jsonschema có thể echo giá trị agent gửi; agent đã có giá trị đó nên không rò
    dữ liệu mới, nhưng vẫn cắt ngắn để không phình context.
    """
    v = _validator(contract.name)
    issues: list[dict[str, str]] = []
    for err in sorted(v.iter_errors(dict(args)), key=lambda e: list(map(str, e.absolute_path))):
        path = "/".join(str(p) for p in err.absolute_path)
        msg = err.message if len(err.message) <= 200 else err.message[:197] + "..."
        issues.append({"path": "/" + path if path else "/", "message": msg})
        if len(issues) >= MAX_ISSUES:
            break
    return issues
