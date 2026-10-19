# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ca tấn công từ audit IAM (docs/archive/audit-iam.md). Mỗi test mô tả hành vi ĐÚNG (đã sửa, hết xfail)
cho tới khi sửa. Sửa xong thì test chuyển XPASS -> fail: khi đó xóa marker tương ứng."""

from __future__ import annotations

import datetime
import json
from dataclasses import replace

import pytest

from tests.unit.iam.support import Host, actor, document, receipt, request, statement
from xt.application.iam import PolicyApprovalError, PolicyStore
from xt.domain.iam import PolicyDocument, PolicyEvaluator

ALLOW_ALL = statement("all", subjects=["*"], actions=["*"], resources=["*"])


def _host(*stmts: dict, **actors) -> Host:
    ev = PolicyEvaluator(PolicyDocument.model_validate(document(*(stmts or (ALLOW_ALL,)))))
    people = {"alice": actor(), "carol": actor(id="carol"), "admin": actor(id="admin")}
    return Host(ev, {**people, **actors})


@pytest.mark.parametrize("kind", ["Agent", "AGENT", "service", "", "bot"])
def test_b1_unknown_kind_is_not_treated_as_human(kind: str) -> None:
    host = _host()
    bot = actor(id="bot", kind=kind, autonomy=0, behalf=None)
    for action in ("request.decide", "record.delete", "record.approve"):
        req = request(
            actor=bot, action=action, resource={"owner": "carol", "creator": "carol", "amount": 1}
        )
        assert not host.evaluate(req).allowed, (kind, action)


@pytest.mark.parametrize("where", ["amount", "limit", "literal"])
def test_b2_huge_int_fails_closed_without_exception(where: str) -> None:
    big = 10**400
    right = {"ref": "subject.limit"} if where == "limit" else (big if where == "literal" else 100)
    cond = {"left": "resource.amount", "op": "ge", "right": right}
    deny = statement(
        "deny-big", effect="Deny", subjects=["*"], actions=["*"], resources=["*"], conditions=[cond]
    )
    try:
        host = _host(ALLOW_ALL, deny)
    except ValueError:
        return  # từ chối literal không biểu diễn được ngay lúc validate cũng là cách sửa hợp lệ
    res = {"owner": "alice", "creator": "carol", "amount": big if where == "amount" else 1}
    who = actor(limit=big) if where == "limit" else actor()
    req = request(actor=who, action="record.update", resource=res)
    d = host.evaluate(req)  # không được ném exception
    if where != "limit":  # amount/literal khổng lồ: Deny phải khớp hoặc indeterminate -> chặn
        assert not d.allowed


def test_b3_default_now_does_not_revive_expired_receipt() -> None:
    host = _host()
    bot = actor(id="bot", kind="agent", behalf="alice", autonomy=3)
    try:
        base = request(
            actor=bot,
            action="record.approve",
            approval="k",
            resource={"owner": "bob", "creator": "bob", "amount": 1},
        )
        req = replace(base, now=0)
        req = type(req)(**{f: getattr(req, f) for f in req.__dataclass_fields__ if f != "now"})
    except TypeError:
        return  # `now` bắt buộc: cách sửa hợp lệ
    host.approvals["k"] = receipt(req, by="admin", expires=1)  # hết hạn từ lâu (epoch 1)
    assert not host.evaluate(req).allowed


def test_b4_duplicate_json_keys_rejected() -> None:
    raw = json.dumps(document(statement("x", effect="Deny"))).replace(
        '"effect": "Deny"', '"effect": "Deny", "effect": "Allow"'
    )
    assert raw.count('"effect"') == 2
    with pytest.raises(ValueError):
        PolicyDocument.model_validate_json(raw)


def test_b5_policy_store_sod_uses_effective_human() -> None:
    doc = PolicyDocument.model_validate(document(ALLOW_ALL))
    proposer = actor(id="bot", kind="agent", behalf="alice")
    store = PolicyStore(doc, proposer)  # type: ignore[arg-type]
    with pytest.raises(PolicyApprovalError):
        store.approve(actor(id="alice", roles=("iam_admin",)))


def test_b6_explain_does_not_leak_approver_attributes() -> None:
    lim = statement(
        "lim",
        subjects=["*"],
        actions=["record.approve"],
        resources=["*"],
        conditions=[{"left": "resource.amount", "op": "le", "right": {"ref": "subject.limit"}}],
    )
    host = _host(lim, carol=actor(id="carol", limit=31337), alice=actor(limit=10**6))
    bot = actor(id="bot", kind="agent", behalf="alice", limit=None)
    req = request(
        actor=bot,
        action="record.approve",
        approval="k",
        now=5,
        resource={"owner": "bob", "creator": "bob", "amount": 50000},
    )
    host.approvals["k"] = receipt(req, by="carol", expires=50)
    ex = host.guard.explain(req)
    assert ex.reason == "approver_permission"
    assert "31337" not in ex.model_dump_json()


def test_b7_non_json_resource_fails_closed_without_exception() -> None:
    host = _host()
    bot = actor(id="bot", kind="agent", behalf="alice")
    req = request(
        actor=bot,
        action="record.approve",
        approval="k",
        resource={
            "owner": "bob",
            "creator": "bob",
            "amount": 1,
            "created": datetime.datetime(2026, 1, 1),
        },
    )
    host.approvals["k"] = receipt(replace(req, resource={"owner": "bob"}), by="admin")
    assert not host.evaluate(req).allowed
