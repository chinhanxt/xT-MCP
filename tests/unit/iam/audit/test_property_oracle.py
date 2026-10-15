# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Property-based: evaluator thật vs oracle pycasbin trên policy ngẫu nhiên; glob vs fnmatchcase."""

from __future__ import annotations

import fnmatch

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tests.unit.iam.support import CasbinOracle, actor, request  # noqa: E402
from xt.domain.iam import Glob, PolicyDocument, PolicyEvaluator  # noqa: E402

ALPHA = "ab:.*"
pattern = st.text(alphabet=ALPHA, min_size=1, max_size=10).filter(lambda p: p.count("*") <= 8)
value = st.text(alphabet="ab:.", max_size=12)


@settings(max_examples=3000, deadline=None)
@given(pattern, value)
def test_glob_equals_fnmatchcase(p: str, v: str) -> None:
    assert Glob(p).matches(v) == fnmatch.fnmatchcase(v, p)


ROLES = ["staff", "lead", "x"]
IDS = ["alice", "bob", "carol"]
ACTIONS = ["record.read", "record.update", "record.approve", "task.create"]
XRNS = ["xrn:acme:expense:1", "xrn:acme:task:2", "xrn:acme:expense:3"]
ACT_PAT = st.sampled_from(["*", "record.*", "record.read", "*.create", "record.u*e", "r*d.*"])
RES_PAT = st.sampled_from(["*", "xrn:acme:*", "xrn:acme:expense:*", "xrn:*:task:*", "xrn:acme:*:1"])
SUBJ = st.sampled_from(["*", *[f"role:{r}" for r in ROLES], *IDS])
ATTR_NUM = st.sampled_from(["subject.limit", "resource.amount"])
ATTR_STR = st.sampled_from(
    ["subject.id", "subject.team", "resource.owner", "resource.creator", "resource.team"]
)
lit_str = st.sampled_from(["alice", "bob", "red", "blue"])
lit_num = st.one_of(st.integers(-10, 10_000), st.floats(-10, 10_000, allow_nan=False))

cond = st.one_of(
    st.builds(
        lambda lhs, o, r: {"left": lhs, "op": o, "right": r},
        ATTR_NUM,
        st.sampled_from(["le", "ge", "eq", "ne"]),
        lit_num,
    ),
    st.builds(
        lambda lhs, o, r: {"left": lhs, "op": o, "right": {"ref": r}},
        ATTR_NUM,
        st.sampled_from(["le", "ge"]),
        ATTR_NUM,
    ),
    st.builds(
        lambda lhs, o, r: {"left": lhs, "op": o, "right": r},
        ATTR_STR,
        st.sampled_from(["eq", "ne"]),
        lit_str,
    ),
    st.builds(
        lambda lhs, o, r: {"left": lhs, "op": o, "right": {"ref": r}},
        ATTR_STR,
        st.sampled_from(["eq", "ne"]),
        ATTR_STR,
    ),
    st.builds(
        lambda lhs, r: {"left": lhs, "op": "in", "right": r},
        ATTR_STR,
        st.lists(lit_str, min_size=1, max_size=3),
    ),
    st.builds(
        lambda lhs, o, r: {"left": lhs, "op": o, "right": r}, ATTR_STR, st.just("eq"), lit_num
    ),  # khác kiểu
)


def _stmt(i: int):
    return st.fixed_dictionaries(
        {
            "id": st.just(f"s{i}"),
            "effect": st.sampled_from(["Allow", "Deny"]),
            "subjects": st.lists(SUBJ, min_size=1, max_size=3),
            "actions": st.lists(ACT_PAT, min_size=1, max_size=2),
            "resources": st.lists(RES_PAT, min_size=1, max_size=2),
            "conditions": st.lists(cond, max_size=3),
        }
    )


policy = st.integers(0, 6).flatmap(lambda n: st.tuples(*[_stmt(i) for i in range(n)]))
maybe = lambda s: st.one_of(st.none(), s)  # noqa: E731
req_s = st.fixed_dictionaries(
    {
        "id": st.sampled_from(IDS),
        "roles": st.lists(st.sampled_from(ROLES), max_size=2, unique=True),
        "team": maybe(st.sampled_from(["red", "blue"])),
        "limit": maybe(lit_num),
        "action": st.sampled_from(ACTIONS),
        "xrn": st.sampled_from(XRNS),
        "owner": maybe(st.sampled_from(IDS)),
        "creator": maybe(st.sampled_from(IDS)),
        "rteam": maybe(st.sampled_from(["red", "blue"])),
        "amount": maybe(st.one_of(lit_num, st.just("7"), st.just(float("nan")))),
    }
)


@settings(max_examples=1500, deadline=None)
@given(policy, req_s)
def test_native_matches_casbin_oracle(stmts, r) -> None:
    doc = PolicyDocument.model_validate(
        {"version": "1", "tenant": "acme", "statements": list(stmts)}
    )
    who = actor(id=r["id"], roles=tuple(r["roles"]), team=r["team"], limit=r["limit"])
    res = {
        k: v
        for k, v in {
            "owner": r["owner"],
            "creator": r["creator"],
            "team": r["rteam"],
            "amount": r["amount"],
        }.items()
        if v is not None
    }
    q = request(actor=who, action=r["action"], xrn=r["xrn"], resource=res)
    a, b = PolicyEvaluator(doc).evaluate(q), CasbinOracle(doc).evaluate(q)
    assert (a.allowed, a.reason) == (b.allowed, b.reason)
    if a.allowed:
        assert set(a.statements) == set(b.statements)
    # bất biến: Deny hit => không bao giờ allow; không hit => implicit deny
    ex = PolicyEvaluator(doc).explain(q)
    matched = [t for t in ex.traces if t.matched]
    assert a.allowed == (bool(matched) and all(t.effect == "Allow" for t in matched))
