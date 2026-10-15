# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Phân loại SAFE / RISKY / DESTRUCTIVE / BLOCKED của planner (thuần, probe giả lập)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.model.support import PACKS, bump, catalog, customer, field, model, pack_models
from xt.adapters.db.compiler import Layout, compile_plan
from xt.domain.model.plan import (
    CheckViolations,
    Duplicates,
    NullCount,
    PlanRejected,
    ProbeResult,
    RiskClass,
    RowCount,
    build_plan,
    check_gate,
    diff_catalogs,
)
from xt.domain.model.spec import Catalog

S, R, D, B = RiskClass.SAFE, RiskClass.RISKY, RiskClass.DESTRUCTIVE, RiskClass.BLOCKED


def classes(
    old: Catalog, edit: Callable[[dict[str, Any]], None], rows: int = 0, viol: int = 0
) -> dict[str, RiskClass]:
    new = bump(old, edit)
    results = {}
    for c in diff_catalogs(old, new):
        if isinstance(c.probe, RowCount):
            results[c.id] = ProbeResult(rows)
        elif isinstance(c.probe, NullCount | CheckViolations | Duplicates):
            results[c.id] = ProbeResult(viol, ("id1",) if viol else ())
    plan = build_plan(old, new, results)
    return {c.id: c.cls for c in plan.changes}


@pytest.fixture
def base() -> Catalog:
    return catalog(customer())


def test_new_catalog_all_safe() -> None:
    plan = build_plan(None, catalog(*pack_models(*PACKS)))
    assert plan.summary() == {"SAFE": 19, "RISKY": 0, "DESTRUCTIVE": 0, "BLOCKED": 0}


