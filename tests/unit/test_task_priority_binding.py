# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Priority mappings in installed task bindings must match the model enum."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from xt.domain.model.spec import ModelSpec
from xt.entrypoints.composition.packs import BindingSpec, _priority_binding_problems

ROOT = Path(__file__).resolve().parents[2]
TASK = ModelSpec.model_validate(
    json.loads((ROOT / "packages/giao-viec-sla/models/task.json").read_text())
)


@pytest.mark.parametrize(
    ("mapping", "field", "reason"),
    [
        ({"normal": "unknown"}, "priority", "không có trong enum"),
        ({"normal": "medium", "low": "medium"}, "priority", "một-một"),
        ({"normal": "medium"}, "title", "kiểu enum"),
    ],
)
def test_invalid_priority_binding(mapping: dict[str, str], field: str, reason: str) -> None:
    binding = BindingSpec.model_validate(
        {"primitive": "task", "fields": {"priority": field}, "priorities": mapping}
    )
    assert reason in " ".join(_priority_binding_problems("task", binding, TASK.by_name()))


def test_priority_binding_rejects_unknown_public_key() -> None:
    with pytest.raises(ValidationError):
        BindingSpec.model_validate({"primitive": "task", "priorities": {"unknown": "medium"}})
