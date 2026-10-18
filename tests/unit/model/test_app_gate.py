# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ứng dụng Tầng 3 bị tắt (G8 / U15): model của gói bị chặn ghi (mọi đường, kể cả quy trình),
đọc chỉ còn cho admin, describe_model báo rõ; model khác và tenant khác không đổi; bật lại thì
dùng lại được. Catalog KHÔNG bị lọc (lọc sẽ khiến lần đổi model kế tiếp lập kế hoạch DROP)."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import pytest

from tests.unit.model.support import catalog, customer
from tests.unit.model.test_service import FakeRepo
from xt.application.model_engine.ports import AggregateQuery, DisabledApps
from xt.application.model_engine.service import APP_ADMIN_ROLE, ModelService, RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.spec import Catalog

A = TenantRef(1, uuid4())
B = TenantRef(2, uuid4())
PERMS = {
    "sales": {"actions": ["create", "read", "update", "delete"], "scope": "all"},
    APP_ADMIN_ROLE: {"actions": ["read"], "scope": "all"},
}


def cat() -> Catalog:
    lead = customer(id="m_lead", name="lead", label="Khách tiềm năng", permissions=PERMS)
    for i, f in enumerate(lead["fields"]):
        f["id"] = f"lead_f{i}"
    return catalog(customer(permissions=PERMS), lead)


class TenantCatalogs:
    """Catalog chung; trạng thái tắt theo từng tenant (như RLS của pack_install)."""

    def __init__(self) -> None:
        self.cat = cat()
        self.off: dict[int, DisabledApps] = {}

    def current(self, actor: ModelActor) -> tuple[int, Catalog] | None:
        return self.cat.version, self.cat

    def disabled(self, actor: ModelActor) -> DisabledApps:
        return self.off.get(actor.tenant.seq, DisabledApps())


def disable(cats: TenantCatalogs, tenant: TenantRef) -> None:
    cats.off[tenant.seq] = DisabledApps(models={"lead": "crm"}, packs=frozenset({"crm"}))


def who(tenant: TenantRef, *roles: str) -> ModelActor:
    return ModelActor(tenant, uuid4(), roles or ("sales",))


def setup() -> tuple[TenantCatalogs, RecordService, UUID]:
    cats = TenantCatalogs()
    rs = RecordService(cats, FakeRepo())
    rid = UUID(rs.create(who(A), "lead", {"code": "KH0001", "full_name": "An"})["record"]["id"])
    return cats, rs, rid


def assert_app_disabled(ex: pytest.ExceptionInfo[ModelError]) -> None:
    assert ex.value.code == "capability_unavailable"
    assert ex.value.details == {"reason": "app_disabled", "pack": "crm", "model": "lead"}
    assert "crm" in ex.value.message and ex.value.remediation


def test_disabled_app_blocks_every_write_path_and_keeps_data() -> None:
    cats, rs, rid = setup()
    disable(cats, A)
    a = who(A)

    def create() -> object:
        return rs.create(a, "lead", {"code": "KH0002", "full_name": "Bình"})

    def update() -> object:
        return rs.update(a, "lead", rid, 1, {"note": "x"})

    def workflow() -> object:
        return rs.workflow_write(a, "lead", rid, 1, {"note": "x"})

    def delete() -> object:
        return rs.delete(a, "lead", rid, 1)

    for w in (create, update, workflow, delete):
        with pytest.raises(ModelError) as ex:
            w()
        assert_app_disabled(ex)
    assert rid in rs.repo.rows  # type: ignore[attr-defined]  # dữ liệu giữ nguyên


def test_disabled_app_reads_only_for_admin() -> None:
    cats, rs, rid = setup()
    disable(cats, A)

    def get(a: ModelActor) -> object:
        return rs.get(a, "lead", rid)

    def search(a: ModelActor) -> object:
        return rs.search(a, "lead")

    for read in (get, search):
        with pytest.raises(ModelError) as ex:
            read(who(A))
        assert_app_disabled(ex)
        read(who(A, APP_ADMIN_ROLE))  # admin vẫn đọc được để đối soát


@pytest.mark.parametrize("role", ["admin", "it", "director", "administrator", "tenant_admin"])
def test_disabled_app_read_exception_requires_literal_admin_role(role: str) -> None:
    cats, rs, rid = setup()
    # Every candidate can read the enabled model; only the lifecycle gate differs.
    cats.cat.model("lead").permissions[role] = cats.cat.model("lead").permissions["sales"]
    actor = who(A, role)
    ms = ModelService(cats, None, None)  # type: ignore[arg-type]
    reads: tuple[Callable[[], object], ...] = (
        lambda: rs.get(actor, "lead", rid),
        lambda: rs.search(actor, "lead"),
        lambda: rs.aggregate(actor, "lead", None, AggregateQuery()),
        lambda: ms.describe_model(actor, "lead"),
    )
    for read in reads:
        read()
    disable(cats, A)
    for read in reads:
        if role == "admin":
            read()
        else:
            with pytest.raises(ModelError) as ex:
                read()
            assert_app_disabled(ex)


def test_disabled_app_admin_exception_still_requires_model_permission() -> None:
    cats, rs, rid = setup()
    del cats.cat.model("lead").permissions["admin"]
    disable(cats, A)
    actor = who(A, "admin")
    ms = ModelService(cats, None, None)  # type: ignore[arg-type]
    reads: tuple[Callable[[], object], ...] = (
        lambda: rs.get(actor, "lead", rid),
        lambda: rs.search(actor, "lead"),
        lambda: rs.aggregate(actor, "lead", None, AggregateQuery()),
        lambda: ms.describe_model(actor, "lead"),
    )
    for read in reads:
        with pytest.raises(ModelError) as ex:
            read()
        assert ex.value.code == "access_denied"


def test_disabled_app_blocks_aggregate_for_non_admin() -> None:
    cats, rs, _ = setup()
    disable(cats, A)
    with pytest.raises(ModelError) as ex:
        rs.aggregate(who(A), "lead", None, AggregateQuery())
    assert_app_disabled(ex)


def test_disabled_app_admin_can_count_existing_records() -> None:
    cats, rs, _ = setup()
    disable(cats, A)
    result = rs.aggregate(who(A, "admin"), "lead", None, AggregateQuery())
    assert result["rows"] == [((), 1, ())]


def test_other_models_and_other_tenants_unaffected() -> None:
    cats, rs, rid = setup()
    disable(cats, A)
    a, b = who(A), who(B)
    rs.create(a, "customer", {"code": "KH0003", "full_name": "Chi"})  # gói khác cùng tenant
    rs.create(b, "lead", {"code": "KH0004", "full_name": "Dũng"})  # tenant khác
    assert rs.get(b, "lead", rid)["record"]["id"] == str(rid)


def test_re_enable_restores_writes() -> None:
    cats, rs, rid = setup()
    disable(cats, A)
    with pytest.raises(ModelError):
        rs.update(who(A), "lead", rid, 1, {"note": "x"})
    cats.off.pop(A.seq)
    assert rs.update(who(A), "lead", rid, 1, {"note": "x"})["record"]["revision"] == 2


def test_describe_model_reports_app_disabled() -> None:
    cats = TenantCatalogs()
    ms = ModelService(cats, None, None)  # type: ignore[arg-type]
    assert ms.describe_model(who(A), "lead")["model"]["id"] == "lead"
    disable(cats, A)
    with pytest.raises(ModelError) as ex:
        ms.describe_model(who(A), "lead")
    assert_app_disabled(ex)
    assert ms.describe_model(who(B), "lead")["model"]["id"] == "lead"
