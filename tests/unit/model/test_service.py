# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bề mặt tool (describe_model, propose_model_change) và service với port giả (không DB)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest

from tests.unit.model.support import PACKS, catalog, customer, pack_models
from xt.application.model_engine.operations import apply_operations
from xt.application.model_engine.ports import (
    ApplyOutcome,
    ConstraintViolated,
    JobStats,
    Page,
    StoredProposal,
    UpdateOutcome,
)
from xt.application.model_engine.service import ModelService, RecordService
from xt.contracts.model import Operation, ProposeModelChange, describe_model, pii_level
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.expr import Expr
from xt.domain.model.plan import Plan, build_plan, check_gate
from xt.domain.model.spec import Catalog, ModelSpec

T = TenantRef(1, uuid4())


def actor(*roles: str) -> ModelActor:
    return ModelActor(T, uuid4(), roles or ("sales",))


class FakeCatalogs:
    def __init__(self, cat: Catalog | None) -> None:
        self.cat = cat

    def current(self, actor: ModelActor) -> tuple[int, Catalog] | None:
        return (self.cat.version, self.cat) if self.cat else None


class FakeMigrator:
    def __init__(self, cats: FakeCatalogs) -> None:
        self.cats = cats
        self.applied: list[tuple[str, str]] = []

    def current(self, tenant: TenantRef) -> Catalog | None:
        return self.cats.cat

    def plan(self, tenant: TenantRef, new: Catalog, *, rehearse: bool) -> Plan:
        return build_plan(self.cats.cat, new, {})

    def apply(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        *,
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome:
        p = build_plan(self.cats.cat, new, {})
        check_gate(p, approved_hash, confirm)
        self.cats.cat = new
        self.applied.append((proposed_by, approved_by))
        return ApplyOutcome(tenant.seq, new.version, p.hash, 1, 0)

    def run_jobs(self, tenant_seq: int | None = None) -> JobStats:
        return JobStats()

    def post_state(self, tenant: TenantRef) -> tuple[int, str] | None:
        return (self.cats.cat.version, "validated") if self.cats.cat else None

    def version_distribution(self) -> dict[int, list[int]]:
        return {self.cats.cat.version if self.cats.cat else 0: [1]}


class FakeProposals:
    def __init__(self) -> None:
        self.rows: dict[UUID, StoredProposal] = {}

    def save(
        self,
        actor: ModelActor,
        *,
        base_version: int,
        spec: Catalog,
        plan: Plan,
        rationale: str,
        proposed_by: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredProposal:
        p = StoredProposal(
            uuid4(),
            actor.tenant.seq,
            base_version,
            spec,
            plan.hash,
            proposed_by,
            fingerprint,
            "now",
        )
        self.rows[p.id] = p
        self.keys = {(proposed_by, idempotency_key): p}
        return p

    def find(
        self, actor: ModelActor, proposed_by: str, idempotency_key: str
    ) -> StoredProposal | None:
        return getattr(self, "keys", {}).get((proposed_by, idempotency_key))

    def get(self, tenant: TenantRef, proposal_id: UUID) -> StoredProposal | None:
        return self.rows.get(proposal_id)


def ops(*o: dict[str, Any]) -> list[Operation]:
    return [Operation.model_validate(x) for x in o]


def req(**kw: Any) -> ProposeModelChange:
    base = {"model_id": "customer", "rationale": "thêm SĐT", "idempotency_key": "key-00000001"}
    return ProposeModelChange.model_validate({**base, **kw})


# ------------------------------------------------------------------- describe_model ----
def test_describe_model_shape_and_pii_flags() -> None:
    cat = catalog(*pack_models(*PACKS))
    out = describe_model(cat.model("employee_pii"), 3, include_values_schema=True)["model"]
    assert out["revision"] == 3 and out["primitive"] == "record"
    f = {x["id"]: x for x in out["fields"]}
    assert (f["national_id"]["pii"], f["national_id"]["sensitive"]) == (True, False)
    assert (f["base_salary"]["pii"], f["base_salary"]["sensitive"]) == (True, True)
    assert f["base_salary"]["pii_suggested"] is True
    assert f["employee"]["type"] == "ref" and f["employee"]["ref_model"] == "employee"
    assert out["values_schema"]["additionalProperties"] is False
    assert {"id", "label", "primitive", "revision", "fields", "states", "transitions"} <= set(out)
    assert pii_level(True, False) == "basic" and pii_level(None, None) is None
    assert pii_level(False, True) == "sensitive"


def test_describe_types_map_to_contract_enum() -> None:
    out = describe_model(catalog(*pack_models(*PACKS)).model("task"), 1)["model"]
    allowed = {
        "string",
        "text",
        "integer",
        "decimal",
        "money",
        "boolean",
        "date",
        "datetime",
        "enum",
        "ref",
        "person",
        "file",
    }
    assert {f["type"] for f in out["fields"]} <= allowed
    assert any(f["type"] == "person" for f in out["fields"])


# ---------------------------------------------------------------- operations ----
def test_operations_create_model_and_fields() -> None:
    cat = apply_operations(
        None,
        "device_loan",
        ops(
            {"op": "create_model", "model": {"label": "Phiếu mượn", "primitive": "record"}},
            {
                "op": "add_field",
                "field": {"id": "due_date", "label": "Hạn trả", "type": "date", "required": True},
            },
            {
                "op": "add_field",
                "field": {
                    "id": "borrower_phone",
                    "label": "SĐT",
                    "type": "string",
                    "max_length": 20,
                },
            },
            {
                "op": "add_field",
                "field": {"id": "salary", "label": "Lương", "type": "money", "sensitive": True},
            },
            {
                "op": "add_rule",
                "rule": {"id": "r1", "expr": "due_date >= '2026-01-01'", "message": "Hạn trả sai"},
            },
        ),
    )
    m = cat.model("device_loan")
    assert cat.version == 1 and [f.id for f in m.fields] == ["f1", "f2", "f3"]
    assert m.field("borrower_phone").pii == "basic" and m.field("borrower_phone").pii_suggested
    assert m.field("salary").pii == "sensitive" and not m.field("salary").pii_suggested


def test_operations_capability_unavailable() -> None:
    cat = catalog(customer())
    for o in (
        {"op": "add_state", "state": {"id": "x", "label": "X"}},
        {
            "op": "add_field",
            "field": {"id": "tags", "label": "Tag", "type": "string", "multiple": True},
        },
    ):
        with pytest.raises(ModelError) as ex:
            apply_operations(cat, "customer", ops(o))
        assert ex.value.code == "capability_unavailable"


def test_operations_invalid_vietnamese() -> None:
    cat = catalog(customer())
    with pytest.raises(ModelError) as ex:
        apply_operations(
            cat,
            "customer",
            ops({"op": "add_field", "field": {"id": "select", "label": "x", "type": "text"}}),
        )
    assert ex.value.code == "invalid_request" and "từ khóa SQL" in ex.value.message
    with pytest.raises(ModelError) as ex:
        apply_operations(cat, "customer", ops({"op": "remove_field", "field_id": "emial"}))
    assert ex.value.details["reason"] == "unknown_field"


# ------------------------------------------------------------------- propose ----
def service(cat: Catalog | None) -> tuple[ModelService, FakeMigrator]:
    cats = FakeCatalogs(cat)
    mig = FakeMigrator(cats)
    return ModelService(cats, mig, FakeProposals()), mig


def test_propose_dry_run_classification() -> None:
    svc, _ = service(catalog(customer()))
    a = actor()
    add = {
        "op": "add_field",
        "field": {"id": "phone", "label": "SĐT", "type": "string", "max_length": 20},
    }
    out = svc.propose(a, req(operations=[add]), proposed_by="agent:a")
    assert (
        out["dry_run"] is True and out["classification"] == "additive" and out["base_revision"] == 1
    )
    out = svc.propose(
        a, req(operations=[{"op": "remove_field", "field_id": "note"}]), proposed_by="agent:a"
    )
    assert out["classification"] == "destructive" and out["approvals_required"] == 2
    assert out["destructive_change_ids"] == ["drop_field:m1:f6"]


def test_propose_base_revision_cas() -> None:
    svc, _ = service(catalog(customer(), version=4))
    add = {"op": "add_field", "field": {"id": "phone", "label": "SĐT", "type": "string"}}
    with pytest.raises(ModelError) as ex:
        svc.propose(actor(), req(operations=[add], base_revision=3), proposed_by="agent:a")
    assert ex.value.code == "revision_conflict" and ex.value.details["current_revision"] == 4
    assert (
        svc.propose(actor(), req(operations=[add], base_revision=4), proposed_by="agent:a")[
            "base_revision"
        ]
        == 4
    )
    new = req(
        model_id="lead",
        operations=[
            {"op": "create_model", "model": {"label": "L", "primitive": "record"}},
            {"op": "add_field", "field": {"id": "x", "label": "X", "type": "text"}},
        ],
        base_revision=0,
    )
    assert svc.propose(actor(), new, proposed_by="agent:a")["base_revision"] == 0


def test_propose_submit_idempotent_and_sod() -> None:
    svc, mig = service(catalog(customer()))
    add = {"op": "add_field", "field": {"id": "phone", "label": "SĐT", "type": "string"}}
    out = svc.propose(actor(), req(operations=[add], dry_run=False), proposed_by="agent:a")
    pid = UUID(out["proposal"].split(":", 1)[1])
    again = svc.propose(actor(), req(operations=[add], dry_run=False), proposed_by="agent:a")
    assert again["replayed"] is True and again["proposal"] == out["proposal"]
    other = {"op": "add_field", "field": {"id": "fax", "label": "Fax", "type": "string"}}
    with pytest.raises(ModelError) as ex:
        svc.propose(actor(), req(operations=[other], dry_run=False), proposed_by="agent:a")
    assert ex.value.details["reason"] == "idempotency_conflict"
    with pytest.raises(ModelError) as ex:
        svc.approve_and_apply(
            T, pid, approved_hash=out["plan_hash"], confirm=[], approved_by="agent:a"
        )
    assert ex.value.details["reason"] == "sod"
    res = svc.approve_and_apply(
        T, pid, approved_hash=out["plan_hash"], confirm=[], approved_by="user:mai"
    )
    assert res.version == 2 and mig.applied == [("agent:a", "user:mai")]


# --------------------------------------------------------------------- CRUD ----
class FakeRepo:
    def __init__(self) -> None:
        self.rows: dict[UUID, dict[str, Any]] = {}
        self.fail: Exception | None = None

    def insert(self, actor: ModelActor, m: ModelSpec, values: Mapping[str, Any]) -> dict[str, Any]:
        if self.fail:
            raise self.fail
        rid = uuid4()
        self.rows[rid] = {"id": rid, "revision": 1, **values}
        return self.rows[rid]

    def update(
        self,
        actor: ModelActor,
        m: ModelSpec,
        record_id: UUID,
        expected_revision: int,
        values: Mapping[str, Any],
        *,
        workflow: bool = False,
    ) -> UpdateOutcome:
        r = self.rows.get(record_id)
        if r is None or r["revision"] != expected_revision:
            return UpdateOutcome(None, r["revision"] if r else None)
        r.update(values, revision=r["revision"] + 1)
        return UpdateOutcome(r, r["revision"])

    def delete(
        self, actor: ModelActor, m: ModelSpec, record_id: UUID, expected_revision: int
    ) -> UpdateOutcome:
        r = self.rows.pop(record_id, None)
        return UpdateOutcome(r, 1 if r else None)

    def get(self, actor: ModelActor, m: ModelSpec, record_id: UUID) -> dict[str, Any] | None:
        return self.rows.get(record_id)

    def search(
        self,
        actor: ModelActor,
        m: ModelSpec,
        where: Expr | None,
        order: Sequence[tuple[str, bool]],
        limit: int,
        offset: int,
        scope_hint: Literal["all", "scoped"],
    ) -> Page:
        return Page(len(self.rows), list(self.rows.values())[offset : offset + limit])


def crud() -> tuple[RecordService, FakeRepo]:
    repo = FakeRepo()
    return RecordService(FakeCatalogs(catalog(customer())), repo), repo


def test_crud_permission_and_unknown_model() -> None:
    rs, _ = crud()
    with pytest.raises(ModelError) as ex:
        rs.create(actor("manager"), "customer", {"code": "KH0001", "full_name": "An"})
    assert ex.value.code == "access_denied" and ex.value.details["allowed_roles"] == ["sales"]
    with pytest.raises(ModelError) as ex:
        rs.create(actor(), "custmer", {})
    assert ex.value.code == "not_found" and "có phải 'customer'" in ex.value.message


def test_crud_cas_and_db_error_mapping() -> None:
    rs, repo = crud()
    a = actor()
    rec = rs.create(a, "customer", {"code": "KH0001", "full_name": "An"})["record"]
    rid = UUID(rec["id"])
    assert rs.update(a, "customer", rid, 1, {"note": "x"})["record"]["revision"] == 2
    with pytest.raises(ModelError) as ex:
        rs.update(a, "customer", rid, 1, {"note": "y"})
    assert ex.value.code == "revision_conflict" and ex.value.details["current_revision"] == 2
    repo.fail = ConstraintViolated("unique", "ux_m1_f1", None)
    with pytest.raises(ModelError) as ex:
        rs.create(a, "customer", {"code": "KH0001", "full_name": "Bình"})
    assert (
        ex.value.code == "rule_violation"
        and ex.value.message == "'Mã KH' đã tồn tại, phải là duy nhất"
    )
    with pytest.raises(ModelError) as ex:
        rs.search(a, "customer", where={"field": "code", "op": "~", "value": "x"})
    assert ex.value.code == "invalid_request"
    assert rs.search(a, "customer", sort=["-created_at", "code"])["total"] == 1
