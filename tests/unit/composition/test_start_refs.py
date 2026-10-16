# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""start_process: quét ref bản ghi đệ quy trong input object/mảng (PSA-03, vòng 2)."""

from __future__ import annotations

import uuid

import pytest

from xt.application.tools.errors import ToolError
from xt.entrypoints.composition.process import record_refs

MODELS = {"task", "lifecycle_case"}


def test_record_refs_nested_object_and_array() -> None:
    a, b, c = (str(uuid.uuid4()) for _ in range(3))
    value = {
        "note": "task:không-phải-uuid",
        "items": [f"task:{a}", {"deep": [f"xrn:acme:lifecycle_case:{b}"]}],
        "other": f"person:{c}",  # không phải model của catalog
        "n": 3,
    }
    got = sorted(record_refs(value, MODELS))
    assert got == sorted(
        [("task", f"task:{a}"), ("lifecycle_case", f"xrn:acme:lifecycle_case:{b}")]
    )
    assert record_refs(f"task:{a}", MODELS) == [("task", f"task:{a}")]
    assert record_refs(None, MODELS) == []


def test_record_refs_bounded() -> None:
    deep: object = "x"
    for _ in range(30):
        deep = [deep]
    with pytest.raises(ToolError):
        record_refs(deep, MODELS)
    with pytest.raises(ToolError):
        record_refs(["a"] * 2000, MODELS)
