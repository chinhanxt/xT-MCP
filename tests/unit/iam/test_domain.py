# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Glob, policy schema, ngữ nghĩa condition, explain và các ranh giới của PEP."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from tests.unit.iam.support import (
    Host,
    actor,
    condition,
    document,
    receipt,
    request,
    statement,
    with_resource,
)
from xt.contracts.iam import access_denied_details, explanation_json_schema, policy_json_schema
from xt.domain.iam import (
    DEFAULT_ACTION_LEVELS,
    Glob,
    PolicyDocument,
    PolicyEvaluator,
    glob_match,
    parse_xrn,
    policy_digest,
)


def ev(*statements: dict[str, Any]) -> PolicyEvaluator:
    return PolicyEvaluator.from_data(document(*statements))


# ---- glob ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("a*b", "ab", True),
        ("a*b", "aXXb", True),
        ("a*b", "aXXbc", False),
        ("*", "", True),
        ("**", "x", True),
        ("a*a", "a", False),
        ("a*a", "aa", True),
        ("ab*ba", "aba", False),
        ("a*b*c", "a1b2c", True),
        ("a*b*c", "a1c2b", False),
        ("x.y", "xAy", False),
        ("a+b", "aab", False),
        ("(a|b)", "a", False),
        ("[a-z]", "q", False),
        ("a.*", "a.\nb", True),
    ],
)
def test_glob_matches_like_literal_star_only(pattern: str, value: str, expected: bool) -> None:
    assert glob_match(pattern, value) is expected
    reference = ".*".join(re.escape(p) for p in pattern.split("*"))
    assert (re.fullmatch(reference, value, re.DOTALL) is not None) is expected


def test_glob_is_linear_not_exponential() -> None:
    g = Glob("*a*a*a*a*a*a*a*a*b")
    start = time.perf_counter()
    assert not g.matches("a" * 5000)
    assert time.perf_counter() - start < 0.5


# ---- schema -------------------------------------------------------------------------------
def test_json_schema_is_closed_and_exported() -> None:
    schema = policy_json_schema()
    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["additionalProperties"] is False
    assert schema["properties"]["version"]["const"] == "1"
    defs = schema["$defs"]
    assert {"Statement", "Condition", "Ref"} <= set(defs)
    assert all(
        d.get("additionalProperties") is False
        for n, d in defs.items()
        if n in {"Statement", "Condition", "Ref"}
    )
    assert explanation_json_schema()["title"] == "xT IAM explanation v1"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=1),
        lambda d: d.update(version="2"),
        lambda d: d.update(tenant="a b"),
        lambda d: d.update(tenant=""),
        lambda d: d["statements"][0].update(effect="allow"),
        lambda d: d["statements"][0].update(subjects=[]),
        lambda d: d["statements"][0].update(subjects=["role:"]),
        lambda d: d["statements"][0].update(subjects=["a*b"]),
        lambda d: d["statements"][0].update(subjects=["x:y"]),
        lambda d: d["statements"][0].update(actions=["a" * 257]),
        lambda d: d["statements"][0].update(actions=["*a*a*a*a*a*a*a*a*a"]),
        lambda d: d["statements"][0].update(resources=["xrn:acme:(a|b)"]),
        lambda d: d["statements"][0].update(resources=["x" * 33 for _ in range(33)]),
        lambda d: d["statements"][0].update(id=""),
        lambda d: d["statements"][0].update(id="a b"),
        lambda d: d["statements"].append(dict(d["statements"][0])),
        lambda d: d["statements"][0].update(conditions=[condition("resource.amount", "le", "5")]),
        lambda d: d["statements"][0].update(conditions=[condition("resource.owner", "le", 5)]),
        lambda d: d["statements"][0].update(conditions=[condition("resource.amount", "le", True)]),
        lambda d: d["statements"][0].update(
            conditions=[condition("resource.amount", "le", float("inf"))]
        ),
        lambda d: d["statements"][0].update(conditions=[condition("resource.amount", "in", 5)]),
        lambda d: d["statements"][0].update(conditions=[condition("resource.owner", "eq", ["a"])]),
        lambda d: d["statements"][0].update(
            conditions=[condition("resource.owner", "eq", {"ref": "x"})]
        ),
        lambda d: d["statements"][0].update(
            conditions=[condition("resource.owner", "eq", {"ref": "subject.id", "x": 1})]
        ),
        lambda d: d["statements"][0].update(conditions=[condition("subject.secret", "eq", 1)]),
        lambda d: d["statements"][0].update(conditions=[condition("resource.owner", "eq", None)]),
        lambda d: d["statements"][0].update(
            conditions=[condition("resource.owner", "eq", "x")] * 17
        ),
        lambda d: d["statements"][0].update(
            conditions=[condition("resource.owner", "eq", {"ref": "resource.amount"}), 1]
        ),
    ],
)
def test_invalid_documents_rejected(mutate: Any) -> None:
    d = document(statement("a"))
    mutate(d)
    with pytest.raises(ValidationError):
        PolicyDocument.model_validate(d)


