# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""IAM department attributes follow the stored RLS department for existing records."""

from types import SimpleNamespace
from uuid import uuid4

from xt.entrypoints.composition.security import ToolAccessGuard


def test_record_department_uses_stored_unit_and_null_only_legacy_fallback() -> None:
    owner, stored, missing, rid = uuid4(), uuid4(), uuid4(), uuid4()
    row = {"owner_id": owner, "department_id": stored, "created_by": owner}
    unit_calls = []
    person_calls = []

    def unit(actor, *, uid):
        unit_calls.append(uid)
        return SimpleNamespace(key="stored") if uid == stored else None

    def person(actor, uid):
        person_calls.append(uid)
        return SimpleNamespace(unit_key="owner-current")

    store = SimpleNamespace(unit=unit, person=person)
    rt = SimpleNamespace(
        records=SimpleNamespace(get=lambda actor, model, key: {"record": row}), store=store
    )
    caller = SimpleNamespace(actor=object(), workflow=lambda: object())
    guard = ToolAccessGuard(rt)

    assert guard._record_attrs(caller, "request", rid)["department"] == "stored"
    assert unit_calls == [stored] and person_calls == []

    row["department_id"] = None
    assert guard._record_attrs(caller, "request", rid)["department"] == "owner-current"
    assert person_calls == [owner]

    row["department_id"] = missing
    assert "department" not in guard._record_attrs(caller, "request", rid)
    assert unit_calls[-1] == missing and person_calls == [owner]
