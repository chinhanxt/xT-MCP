# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tấn công định danh/giá trị: lớp 1 (validator) chặn; lớp 2 (sql.Identifier/Literal) vẫn an toàn
khi cố tình bỏ qua validator."""

from __future__ import annotations

import copy

import pytest
import sqlglot
from pydantic import ValidationError

from tests.unit.model.support import catalog, customer
from xt.adapters.db.compiler import Layout, compile_catalog, create_model_steps
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.ident import check_role
from xt.domain.model.spec import FieldSpec, ModelSpec

BAD_NAMES = [
    'x"; drop table xt_meta.catalog_version; --',
    "khách_hàng",
    "select",
    "user",
    "order",
    "a" * 41,
    "a" * 300,
    "\uff53elect",  # full-width
    "Ten",
    "ten\x00",
    "ten\n",
    "a__b",
    "1abc",
    "_x",
    "",
    "ten'--",
    "ten x",
    "ten;",
    "ten-x",
    "x_",
    "tên",
    "pg_catalog.pg_authid",
]
SYSTEM_NAMES = ["tenant_id", "id", "owner_id", "revision", "xmin", "ctid", "tableoid"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_bad_model_name_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        catalog(customer(name=name))


@pytest.mark.parametrize("name", BAD_NAMES + SYSTEM_NAMES)
def test_bad_field_name_rejected(name: str) -> None:
    m = customer()
    m["fields"][5] = {"id": "f6", "name": name, "type": "text", "label": "x"}
    with pytest.raises(ValidationError):
        catalog(m)


def test_attack_count_is_at_least_18() -> None:
    assert len(BAD_NAMES) >= 18


@pytest.mark.parametrize("sid", ["F1", "a" * 25, "x-y", "x;y", "", "\uff461", "f 1"])
def test_bad_stable_id_rejected(sid: str) -> None:
    m = customer()
    m["fields"][0]["id"] = sid
    with pytest.raises(ValidationError):
        catalog(m)


@pytest.mark.parametrize("role", ["admin') OR true OR ('", "staff,accountant", "Admin", "a b", ""])
def test_bad_role_rejected(role: str) -> None:
    m = customer(permissions={role: {"actions": ["read"], "scope": "all"}})
    with pytest.raises(ValidationError):
        catalog(m)
    with pytest.raises(ValueError):
        check_role(role)


def test_runtime_role_injection_blocked() -> None:
    from uuid import uuid4

    with pytest.raises(ValueError, match="vai trò"):
        ModelActor(TenantRef(1, uuid4()), uuid4(), ("staff,accountant",))


def test_ref_to_system_catalog_rejected() -> None:
    m = customer()
    m["fields"].append({"id": "f9", "name": "x", "type": "ref", "label": "x", "ref": "pg_authid"})
    with pytest.raises(ValidationError, match="không tồn tại"):
        catalog(m)


def test_rule_cannot_be_raw_sql() -> None:
    m = customer(rules=[{"id": "r1", "message": "x", "check": {"sql": "true; drop table x"}}])
    with pytest.raises(ValidationError):
        catalog(m)


def test_rule_referencing_system_column_rejected() -> None:
    m = customer(
        rules=[{"id": "r1", "message": "x", "check": {"field": "tenant_id", "op": "is_null"}}]
    )
    with pytest.raises(ValidationError, match="không tồn tại"):
        catalog(m)


def _statements(ddl: str) -> int:
    return len([s for s in sqlglot.parse(ddl, read="postgres") if s is not None])


def test_malicious_values_become_literals() -> None:
    evil = "x'); DROP TABLE xt_meta.catalog_version; --"
    m = customer()
    m["fields"][3]["options"].append({"value": evil, "label": "Khách'); DELETE FROM x; --"})
    m["label"] = "Khách'); DROP TABLE xt_meta.catalog_version; --"
    cat = catalog(m)
    lay = Layout(7)
    create = next(s for s in create_model_steps(lay, cat.models[0]) if s.order == 10)
    text = create.text()
    assert "'x''); DROP TABLE xt_meta.catalog_version; --'" in text
    assert _statements(text) == 1
    for s in compile_catalog(lay, cat):
        if s.text().startswith(("CREATE TABLE", "COMMENT", "ALTER TABLE")):
            assert _statements(s.text()) == 1, s.text()


def test_identifier_quoting_survives_validator_bypass() -> None:
    """Lớp 2: model_construct bỏ qua validator, sql.Identifier vẫn quote đúng → 1 câu lệnh."""
    good = catalog(customer()).models[0]
    evil = 'x"; DROP TABLE xt_meta.catalog_version; --'
    fields = [
        FieldSpec.model_construct(**{**f.__dict__, "name": evil if i == 1 else f.name})
        for i, f in enumerate(good.fields)
    ]
    m = ModelSpec.model_construct(**{**copy.copy(good.__dict__), "name": evil, "fields": fields})
    create = next(s for s in create_model_steps(Layout(7), m) if s.order == 10).text()
    assert '"xt_t7"."x""; DROP TABLE xt_meta.catalog_version; --"' in create
    assert _statements(create) == 1