def test_conditions_default_empty_and_round_trip_digest() -> None:
    raw = document(statement("a"))
    del raw["statements"][0]["conditions"]
    doc = PolicyDocument.model_validate(raw)
    assert doc.statements[0].conditions == ()
    again = PolicyDocument.model_validate(doc.model_dump(mode="json"))
    assert policy_digest(again) == policy_digest(doc)
    assert policy_digest(PolicyDocument.model_validate(document(statement("b")))) != policy_digest(
        doc
    )


def test_statement_count_limit() -> None:
    many = [statement(f"s{i}") for i in range(2001)]
    with pytest.raises(ValidationError):
        PolicyDocument.model_validate(document(*many))


# ---- xrn ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad", ["", "xrn:a:b", "xrn:a:b:c:d", "xrn:a:b:*", "x:a:b:c", "xrn:a::c", "xrn:a:b:c d"]
)
def test_parse_xrn_rejects(bad: str) -> None:
    assert parse_xrn(bad) is None


def test_parse_xrn_ok() -> None:
    assert parse_xrn("xrn:acme:expense:TU-1") == ("acme", "expense", "TU-1")


# ---- ngữ nghĩa ------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("op", "right", "amount", "allowed"),
    [
        ("ge", 100, 100, True),
        ("ge", 100, 99, False),
        ("le", 100, 100, True),
        ("in", [1, 2, 3], 2, True),
        ("in", [1, 2, 3], 4, False),
        ("in", [1], "a", False),  # khác kiểu: không đánh giá được -> Allow không khớp
        ("eq", 100, 100.0, True),
        ("eq", 100, "100", False),
    ],
)
def test_numeric_ops(op: str, right: Any, amount: Any, allowed: bool) -> None:
    e = ev(statement(conditions=[condition("resource.amount", op, right)]))
    assert e.evaluate(with_resource(request(), amount=amount)).allowed is allowed


def test_in_op_on_text() -> None:
    e = ev(statement(conditions=[condition("resource.department", "in", ["sales", "hr"])]))
    assert e.evaluate(request()).allowed
    assert not e.evaluate(with_resource(request(), department="it")).allowed


def test_deny_fails_closed_when_attribute_missing_or_mistyped() -> None:
    allow = statement("allow", subjects=["*"])
    deny = statement(
        "deny", effect="Deny", subjects=["*"], conditions=[condition("resource.amount", "ge", 10)]
    )
    e = ev(allow, deny)
    assert not e.evaluate(with_resource(request(), amount=10)).allowed
    assert e.evaluate(with_resource(request(), amount=9)).allowed
    for broken in ({"owner": "alice"}, {"amount": "10"}, {"amount": float("nan")}):
        d = e.evaluate(request(resource=broken))
        assert (d.allowed, d.reason) == (False, "explicit_deny"), broken


def test_allow_never_matches_on_indeterminate_condition() -> None:
    e = ev(statement(conditions=[condition("resource.amount", "le", 10)]))
    assert e.evaluate(request(resource={"owner": "alice"})).reason == "implicit_deny"


def test_policy_tenant_xrn_and_action_bounds() -> None:
    e = ev(statement(subjects=["*"], actions=["*"], resources=["*"]))
    assert e.evaluate(request(xrn="xrn:acme:" + "x" * 300 + ":1")).reason == "invalid_xrn"
    assert e.evaluate(request(action="a" * 300)).reason == "unknown_action"


def test_actor_id_cannot_impersonate_role_namespace() -> None:
    e = ev(statement(subjects=["role:staff"], actions=["*"]))
    assert not e.evaluate(request(actor=actor(id="role:staff", roles=()))).allowed


# ---- explain -------------------------------------------------------------------------------
def test_explain_reports_actual_vs_expected_and_near_miss() -> None:
    e = ev(
        statement(
            "limit", conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})]
        ),
        statement("other", subjects=["role:nobody"]),
    )
    why = e.explain(with_resource(request(), amount=6000))
    assert why.allowed is False
    assert why.stage == "policy"
    assert why.near_misses == ("limit",)
    t = {t.id: t for t in why.traces}
    assert t["limit"].subject and t["limit"].action and t["limit"].resource
    assert t["limit"].conditions is False
    c = t["limit"].condition_details[0]
    assert (c.actual, c.expected, c.passed) == (6000, 5000, False)
    assert not t["other"].subject
    assert why.model_dump(mode="json")["traces"][0]["id"] == "limit"


