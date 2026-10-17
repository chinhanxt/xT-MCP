# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-UNI-H-1 trên Postgres 17 thật (migration 0017, xt_app + RLS): vai trò cấp qua access_grant đã
duyệt vào vai trò hiệu lực của person (đọc lại mỗi lời gọi), thu hồi có hiệu lực ở lần đọc kế tiếp,
idempotent, SoD ở mức DB, cách ly tenant, mỗi thay đổi vào hash-chain audit."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import StackBase
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.integration import import_org, person_id, register_tenant
from xt.application.iam.role_grants import AccessGrant, RoleGrantRejected, RoleGrantService
from xt.domain.model.context import ModelActor
from xt.entrypoints.composition.runtime import Runtime

pytestmark = pytest.mark.integration
ALLOWED = ("employee", "hr", "it_admin")


def _org(sfx: str, *keys: str) -> dict[str, object]:
    return {
        "people": [{"key": k, "name": k, "subject": f"kc-{sfx}-{k}", "roles": []} for k in keys]
    }


def test_access_grant_roles_on_pg(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    sfx = uuid.uuid4().hex[:8]
    with pg.migrator() as m:
        ta = register_tenant(m, alias=f"rga{sfx}", name="A", timezone="UTC", llm_egress="masked")
        tb = register_tenant(m, alias=f"rgb{sfx}", name="B", timezone="UTC", llm_egress="masked")
        import_org(m, ta, _org(sfx, "hr", "it", "new"))
        import_org(m, tb, _org(sfx, "other"))
    hr, it, new = (person_id(ta.id, k) for k in ("hr", "it", "new"))
    other = person_id(tb.id, "other")
    db = Database(pg.app_dsn, max_size=2, audit_key=AuditKey(1, pg.pepper))
    rt = Runtime(app_db=db, migrator_dsn=None)
    svc = RoleGrantService(rt.store)
    ref = f"access_grant:{uuid.uuid4()}"
    grant = AccessGrant(ref, "keycloak", "employee", new, hr, revoked=False, creator=hr)
    as_it, look = ModelActor(ta, it, ("xt_workflow",)), Runtime.lookup_actor(ta)
    try:
        # áp + đọc lại (Runtime.resolve dùng person_by_subject mỗi lời gọi)
        assert svc.apply(as_it, grant, allowed=ALLOWED) == "applied"
        p = rt.store.person_by_subject(look, f"kc-{sfx}-new")
        assert p is not None and p.roles == ("employee",)
        assert [x.id for x in rt.store.people(look, role="employee")[1]] == [new]
        assert svc.apply(as_it, grant, allowed=ALLOWED) == "already"

        # tự duyệt / vai trò ngoài allow-list: service từ chối; SoD còn chặn ở DB
        with pytest.raises(RoleGrantRejected):
            svc.apply(ModelActor(ta, hr, ()), grant, allowed=ALLOWED)
        with pytest.raises(RoleGrantRejected):
            svc.apply(
                as_it, AccessGrant(ref, "keycloak", "xt_admin", new, hr, False, hr), allowed=ALLOWED
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            rt.store.apply_role_grant(as_it, f"access_grant:{uuid.uuid4()}", it, "hr", hr, it)

        # cách ly tenant: actor tenant B không thấy/không gỡ được dòng của A, không cấp cho người A
        as_other = ModelActor(tb, other, ())
        assert rt.store.person_active(as_other, new) is False
        assert svc.revoke(as_other, ref, by=other) is False
        with pytest.raises(RoleGrantRejected):
            svc.apply(as_other, grant, allowed=ALLOWED)
        p = rt.store.person(look, new)
        assert p is not None and "employee" in p.roles

        # thu hồi: lần đọc kế tiếp không còn vai trò; thu hồi lặp lại idempotent
        assert svc.apply(as_it, AccessGrant(ref, "keycloak", "employee", new, hr, True, hr),
                         allowed=ALLOWED) == "revoked"  # fmt: skip
        p = rt.store.person(look, new)
        assert p is not None and p.roles == ()
        assert svc.revoke(as_it, ref, by=it) is False
    finally:
        db.close()

    with pg.su() as su:
        ops = [r[0] for r in su.execute(
            "SELECT op FROM xt_audit.event WHERE tenant_id = %s AND model LIKE %s ORDER BY seq",
            (ta.id, "%person_role_grant"),
        ).fetchall()]  # fmt: skip
    assert len(ops) >= 2  # áp + thu hồi đều vào hash-chain
