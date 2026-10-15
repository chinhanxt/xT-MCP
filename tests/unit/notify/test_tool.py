# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""The notify handler shares the existing dispatcher security and replay pipeline."""

from dataclasses import dataclass, replace

import pytest

from tests.unit.tools.fakes import World, agent_ctx, later, make_dispatcher, make_world
from xt.application.tools import ConfirmationService
from xt.application.tools.context import CallContext
from xt.application.tools.errors import ToolError, not_found
from xt.application.tools.ports import Args, CommitResult, MutationPlan, PendingStatus
from xt.contracts.tools.notify import NOTIFY
from xt.domain.autonomy import RiskLevel


@dataclass
class Notifications:
    plans: int = 0
    commits: int = 0

    def plan_notify(self, ctx: CallContext, args: Args) -> MutationPlan:
        self.plans += 1
        if args["recipients"] == ["person:missing"]:
            raise not_found("person:missing")
        return MutationPlan(
            RiskLevel.L2,
            "Gửi thông báo nội bộ",
            {"recipients": args["recipients"], "message": args["message"]},
        )

    def commit_notify(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        self.commits += 1
        return CommitResult(dict(NOTIFY.sample_output), "notification:test", "queued")


def configured() -> tuple[World, Notifications]:
    world = make_world()
    port = Notifications()
    world.services = replace(world.services, notifications=port)
    return world, port


def test_notify_iam_runs_before_plan_or_confirmation() -> None:
    world, port = configured()
    world.access.deny_tools["notify"] = "implicit_deny"
    result = make_dispatcher(world).call("notify", NOTIFY.examples[0], agent_ctx())
    assert result.envelope["error"]["code"] == "access_denied"
    assert world.access.checks[-1].action == "notify.send"
    assert port.plans == port.commits == 0


def test_notify_l2_confirmation_and_same_key_replay() -> None:
    world, port = configured()
    dispatcher = make_dispatcher(world)
    pending = dispatcher.call("notify", NOTIFY.examples[0], agent_ctx())
    assert pending.envelope["error"]["code"] == "confirmation_required"
    assert port.commits == 0
    details = pending.envelope["error"]["details"]
    confirmed = ConfirmationService(dispatcher).confirm(
        details["confirmation_id"], "sub-viet", later(1), "tr_desk"
    )
    assert confirmed.status is PendingStatus.CONFIRMED
    replayed = dispatcher.call("notify", NOTIFY.examples[0], agent_ctx(now=later(2)))
    assert not replayed.is_error
    assert replayed.envelope["meta"]["effective_level"] == "L2"
    assert replayed.envelope["meta"]["receipt"]["replayed"] is True
    assert replayed.envelope["meta"]["receipt"]["commit_state"] == "queued"
    assert port.commits == 1


def test_notify_authorized_l2_direct_send_and_conflicting_replay() -> None:
    world, port = configured()
    world.access.ceiling = RiskLevel.L2
    dispatcher = make_dispatcher(world)
    first = dispatcher.call("notify", NOTIFY.examples[0], agent_ctx())
    assert not first.is_error
    second = dispatcher.call("notify", {**NOTIFY.examples[0], "message": "Khác"}, agent_ctx())
    assert second.envelope["error"]["code"] == "idempotency_conflict"
    assert port.commits == 1


def test_notify_rejects_unknown_recipient_and_agent_approval_flags() -> None:
    world, port = configured()
    dispatcher = make_dispatcher(world)
    missing = dispatcher.call(
        "notify", {**NOTIFY.examples[0], "recipients": ["person:missing"]}, agent_ctx()
    )
    assert missing.envelope["error"]["code"] == "not_found"
    invalid = dispatcher.call("notify", {**NOTIFY.examples[0], "approved": True}, agent_ctx())
    assert invalid.envelope["error"]["code"] == "invalid_request"
    assert port.commits == 0


def test_notify_cannot_write_masked_pii_tokens_back() -> None:
    world, port = configured()
    result = make_dispatcher(world).call(
        "notify", {**NOTIFY.examples[0], "message": "[PII:masked-value]"}, agent_ctx()
    )
    assert result.envelope["error"]["details"]["reason"] == "masked_value"
    assert port.commits == 0


def test_composition_canonicalizes_tenant_refs_and_deduplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock
    from uuid import uuid4

    from xt.application.notify.ports import Person
    from xt.entrypoints.composition import notify as module

    user = Person(uuid4(), "person:b", "B")
    caller = SimpleNamespace(alias="acme", actor=object(), person=SimpleNamespace(id=user.id))
    runtime = Mock()
    runtime.caller.return_value = caller
    repo = Mock()
    repo.resolve_person.return_value = user
    monkeypatch.setattr(module, "PgNotificationRepository", Mock(return_value=repo))
    adapter = module.NotificationToolAdapter(runtime)
    plan = adapter.plan_notify(
        agent_ctx(),
        {
            "recipients": ["person:b", "xrn:acme:person:b", "me"],
            "message": "Nộp báo cáo",
            "idempotency_key": "notification-test-0001",
        },
    )
    assert plan.level is RiskLevel.L2
    assert len(plan.payload["recipients"]) == 1
    assert repo.resolve_person.call_args_list[1].args == ("person:b",)
    assert repo.resolve_person.call_args_list[2].args == (f"person:{user.id}",)
    # A renamed/reused account key cannot redirect an operation awaiting confirmation.
    repo.resolve_person.return_value = Person(uuid4(), user.ref, user.name)
    with pytest.raises(ToolError):
        adapter.commit_notify(agent_ctx(), plan)
    repo.enqueue.assert_not_called()


def test_composition_rejects_other_tenant_before_repository_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from xt.entrypoints.composition import notify as module

    runtime = Mock()
    runtime.caller.return_value = SimpleNamespace(alias="acme", actor=object())
    repo = Mock()
    monkeypatch.setattr(module, "PgNotificationRepository", Mock(return_value=repo))
    with pytest.raises(ToolError) as caught:
        module.NotificationToolAdapter(runtime).plan_notify(
            agent_ctx(), {**NOTIFY.examples[0], "recipients": ["xrn:other:person:b"]}
        )
    assert caught.value.code.value == "not_found"
    repo.resolve_person.assert_not_called()
