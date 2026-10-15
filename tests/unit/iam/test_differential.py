# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Oracle vi sai: evaluator thật và pycasbin phải cho cùng quyết định trên request ngẫu nhiên."""

from __future__ import annotations

import random
from typing import Any

from tests.unit.iam.support import CasbinOracle, actor, document, request, statement
from tests.unit.iam.test_spike_cases import BASE
from xt.domain.iam import PolicyDocument, PolicyEvaluator

ROLES = ["staff", "lead", "manager", "approver", "worker", "iam_admin", "none"]
ACTIONS = ["record.read", "record.update", "record.approve", "record.delete", "x.y"]
XRNS = ["xrn:acme:expense:42", "xrn:acme:task:7", "xrn:acme:expense:9", "xrn:other:expense:1"]
NAMES = ["alice", "bob", "carol", "boss", "role:staff"]


def _noise() -> list[dict[str, Any]]:
    return [
        statement(
            "deny-delete",
            effect="Deny",
            subjects=["*"],
            actions=["record.delete"],
            resources=["xrn:acme:task:*"],
        ),
        statement(
            "deny-big",
            effect="Deny",
            subjects=["role:staff"],
            actions=["record.u*"],
            conditions=[{"left": "resource.amount", "op": "ge", "right": 9000}],
        ),
        statement(
            "dept-in",
            subjects=["role:none"],
            actions=["*"],
            resources=["*"],
            conditions=[{"left": "resource.department", "op": "in", "right": ["sales", "hr"]}],
        ),
        statement("by-id", subjects=["bob"], actions=["record.read"], resources=["xrn:acme:*"]),
    ]


def test_native_and_casbin_agree_on_random_requests() -> None:
    rng = random.Random(20261015)
    doc = PolicyDocument.model_validate({**BASE, "statements": [*BASE["statements"], *_noise()]})
    native, oracle = PolicyEvaluator(doc), CasbinOracle(doc)
    allowed = denied = 0
    for i in range(600):
        who = actor(
            id=f"{rng.choice(NAMES)}{i % 3}" if rng.random() < 0.8 else rng.choice(NAMES),
            roles=tuple(rng.sample(ROLES, rng.randint(0, 3))),
            tenant="acme" if rng.random() < 0.95 else "other",
            team=rng.choice(["red", "blue", None]),
            department=rng.choice(["sales", "hr", "it"]),
            limit=rng.choice([0, 100, 5000, 9000]),
        )
        resource: dict[str, Any] = {
            "owner": rng.choice(NAMES),
            "creator": rng.choice(NAMES),
            "amount": rng.choice([0, 5000, 5001, 9000, 12000, "7", None]),
            "team": rng.choice(["red", "blue"]),
            "department": rng.choice(["sales", "hr", "it"]),
        }
        req = request(
            actor=who, action=rng.choice(ACTIONS), xrn=rng.choice(XRNS), resource=resource
        )
        a, b = native.evaluate(req), oracle.evaluate(req)
        assert (a.allowed, a.reason) == (b.allowed, b.reason), req
        if a.allowed:
            assert set(a.statements) == set(b.statements), req
        else:  # casbin dừng ở Deny đầu tiên nên có thể thấy ít statement hơn
            assert set(b.statements) <= set(a.statements), req
        allowed += a.allowed
        denied += not a.allowed
    assert allowed > 50
    assert denied > 50


def test_deny_wins_regardless_of_statement_order() -> None:
    rng = random.Random(7)
    base = [statement("a1"), statement("a2", subjects=["alice"]), statement("d", effect="Deny")]
    for _ in range(10):
        rng.shuffle(base)
        for engine in (PolicyEvaluator, CasbinOracle):
            d = engine(PolicyDocument.model_validate(document(*base))).evaluate(request())
            assert (d.allowed, d.reason) == (False, "explicit_deny")
