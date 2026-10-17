# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Department attribution on record insert uses the caller's existing tenant context."""

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from xt.adapters.db.records import SYSTEM_OUT, PgRecordRepository
from xt.domain.model.context import ModelActor, TenantRef


def test_insert_uses_single_owner_department_and_preserves_explicit_value() -> None:
    owner, other_owner, own_dept, explicit_dept = uuid4(), uuid4(), uuid4(), uuid4()
    actor = ModelActor(TenantRef(1, uuid4()), owner, (), department_ids=(own_dept,))

    def insert(values):
        class Cursor:
            def execute(self, query, params):
                self.params = params
                return self

            def fetchone(self):
                return {**dict.fromkeys(SYSTEM_OUT), "revision": 1}

        cursor = Cursor()

        @contextmanager
        def tenant_tx(current, *, workflow=False):
            assert current is actor
            yield cursor

        repo = PgRecordRepository(SimpleNamespace(tenant_tx=tenant_tx))
        repo.insert(actor, SimpleNamespace(name="request", fields=[]), values)
        return cursor.params

    assert insert({"code": "own"}) == ["own", own_dept]
    assert insert({"owner_id": owner, "code": "own"}) == [owner, "own", own_dept]
    assert insert({"owner_id": other_owner, "code": "other"}) == [other_owner, "other"]
    assert insert({"code": "explicit", "department_id": explicit_dept}) == [
        "explicit",
        explicit_dept,
    ]


def test_insert_without_actor_department_does_not_need_org_tables() -> None:
    actor = ModelActor(TenantRef(1, uuid4()), uuid4(), ())

    class Cursor:
        calls = 0

        def execute(self, query, params):
            self.calls += 1
            self.params = params
            return self

        def fetchone(self):
            return {**dict.fromkeys(SYSTEM_OUT), "revision": 1}

    cursor = Cursor()

    @contextmanager
    def tenant_tx(current, *, workflow=False):
        yield cursor

    repo = PgRecordRepository(SimpleNamespace(tenant_tx=tenant_tx))
    repo.insert(actor, SimpleNamespace(name="emp", fields=[]), {})
    assert cursor.params == []
    assert cursor.calls == 1
