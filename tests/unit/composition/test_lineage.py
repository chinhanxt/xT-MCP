# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Aggregate lineage remains private, expires, and rechecks current model access."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from tests.unit.model.support import catalog, customer
from xt.application.model_engine.ports import DisabledApps, Page
from xt.application.model_engine.service import RecordService
from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.application.tools.errors import ToolError
from xt.application.tools.ports import AccessCheck, AccessDecision
from xt.contracts.tools import BY_NAME, ErrorCode
from xt.domain.identity import PrincipalKind
from xt.domain.model.context import ModelActor, TenantRef
from xt.entrypoints.composition.runtime import iam_person
from xt.entrypoints.composition.security import ToolAccessGuard
from xt.entrypoints.composition.workspace import History

AS_OF = datetime(2026, 11, 30, 3, 0, tzinfo=UTC)
TENANT = TenantRef(1, uuid4())


def actor() -> ModelActor:
    return ModelActor(TENANT, uuid4(), ("manager",))


OWNER = Actor("t1", "sub", PrincipalKind.HUMAN, "desk", None)
QUERY = {
    "model_id": "customer", "model_revision": 3, "where": {"tier": "gold"},
    "metrics": ["count"], "group_by": ["tier"], "date_field": "created_at",
    "time_bucket": None, "timezone": "UTC", "as_of": "2026-11-30T03:00:00Z",
    "search_records": {"model_id": "customer", "where": {"tier": "gold"}},
}  # fmt: skip


@pytest.mark.parametrize(
    "case", ["owner", "admin", "tenant", "actor", "client", "expired", "unknown"]
)
def test_pg_lineage_authorization_and_expiration(case: str) -> None:
    from xt.adapters.db.lineage import PgLineageStore

    ma = actor()
    row: dict[str, Any] | None = {
        "tenant_id": ma.tenant.id,
        "actor_key": hashlib.sha256(OWNER.key.encode()).hexdigest(),
        "expires_at": AS_OF + timedelta(hours=1),
        "query": QUERY,
    }
    caller, key = ma, OWNER.key
    if case == "admin":
        caller, key = replace(ma, roles=("admin",)), "another actor"
    elif case == "tenant":
        caller = replace(ma, tenant=TenantRef(2, uuid4()), roles=("admin",))
    elif case in ("actor", "client"):
        key = (
            replace(OWNER, subject="other")
            if case == "actor"
            else replace(OWNER, client_id="other")
        ).key
    elif case == "expired":
        assert row is not None
        row["expires_at"] = AS_OF  # the exact TTL boundary is expired
    elif case == "unknown":
        row = None
    calls: list[Any] = []

    class Cursor:
        def execute(self, query: Any, params: Any) -> Cursor:
            calls.append((query, params))
            return self

        def fetchone(self) -> dict[str, Any] | None:
            return row

    @contextmanager
    def tx(passed: Any) -> Iterator[Cursor]:
        assert passed is caller
        yield Cursor()

    db: Any = SimpleNamespace(tenant_tx=tx)
    store = PgLineageStore(db)
    found = store.get(caller, key, "trace-1", AS_OF)
    assert found == (QUERY if case in ("owner", "admin") else None)
    assert "tenant_id" in calls[0][0] and caller.tenant.id in calls[0][1]


@pytest.fixture
def lineage(monkeypatch: pytest.MonkeyPatch) -> Any:
    ma = actor()
    repo: Any = SimpleNamespace(search=lambda *a: Page(0, []))
    model = customer()
    model["permissions"]["admin"] = {"actions": ["read"], "scope": "all"}
    cat = catalog(model)
    cats: Any = SimpleNamespace(current=lambda a: (3, cat), disabled=lambda a: DisabledApps())
    records = RecordService(cats, repo)
    state: Any = SimpleNamespace(query=QUERY, allowed=True, checks=[], searches=[])
    ctx = CallContext(OWNER, ClientClass.HUMAN, "history-1", AS_OF)
    caller = SimpleNamespace(actor=ma, profile=SimpleNamespace(timezone="UTC"))

    def check(_self: Any, _ctx: Any, request: Any) -> AccessDecision:
        state.checks.append(request)
        return AccessDecision(state.allowed)

    def search(*args: Any, **kwargs: Any) -> Any:
        state.searches.append((args, kwargs))
        return records.search(*args, **kwargs)

    monkeypatch.setattr(ToolAccessGuard, "check", check)
    rt: Any = SimpleNamespace(
        caller=lambda c: caller,
        lineage=SimpleNamespace(get=lambda *a: state.query),
        records=SimpleNamespace(search=search),
        repo=repo,
        catalogs=cats,
    )
    state.history, state.ctx, state.caller = History(rt, b"k" * 32, frozenset()), ctx, caller
    state.model = model
    return state