def test_explain_shows_missing_attribute_error() -> None:
    e = ev(statement(conditions=[condition("resource.amount", "le", 1)]))
    c = e.explain(request(resource={})).traces[0].condition_details[0]
    assert c.passed is None
    assert c.error == "missing_or_incomparable"
    assert c.actual is None


def test_explain_boundary_stage() -> None:
    why = ev(statement()).explain(request(xrn="xrn:other:expense:1"))
    assert (why.stage, why.reason) == ("boundary", "tenant_boundary")
    assert why.detail["xrn_tenant"] == "other"


# ---- PEP -----------------------------------------------------------------------------------
def host(*statements: dict[str, Any]) -> Host:
    base = statements or (statement(subjects=["*"], actions=["*"], resources=["*"]),)
    h = Host(ev(*base), actors={"alice": actor(), "boss": actor(id="boss", roles=("approver",))})
    return h


def test_guard_rejects_malformed_attributes() -> None:
    h = host()
    for res in ({"owner": ""}, {"owner": 5}, {"amount": -1}, {"amount": True}, {"amount": None}):
        d = h.evaluate(request(resource=res))
        assert d.reason == "invalid_attributes", res
    assert h.evaluate(request(action="record.approve", resource={"owner": "x"})).reason == (
        "invalid_attributes"
    )


def test_every_catalog_action_is_known_to_guard() -> None:
    h = host()
    for action in DEFAULT_ACTION_LEVELS:
        assert h.evaluate(request(action=action, resource={"owner": "x", "creator": "y"})).allowed


def test_human_only_action_is_never_allowed_for_agents() -> None:
    bot = actor(id="bot", kind="agent", roles=("worker",), behalf="alice", autonomy=3)
    h = host()
    h.actors["bot"] = bot
    r = request(
        actor=bot, action="request.decide", resource={"owner": "x", "creator": "y"}, approval="ok"
    )
    h.approvals["ok"] = receipt(r, by="boss")
    d = h.evaluate(r)
    assert (d.allowed, d.reason) == (False, "human_only")
    # Người thì được (SoD vẫn áp dụng).
    human = replace(r, actor=actor())
    assert h.evaluate(human).allowed
    assert h.evaluate(with_resource(human, creator="alice")).reason == "self_approval"


def test_guard_explain_covers_every_denial_stage() -> None:
    bot = actor(id="bot", kind="agent", roles=("worker",), behalf="alice", autonomy=0)
    h = host()
    h.actors["bot"] = bot
    why = h.guard.explain(request(actor=bot, action="record.update"))
    assert why.stage == "autonomy"
    assert why.detail == {"required_level": "L1", "ceiling": "L0"}
    why = h.guard.explain(request(xrn="xrn:other:a:b"))
    assert why.stage == "boundary"
    ok = h.guard.explain(request())
    assert ok.allowed and ok.stage == "policy" and ok.traces
    deny = Host(ev(statement(subjects=["*"], actions=["*"], resources=["*"], effect="Deny")))
    deny.actors["alice"] = actor()
    why = deny.guard.explain(request())
    assert why.reason == "explicit_deny"
    assert why.statements == ("base",)
    details = access_denied_details(why, "explain:deny-01")
    assert details["explain_ref"] == "explain:deny-01"
    assert details["statements"] == ["base"]
    assert "traces" not in details


def test_approval_binding_reports_mismatched_fields() -> None:
    bot = actor(id="bot", kind="agent", roles=("worker",), behalf="alice", autonomy=2)
    h = host()
    h.actors["bot"] = bot
    res = {"owner": "bob", "creator": "carol", "amount": 1}
    r = request(actor=bot, action="record.approve", resource=res, approval="ok")
    h.approvals["ok"] = receipt(r, by="boss")
    d = h.evaluate(replace(r, revision=9))
    assert d.reason == "approval_binding"
    assert d.detail["mismatched"] == ["revision"]


def test_expired_receipt_flagged() -> None:
    bot = actor(id="bot", kind="agent", roles=("worker",), behalf="alice", autonomy=2)
    h = host()
    h.actors["bot"] = bot
    res = {"owner": "bob", "creator": "carol", "amount": 1}
    r = request(actor=bot, action="record.approve", resource=res, approval="ok", now=300)
    h.approvals["ok"] = receipt(r, by="boss", expires=300)
    d = h.evaluate(r)
    assert d.reason == "approval_binding"
    assert d.detail["expired"] is True