def test_add_nullable_field_safe(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        model(d, "customer")["fields"].append(
            {"id": "f9", "name": "phone", "type": "text", "label": "SĐT"}
        )

    got = classes(base, e)
    assert got["add_field:m1:f9"] is S
    assert got["grants:m1"] is S  # cấp lại quyền cột cho cột mới


def test_add_required_field(base: Catalog) -> None:
    def with_default(d: dict[str, Any]) -> None:
        model(d, "customer")["fields"].append(
            {
                "id": "f9",
                "name": "region",
                "type": "text",
                "label": "Vùng",
                "required": True,
                "default": "north",
            }
        )

    def no_default(d: dict[str, Any]) -> None:
        model(d, "customer")["fields"].append(
            {"id": "f9", "name": "region", "type": "text", "label": "Vùng", "required": True}
        )

    assert classes(base, with_default, rows=27)["add_field:m1:f9"] is R
    assert classes(base, no_default, rows=0)["add_field:m1:f9"] is R
    assert classes(base, no_default, rows=27)["add_field:m1:f9"] is B


def test_rename_field_by_stable_id_is_destructive_not_drop_add(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        field(d, "customer", "email")["name"] = "contact_email"

    got = classes(base, e)
    assert got == {"rename_field:m1:f3": D}
    new = bump(base, e)
    plan = build_plan(base, new, {})
    sql = [cs.step.text() for cs in compile_plan(Layout(1), plan, base, new)]
    assert any('RENAME COLUMN "email" TO "contact_email"' in s for s in sql)
    assert not any("DROP COLUMN" in s or "ADD COLUMN" in s for s in sql)


def test_swap_names_blocked(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        a, b = field(d, "customer", "email"), field(d, "customer", "note")
        a["name"], b["name"] = "note", "email"

    got = classes(base, e)
    assert got["rename_field:m1:f3"] is B and got["rename_field:m1:f6"] is B


def test_drop_field_and_model_destructive(base: Catalog) -> None:
    def drop_field(d: dict[str, Any]) -> None:
        model(d, "customer")["fields"].pop(5)

    assert classes(base, drop_field, rows=4)["drop_field:m1:f6"] is D
    two = catalog(customer(), customer(id="m2", name="lead", label="Tiềm năng"))

    def drop_model(d: dict[str, Any]) -> None:
        d["models"].pop(1)

    assert classes(two, drop_model)["drop_model:m2"] is D

    def rename_model(d: dict[str, Any]) -> None:
        model(d, "lead")["name"] = "prospect"

    assert classes(two, rename_model)["rename_model:m2"] is D


def test_retype_blocked(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        f = field(d, "customer", "note")
        f["type"] = "number"

    assert classes(base, e)["retype_field:m1:f6"] is B


def test_tighten_and_loosen_checks(base: Catalog) -> None:
    def tighten(d: dict[str, Any]) -> None:
        field(d, "customer", "full_name")["max"] = 50

    def loosen(d: dict[str, Any]) -> None:
        field(d, "customer", "full_name")["max"] = 200

    def enum_grow(d: dict[str, Any]) -> None:
        field(d, "customer", "tier")["options"].append({"value": "vip", "label": "VIP"})

    def enum_shrink(d: dict[str, Any]) -> None:
        field(d, "customer", "tier")["options"].pop()

    assert classes(base, tighten, viol=0)["check:m1:ck_m1_f2_max"] is R
    assert classes(base, tighten, viol=2)["check:m1:ck_m1_f2_max"] is B
    assert classes(base, loosen)["check:m1:ck_m1_f2_max"] is S
    assert classes(base, enum_grow)["check:m1:ck_m1_f4_enum"] is S
    assert classes(base, enum_shrink, viol=1)["check:m1:ck_m1_f4_enum"] is B


def test_require_unique_index(base: Catalog) -> None:
    def require(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["required"] = True

    def unique(d: dict[str, Any]) -> None:
        field(d, "customer", "email")["unique"] = True

    def uindex(d: dict[str, Any]) -> None:
        model(d, "customer")["indexes"].append(
            {"id": "i2", "fields": ["email", "tier"], "unique": True}
        )

    def plain_index(d: dict[str, Any]) -> None:
        model(d, "customer")["indexes"].append({"id": "i2", "fields": ["email"]})

    assert classes(base, require, viol=0)["require_field:m1:f6"] is R
    assert classes(base, require, viol=3)["require_field:m1:f6"] is B
    assert classes(base, unique, viol=2)["unique_field:m1:f3"] is B
    assert classes(base, uindex, viol=0)["index:m1:i2"] is R
    assert classes(base, plain_index)["index:m1:i2"] is S


def test_pii_downgrade_is_risky_upgrade_safe() -> None:
    m = customer()
    m["fields"][4]["pii"] = "sensitive"
    base = catalog(m)

    def down(d: dict[str, Any]) -> None:
        field(d, "customer", "credit")["pii"] = "basic"

    def up(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["pii"] = "sensitive"

    assert classes(base, down)["pii:m1"] is R
    assert classes(base, up)["pii:m1"] is S
    new = bump(base, down)
    plan = build_plan(base, new, {})
    sql = [cs.step.text() for cs in compile_plan(Layout(1), plan, base, new)]
    # trigger cài lại qua xt_audit.install (CREATE OR REPLACE), không còn cột sensitive
    assert "SELECT xt_audit.install('xt_t1', 'customer', '{}'::text[])" in sql


def test_managed_by_removed_is_risky() -> None:
    m = customer()
    m["fields"][3]["managed_by"] = "workflow"
    base = catalog(m)

    def free(d: dict[str, Any]) -> None:
        field(d, "customer", "tier")["managed_by"] = None

    def manage(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["managed_by"] = "workflow"

    assert classes(base, free)["grants:m1"] is R
    assert classes(base, manage)["grants:m1"] is S


def test_permission_widen_risky_narrow_safe(base: Catalog) -> None:
    def widen(d: dict[str, Any]) -> None:
        model(d, "customer")["permissions"]["sales"]["scope"] = "team"

    def narrow(d: dict[str, Any]) -> None:
        model(d, "customer")["permissions"]["manager"]["actions"] = ["read"]
        model(d, "customer")["permissions"]["sales"]["actions"] = ["read"]

    assert classes(base, widen)["permission:m1"] is R
    assert classes(base, narrow)["permission:m1"] is S


def test_labels_and_defaults_safe(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        model(d, "customer")["label"] = "Khách"
        field(d, "customer", "note")["label"] = "Ghi chú nội bộ"
        field(d, "customer", "tier")["default"] = "gold"

    assert set(classes(base, e).values()) == {S}


def test_noop_has_no_changes(base: Catalog) -> None:
    assert classes(base, lambda d: None) == {}


def test_version_must_increment(base: Catalog) -> None:
    with pytest.raises(ValueError, match="version"):
        build_plan(base, base)


def test_gate(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        model(d, "customer")["fields"].pop(5)

    new = bump(base, e)
    plan = build_plan(base, new, {"drop_field:m1:f6": ProbeResult(3)})
    with pytest.raises(PlanRejected) as ex:
        check_gate(plan, "sha256:" + "0" * 64, set())
    assert ex.value.reason == "plan_changed"
    with pytest.raises(PlanRejected) as ex:
        check_gate(plan, plan.hash, set())
    assert ex.value.reason == "confirmation_missing"
    check_gate(plan, plan.hash, {"drop_field:m1:f6"})
    assert "3 bản ghi" in plan.render_vi()


def test_plan_hash_changes_with_spec(base: Catalog) -> None:
    def a(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["label"] = "A"

    def b(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["label"] = "B"

    assert build_plan(base, bump(base, a)).hash != build_plan(base, bump(base, b)).hash


def test_rename_sensitive_field_regenerates_audit_trigger_with_new_name() -> None:
    """AUD-01: trigger audit nhận tên cột sensitive theo catalog MỚI trong cùng migration."""
    m = customer()
    m["fields"][4]["pii"] = "sensitive"
    base = catalog(m)

    def e(d: dict[str, Any]) -> None:
        field(d, "customer", "credit")["name"] = "credit_limit"

    got = classes(base, e)
    assert got["rename_field:m1:f5"] is D and got["pii:m1"] is S
    new = bump(base, e)
    sql = [cs.step.text() for cs in compile_plan(Layout(1), build_plan(base, new, {}), base, new)]
    trig = [s for s in sql if "xt_audit.install(" in s]
    assert trig == ["SELECT xt_audit.install('xt_t1', 'customer', '{credit_limit}'::text[])"]
    assert not any("DROP TRIGGER" in s or "CREATE TRIGGER xt_audit" in s for s in sql)


def test_any_touched_model_gets_fresh_audit_trigger(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        field(d, "customer", "note")["label"] = "Ghi chú mới"

    new = bump(base, e)
    sql = [cs.step.text() for cs in compile_plan(Layout(1), build_plan(base, new, {}), base, new)]
    assert any(s.startswith("SELECT xt_audit.install('xt_t1', 'customer'") for s in sql)


def test_backfill_is_phase2_data_step_never_rehearsed(base: Catalog) -> None:
    def e(d: dict[str, Any]) -> None:
        field(d, "customer", "note").update(required=True, default="n/a")

    new = bump(base, e)
    steps = compile_plan(Layout(1), build_plan(base, new, {}), base, new)
    data = [cs.step for cs in steps if cs.step.data]
    assert len(data) == 1 and data[0].phase == "backfill" and "LIMIT 1000" in data[0].text()
    assert not any(cs.step.phase == "tx" and cs.step.text().startswith("UPDATE") for cs in steps)
