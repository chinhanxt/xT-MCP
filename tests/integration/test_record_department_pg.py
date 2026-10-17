# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Record department attribution and model RLS on a small PostgreSQL fixture."""

from uuid import UUID, uuid4

import pytest

from tests.integration import pg as pgmod
from tests.unit.model.support import catalog
from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bootstrap, provision_tenant
from xt.application.model_engine.service import RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import RiskClass

pytestmark = pytest.mark.integration


def test_request_department_read_scope_and_explicit_department() -> None:
    pg = pgmod.start()
    app = None
    try:
        with pg.su() as su:
            bootstrap(
                su,
                migrator_password=pg.migrator_password,
                app_password=pg.app_password,
                pepper=pg.pepper,
            )
        with pg.migrator() as conn:
            tenant_a = provision_tenant(conn, uuid4(), "A")
            tenant_b = provision_tenant(conn, uuid4(), "B")
        app = Database(pg.app_dsn, audit_key=AuditKey(1, pg.pepper))
        catalogs = PgCatalogReader(app)
        repo = PgRecordRepository(app)
        records = RecordService(catalogs, repo)
        spec = catalog(
            {
                "id": "m1",
                "name": "request",
                "label": "Request",
                "fields": [{"id": "f1", "name": "code", "type": "text", "label": "Code"}],
                "permissions": {
                    "employee": {"actions": ["create", "read"], "scope": "own"},
                    "dept_head": {"actions": ["read"], "scope": "department"},
                },
            }
        )
        migrator = PgMigrator(pg.migrator, audit_key=AuditKey(1, pg.pepper))
        for tenant in (tenant_a, tenant_b):
            plan = migrator.plan(tenant, spec, rehearse=True)
            assert not plan.ids(RiskClass.BLOCKED)
            migrator.apply(tenant, spec, plan.hash, set(), proposed_by="test", approved_by="test")
            assert migrator.run_jobs(tenant.seq).dead == 0

        dept, other_dept = uuid4(), uuid4()
        owner_id = uuid4()

        def actor(tenant: TenantRef, person: UUID, role: str, department: UUID) -> ModelActor:
            return ModelActor(tenant, person, (role,), department_ids=(department,))

        owner = actor(tenant_a, owner_id, "employee", dept)
        own_head = actor(tenant_a, uuid4(), "dept_head", dept)
        other_head = actor(tenant_a, uuid4(), "dept_head", other_dept)
        foreign_head = actor(tenant_b, uuid4(), "dept_head", dept)
        created = records.create(owner, "request", {"code": "new"})["record"]
        rid = UUID(created["id"])
        assert created["department_id"] == str(dept)
        assert records.get(own_head, "request", rid)["record"]["id"] == str(rid)
        for denied in (other_head, foreign_head):
            with pytest.raises(ModelError) as exc:
                records.get(denied, "request", rid)
            assert exc.value.code == "not_found"

        model = catalogs.current(owner)[1].model("request")
        explicit = repo.insert(owner, model, {"code": "explicit", "department_id": other_dept})
        assert explicit["department_id"] == other_dept
        assert records.get(other_head, "request", explicit["id"])["record"]["id"] == str(
            explicit["id"]
        )
        with pytest.raises(ModelError) as exc:
            records.get(own_head, "request", explicit["id"])
        assert exc.value.code == "not_found"
    finally:
        if app is not None:
            app.close()
        pgmod.stop()
