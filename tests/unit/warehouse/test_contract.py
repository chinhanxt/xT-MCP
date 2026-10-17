# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Generic tools accept bounded table rows for model JSON fields."""

from xt.contracts.tools.catalog import BY_NAME
from xt.contracts.tools.validation import input_issues


def test_json_lines_use_generic_create_contract():
    args = {
        "model_id": "stock_request",
        "values": {"lines": [{"item": "id", "location": "id", "quantity_milli": 1000}]},
        "idempotency_key": "warehouse-contract",
    }
    contract = BY_NAME["create_record"]
    assert input_issues(contract, args) == []
    for lines in (
        [{"item": {"nested": 1}}],
        [{"item": [1]}],
        [{}] * 101,
        [{f"field_{i}": i for i in range(201)}],
        [{"invalid key": 1}],
    ):
        assert input_issues(contract, {**args, "values": {"lines": lines}})