def test_history_lineage_schema_replay_and_current_model_checks(lineage: Any) -> None:
    result = lineage.history.get_history(lineage.ctx, {"ref": "explain:trace-1", "view": "explain"})
    Draft202012Validator(BY_NAME["get_history"].output_schema).validate(result)
    assert result["explanation"]["query"] == QUERY
    replay = result["explanation"]["query"]["search_records"]
    Draft202012Validator(BY_NAME["search_records"].input_schema).validate(replay)
    assert lineage.searches and lineage.searches[0][0][0] is lineage.caller.actor
    assert any(
        c.action == "record.read" and c.attributes["model_id"] == "customer" for c in lineage.checks
    )
    assert "record_refs" not in result["explanation"]


@pytest.mark.parametrize("case", ["unknown", "iam", "model"])
def test_history_lineage_denials_are_clear(lineage: Any, case: str) -> None:
    if case == "unknown":
        lineage.query = None
    elif case == "iam":
        lineage.allowed = False
    else:
        lineage.caller.actor = replace(lineage.caller.actor, roles=("guest",))
    with pytest.raises(ToolError) as exc:
        lineage.history.get_history(lineage.ctx, {"ref": "explain:trace-1", "view": "explain"})
    assert exc.value.code in (ErrorCode.NOT_FOUND, ErrorCode.ACCESS_DENIED)
    if case == "unknown":
        assert exc.value.code == ErrorCode.NOT_FOUND
        assert exc.value.details["ref"] == "explain:trace-1"


@pytest.mark.parametrize("part", ["where", "bucket"])
def test_history_rechecks_changed_pii_classification(lineage: Any, part: str) -> None:
    if part == "where":
        lineage.model["fields"][3]["pii"] = "sensitive"
    else:
        lineage.model["fields"].append(
            {
                "id": "birth",
                "name": "born_on",
                "type": "date",
                "label": "Birth",
                "pii": "sensitive",
            }
        )
        lineage.query = {
            **QUERY,
            "where": {},
            "group_by": [],
            "date_field": "born_on",
            "time_bucket": "month",
            "search_records": {"model_id": "customer", "where": {}},
        }
    lineage.history.rt.catalogs.current = lambda a: (4, catalog(lineage.model))
    with pytest.raises(ToolError) as exc:
        lineage.history.get_history(lineage.ctx, {"ref": "explain:trace-1", "view": "explain"})
    assert exc.value.code == ErrorCode.INVALID_REQUEST
    assert "gold" not in str(exc.value)
    assert not lineage.searches


@pytest.mark.parametrize("known", [True, False])
def test_history_iam_resolves_explain_ref_to_aggregate_model(known: bool) -> None:
    ma = actor()
    person: Any = SimpleNamespace(
        id=ma.actor_id, roles=ma.roles, active=True, unit_key=None, limit_amount=None
    )
    caller = SimpleNamespace(
        actor=ma, person=person, alias="t1", tenant=ma.tenant, iam=lambda: iam_person("t1", person)
    )
    policy = {
        "version": "1",
        "tenant": "t1",
        "statements": [
            {
                "id": "history",
                "effect": "Allow",
                "subjects": ["role:manager"],
                "actions": ["history.read"],
                "resources": ["xrn:t1:customer:*"],
            }
        ],
    }
    rt: Any = SimpleNamespace(
        caller=lambda ctx: caller,
        lineage=SimpleNamespace(get=lambda *a: QUERY if known else None),
        store=SimpleNamespace(active_policy=lambda a: (1, policy)),
    )
    guard = ToolAccessGuard(rt)
    ctx = CallContext(OWNER, ClientClass.HUMAN, "history-1", AS_OF)
    check = AccessCheck("get_history", "history.read", "explain:trace-1")
    if known:
        assert guard.check(ctx, check).allowed
    else:
        with pytest.raises(ToolError) as exc:
            guard.check(ctx, check)
        assert exc.value.code == ErrorCode.NOT_FOUND
