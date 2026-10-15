# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Poka-yoke payload CRUD (lỗi tiếng Việt), DSL biểu thức, logic 3 trị."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from tests.unit.model.support import PACKS, catalog, customer, pack_models
from xt.domain.model.errors import ModelError
from xt.domain.model.expr import And, Cmp, Not, Or, eval3, parse_rule_expr
from xt.domain.model.spec import ModelSpec
from xt.domain.model.values import (
    build_values_model,
    reject_managed,
    rule_violations,
    translate_validation,
)


def spec() -> ModelSpec:
    return catalog(customer()).models[0]


def err(m: ModelSpec, data: dict[str, Any], mode: str = "create") -> ModelError:
    with pytest.raises(ValidationError) as ex:
        build_values_model(m, mode).model_validate(data)  # type: ignore[arg-type]
    return translate_validation(m, ex.value, mode)  # type: ignore[arg-type]


def test_valid_create() -> None:
    v = build_values_model(spec(), "create").model_validate(
        {"code": "KH0001", "full_name": "An", "credit": "5000000"}
    )
    assert v.model_dump(exclude_unset=True)["credit"] == 5_000_000


def test_vietnamese_errors() -> None:
    m = spec()
    e = err(m, {"code": "X1", "tier": "silver", "credit": 1.5})
    assert e.code == "invalid_request"
    msgs = " | ".join(i["message"] for i in e.details["fields"])
    assert "'Mã KH' không đúng định dạng (mẫu ^KH[0-9]{4}$)" in msgs
    assert "'Tên' là trường bắt buộc, chưa có giá trị" in msgs
    assert "'Hạng' chỉ nhận một trong: basic (Thường), gold (Vàng)" in msgs
    assert "VND không có phần lẻ" in msgs


def test_unknown_field_suggestion() -> None:
    e = err(spec(), {"code": "KH0001", "full_name": "An", "emial": "a@b.vn"})
    assert e.details["reason"] == "unknown_field"
    assert "có phải ý bạn là 'email'?" in e.details["fields"][0]["message"]
    assert "email" in e.details["valid_fields"]


def test_update_cannot_null_required() -> None:
    e = err(spec(), {"full_name": None}, "update")
    assert "không được để trống" in e.details["fields"][0]["message"]


def test_masked_pii_rejected() -> None:
    m = customer()
    m["fields"][2]["pii"] = "basic"
    e = err(catalog(m).models[0], {"code": "KH0001", "full_name": "An", "email": "[PII:ab12]"})
    assert "[PII:" in e.details["fields"][0]["message"]


def test_money_number_types() -> None:
    m = customer()
    m["fields"].append({"id": "f9", "name": "rate", "type": "number", "label": "Tỷ lệ"})
    vm = build_values_model(catalog(m).models[0], "update")
    assert vm.model_validate({"rate": "12.5"}).model_dump(exclude_unset=True)["rate"] == Decimal(
        "12.5"
    )
    with pytest.raises(ValidationError):
        vm.model_validate({"rate": 12.5})
    with pytest.raises(ValidationError):
        vm.model_validate({"credit": True})


def test_managed_fields_excluded_and_rejected() -> None:
    cat = catalog(*pack_models(*PACKS))
    d = cat.dump()
    p = next(x for x in d["models"] if x["name"] == "proposal")
    for f in p["fields"]:
        if f["name"] in ("state", "final_approver", "approved_at"):
            f["managed_by"] = "workflow"
    m = type(cat).model_validate(d).model("proposal")
    props = build_values_model(m, "update").model_json_schema()["properties"]
    assert "state" not in props and "amount" in props
    assert "state" in build_values_model(m, "workflow").model_json_schema()["properties"]
    with pytest.raises(ModelError) as ex:
        reject_managed(m, {"state": "approved", "reason": "x"}, "proposal:1")
    assert ex.value.code == "precondition_failed"
    assert ex.value.continuation == {
        "tool": "transition_record",
        "arguments": {"ref": "proposal:1"},
    }
    assert "Trạng thái" in ex.value.message


def test_rule_three_valued_logic() -> None:
    m = catalog(*pack_models("de-xuat-phe-duyet")).model("advance_request")
    by = m.by_name()
    rule = next(r for r in m.rules if r.id == "r1")
    row = {"trip_start": date(2026, 11, 22), "trip_end": date(2026, 11, 20)}
    assert eval3(rule.check, row, by) is False
    assert eval3(rule.check, {"trip_start": date(2026, 11, 22)}, by) is None  # NULL → CHECK qua
    bad = rule_violations(m, {**row, "payout_method": "bank_transfer"})
    assert bad is not None and bad.code == "rule_violation"
    rules = {v["rule"] for v in bad.details["violations"]}
    assert rules == {"r1", "r3"}
    assert "'Ngày về' phải sau hoặc bằng 'Ngày đi'" in bad.message


def test_parse_rule_expr() -> None:
    e = parse_rule_expr("trip_end >= trip_start and (note is not null or tier in ('gold', 'vip'))")
    assert isinstance(e, And)
    assert e.and_[0] == Cmp(field="trip_end", op=">=", other_field="trip_start")
    assert isinstance(e.and_[1], Or)
    assert parse_rule_expr("not credit > 5") == Not.model_validate(
        {"not": Cmp(field="credit", op=">", value=5)}
    )
    assert parse_rule_expr("x not in (1, 2)") == Not.model_validate(
        {"not": Cmp(field="x", op="in", value=[1, 2])}
    )
    assert parse_rule_expr("a = 'x y'") == Cmp(field="a", op="=", value="x y")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "a >",
        "a = b; drop",
        "__import__('os')",
        "a = 1 or",
        "(" * 12 + "a = 1" + ")" * 12,
        "a ~ 'x'",
        "x" * 501,
    ],
)
def test_parse_rule_expr_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_rule_expr(bad)


def test_filter_depth_and_in_limits() -> None:
    e: Any = {"field": "a", "op": "=", "value": 1}
    for _ in range(9):
        e = {"not": e}
    from pydantic import TypeAdapter

    from xt.domain.model.expr import Expr, expr_fields

    with pytest.raises(ValueError, match="8 cấp"):
        expr_fields(TypeAdapter(Expr).validate_python(e))
    with pytest.raises(ValidationError):
        TypeAdapter(Expr).validate_python({"field": "a", "op": "in", "value": list(range(101))})
    with pytest.raises(ValidationError):
        TypeAdapter(Expr).validate_python({"field": "a", "op": "~", "value": "x"})
