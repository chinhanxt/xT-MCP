# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from types import SimpleNamespace
from uuid import uuid4

from xt.entrypoints.composition.process import ProcessToolAdapter


def test_request_withdrawal_metadata_requires_owner_and_transition_permission() -> None:
    owner, other, rid = uuid4(), uuid4(), uuid4()
    binding = SimpleNamespace(primitive="request", config={})
    store = SimpleNamespace(bindings=lambda actor: {"advance_request": binding})
    adapter = object.__new__(ProcessToolAdapter)
    adapter.rt = SimpleNamespace(store=store)
    checks = []
    adapter.access = SimpleNamespace(
        check=lambda ctx, check: checks.append(check) or SimpleNamespace(allowed=True)
    )
    caller = SimpleNamespace(actor=object(), person=SimpleNamespace(id=owner))
    row = {"owner_id": owner, "state": "draft"}

    assert adapter.available_transitions(None, caller, "advance_request", rid, row) == [
        {"to_state": "withdrawn", "label": "Rút đề nghị", "level": "L1"}
    ]
    assert checks[0].action == "record.transition"
    assert checks[0].resource == f"advance_request:{rid}"
    assert (
        adapter.available_transitions(
            None, caller, "advance_request", rid, {**row, "state": "approved"}
        )
        == []
    )
    assert (
        adapter.available_transitions(
            None, caller, "advance_request", rid, {**row, "owner_id": other}
        )
        == []
    )

    adapter.access = SimpleNamespace(check=lambda ctx, check: SimpleNamespace(allowed=False))
    assert adapter.available_transitions(None, caller, "advance_request", rid, row) == []
