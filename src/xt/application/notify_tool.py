# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""MCP notification handler; the dispatcher owns IAM, autonomy, receipts and transactions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from xt.application.tools.context import CallContext
from xt.application.tools.errors import capability_unavailable
from xt.application.tools.ports import Args, CommitResult, MutationPlan

if TYPE_CHECKING:
    from xt.application.tools.handlers import ToolServices


class NotificationToolPort(Protocol):
    """Plan without sending; commit writes only the inbox and delivery outbox."""

    def plan_notify(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def commit_notify(self, ctx: CallContext, plan: MutationPlan) -> CommitResult: ...


def _port(services: ToolServices) -> NotificationToolPort:
    port = services.notifications
    if port is None:
        raise capability_unavailable(
            "notify", "v1.0", "Configure the xT notification service before sending notices."
        )
    return port


def plan_notify(s: ToolServices, ctx: CallContext, args: Args) -> MutationPlan:
    return _port(s).plan_notify(ctx, args)


def commit_notify(s: ToolServices, ctx: CallContext, plan: MutationPlan) -> CommitResult:
    return _port(s).commit_notify(ctx, plan)
