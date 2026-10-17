# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Validation recovery describes catalog fields without inventing values or exposing defaults."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from tests.unit.model.support import catalog, customer
from xt.domain.model.spec import ModelSpec
from xt.domain.model.values import build_values_model, translate_validation


def failure(spec: ModelSpec, values: dict) -> dict:
    with pytest.raises(ValidationError) as caught:
        build_values_model(spec, "create").model_validate(values)
    return translate_validation(spec, caught.value, "create").details


def test_recovery_explains_enum_and_optional_reference_from_real_catalog() -> None:
    model = customer()
    model["fields"].append(
        {
            "id": "parent",
            "name": "parent",
            "label": "Công ty mẹ",
            "type": "ref",
            "ref": "customer",
        }
    )
    spec = catalog(model).models[0]
    details = failure(
        spec, {"code": "KH0001", "full_name": "An", "tier": "wrong", "parnet": "Parent"}
    )
    fields = {item["field"]: item for item in details["field_contracts"]}
    assert fields["tier"]["type"] == "enum"
    assert {item["value"] for item in fields["tier"]["options"]} == {"basic", "gold"}
    assert fields["parent"]["type"] == "ref" and fields["parent"]["ref"] == "customer"
    assert fields["parent"]["required"] is False
    assert all("default" not in item for item in fields.values())
    assert "Parent" not in json.dumps(details["field_contracts"])


def test_large_schema_is_explicitly_omitted_from_bounded_recovery_metadata() -> None:
    model = customer()
    field = next(field for field in model["fields"] if field["name"] == "tier")
    field["options"] = [{"value": f"v{i:03}", "label": "Label" * 20} for i in range(200)]
    field["default"] = "v000"
    details = failure(
        catalog(model).models[0], {"code": "KH0001", "full_name": "An", "tier": "wrong"}
    )
    assert "tier" in details["field_contracts_omitted"]
    assert len(json.dumps(details["field_contracts"], ensure_ascii=False).encode()) <= 16384
