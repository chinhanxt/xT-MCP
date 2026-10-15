# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tenant-scoped MCP notification composition. External delivery belongs to the worker."""

from __future__ import annotations

from typing import Any

from xt.adapters.db.notify import PgNotificationRepository
from xt.application.notify.service import NotificationService
from xt.application.tools.context import CallContext
from xt.application.tools.errors import not_found
from xt.application.tools.ports import Args, CommitResult, MutationPlan
from xt.domain.autonomy import RiskLevel
from xt.entrypoints.composition.runtime import Runtime
from xt.entrypoints.composition.util import parse_record_ref


class NotificationToolAdapter:
    def __init__(self, rt: Runtime, *, confirm_base_url: str | None = None) -> None:
        self.rt, self.confirm_base_url = rt, confirm_base_url

    def _repo(self, ctx: CallContext) -> PgNotificationRepository:
        return PgNotificationRepository(self.rt.uow, self.rt.caller(ctx).actor)

    def plan_notify(self, ctx: CallContext, args: Args) -> MutationPlan:
        repo = self._repo(ctx)
        caller = self.rt.caller(ctx)
        recipients = []
        seen = set()
        for value in args["recipients"]:
            ref = f"person:{caller.person.id}" if value == "me" else str(value)
            person = repo.resolve_person(ref)
            if person is None:
                raise not_found(str(value))
            if person.id not in seen:
                seen.add(person.id)
                recipients.append(person)
        subject_ref = args.get("subject_ref")
        if subject_ref:
            # An inaccessible subject cannot become a side channel to another tenant/record.
            model, rid = parse_record_ref(ctx, str(subject_ref))
            if self.rt.records.get(caller.actor, model, rid) is None:
                raise not_found(str(subject_ref))
        return MutationPlan(
            level=RiskLevel.L2,
            summary=f"Gửi thông báo nội bộ tới {len(recipients)} người.",
            target_ref=str(subject_ref) if subject_ref else None,
            digest_source={
                "recipients": [p.ref for p in recipients],
                "message": args["message"],
                "subject_ref": subject_ref,
            },
            payload={"recipients": recipients, "args": dict(args)},
        )

    def commit_notify(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        payload: dict[str, Any] = plan.payload
        args = payload["args"]
        caller = self.rt.caller(ctx)
        # Re-resolve after human confirmation: membership may have changed in the meantime.
        repo = self._repo(ctx)
        recipients = []
        for previous in payload["recipients"]:
            current = repo.resolve_person(previous.ref)
            if current is None:
                raise not_found(previous.ref)
            recipients.append(current)
        service = NotificationService(repo, confirm_base_url=self.confirm_base_url)
        rows = service.send(
            actor_id=caller.person.id,
            recipients=recipients,
            message=str(args["message"]),
            idempotency_key=f"tool:{ctx.actor.key}:{args['idempotency_key']}",
            subject_ref=args.get("subject_ref"),
        )
        return CommitResult(
            data={
                "notifications": [
                    {
                        "ref": f"notification:{n.id}",
                        "recipient": {"ref": n.recipient_ref, "name": n.recipient_name},
                        "status": n.status,
                    }
                    for n in rows
                ]
            },
            operation_ref=f"notification:{rows[0].id}",
            commit_state="queued",
        )
