# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.unit.model.support import PACKS, catalog, customer, pack_models
from xt.domain.model.spec import (
    Catalog,
    SpecFormatError,
    catalog_json_schema,
    check_regex,
    model_spec_json_schema,
    parse_catalog_json,
    suggest_pii,
)


def test_all_pack_models_validate() -> None:
    raw = pack_models(*PACKS)
    cat = catalog(*raw)
    # đếm theo dữ liệu gói, không đếm cứng (gói được sửa thì test vẫn đúng)
    assert len(cat.models) == len(raw) > 0
    assert sum(len(m.fields) for m in cat.models) == sum(len(m["fields"]) for m in raw)


def test_roundtrip_is_stable() -> None:
    cat = catalog(*pack_models(*PACKS))
    again = Catalog.model_validate(json.loads(json.dumps(cat.dump())))
    assert again.digest() == cat.digest()
    assert again == Catalog.model_validate(again.dump())


def test_extra_keys_forbidden_everywhere() -> None:
    m = customer(extra=1)
    with pytest.raises(ValidationError, match="Extra inputs"):
        catalog(m)
    m = customer()
    m["fields"][0]["sql"] = "x"
    with pytest.raises(ValidationError, match="Extra inputs"):
        catalog(m)
    m = customer(permissions={"sales": {"actions": ["read"], "scope": "all", "x": 1}})
    with pytest.raises(ValidationError):
        catalog(m)


def test_json_only_input() -> None:
    good = json.dumps({"version": 1, "models": [customer()]})
    assert parse_catalog_json(good).models[0].name == "customer"
    with pytest.raises(SpecFormatError, match="YAML"):
        parse_catalog_json("version: 1\nmodels: []\n")
    with pytest.raises(SpecFormatError, match="trùng"):
        parse_catalog_json('{"version": 1, "version": 2, "models": []}')
    with pytest.raises(SpecFormatError, match="NaN"):
        parse_catalog_json('{"version": 1, "models": [], "x": NaN}')


def test_floats_parse_as_decimal() -> None:
    m = customer()
    m["fields"].append({"id": "f9", "name": "rate", "type": "number", "label": "Tỷ lệ", "max": 0.1})
    cat = parse_catalog_json(
        json.dumps({"version": 1, "models": [m]}).replace('"max": 0.1', '"max": 0.1')
    )
    f = cat.models[0].field("rate")
    assert f.coerced("max") == Decimal("0.1")


@pytest.mark.parametrize(
    "pattern",
    [
        "(a)\\1",
        "(?=a)b",
        "(?<!a)b",
        "\\d{3}",
        "\\w+",
        "[[:alpha:]]+",
        "(?i)abc",
        "a{1000}",
        "(?P<x>a)",
        "\\x41",
        "\\u0041",
        "(a+)+",
        "([a-z]*[a-z]*){200}",
        "a+?",
        "a{2",
        "[a-z",
        "(ab",
        "^[a-c&&b]$",
        "^[a[b]c]$",
        "[a--b]",
        "[a~~b]",
        "^a.b$",
        "(a{255}){255}",
        "(ab{2}){3}",
        "^[A-Z]{2}{2}$",
        "^a$+",
        "^{2}a",
        "#*{0,}",
        "a+{1,3}",
        "(|*)",
    ],
)
def test_regex_outside_common_subset_rejected(pattern: str) -> None:
    with pytest.raises(ValueError):
        check_regex(pattern)


def test_regex_engine_is_linear_not_python_re() -> None:
    """Pattern bùng nổ backtracking ở Python `re` được nhận (engine Rust tuyến tính) và CRUD chạy
    nhanh trên chuỗi dài."""
    from xt.domain.model.values import build_values_model

    m = customer()
    m["fields"][5]["regex"] = "^(a|aa)+$"  # Python re: bùng nổ backtracking
    spec = catalog(m).models[0]
    vm = build_values_model(spec, "update")
    t = time.perf_counter()
    with pytest.raises(ValidationError):
        vm.model_validate({"note": "a" * 5000 + "b"})
    assert time.perf_counter() - t < 0.5


def test_field_rules() -> None:
    def bad(**f: object) -> None:
        m = customer()
        m["fields"][5] = {"id": "f6", "name": "note", "type": "text", "label": "x", **f}
        with pytest.raises(ValidationError):
            catalog(m)

    bad(type="enum")
    bad(options=[{"value": "a", "label": "A"}])
    bad(type="number", regex="^a$")
    bad(min=5, max=1)
    bad(type="bool", unique=True)
    bad(type="json", default="x")
    bad(type="money", min="1.5")
    bad(type="datetime", min="2026-01-01T00:00:00")  # thiếu múi giờ
    bad(managed_by="workflow", required=True)  # managed + required cần default
    bad(default="xx", max=1)
    bad(type="enum", options=[{"value": "a", "label": "A"}, {"value": "a", "label": "B"}])


def test_model_rules() -> None:
    m = customer(title_field="nope")
    with pytest.raises(ValidationError, match="title_field"):
        catalog(m)
    m = customer(indexes=[{"id": "i1", "fields": ["missing"]}])
    with pytest.raises(ValidationError):
        catalog(m)
    m = customer(
        rules=[
            {
                "id": "r1",
                "message": "x",
                "check": {"field": "code", "op": "<", "other_field": "credit"},
            }
        ]
    )
    with pytest.raises(ValidationError, match="không so sánh"):
        catalog(m)
    with pytest.raises(ValidationError, match="trùng"):
        catalog(customer(), customer())


def test_pii_suggestion_by_name_and_label() -> None:
    assert suggest_pii("base_salary", "Lương", "money") == "sensitive"
    assert suggest_pii("x", "Tình trạng sức khỏe", "text") == "sensitive"
    assert suggest_pii("national_id", "Số CCCD", "text") == "basic"
    assert suggest_pii("phone", "Điện thoại", "text") == "basic"
    assert suggest_pii("amount", "Số tiền", "money") == "none"
    assert suggest_pii("tier", "Hạng", "enum") == "none"
    cat = catalog(*pack_models("nhan-su-vong-doi"))
    emp = cat.model("employee_pii").by_name()
    assert (emp["base_salary"].pii, emp["base_salary"].pii_suggested) == ("sensitive", True)
    assert emp["national_id"].pii == "basic"
    m = customer()
    m["fields"][2]["pii"] = "none"
    assert catalog(m).models[0].field("email").pii_suggested is False


def test_json_schema_export() -> None:
    s = catalog_json_schema()
    assert s["$schema"].endswith("2020-12/schema")
    assert s["additionalProperties"] is False
    fs = s["$defs"]["FieldSpec"]
    assert fs["additionalProperties"] is False
    assert set(fs["properties"]["pii"]["enum"]) == {"none", "basic", "sensitive"}
    assert model_spec_json_schema()["title"] == "xT model spec v1"
    json.dumps(s)
