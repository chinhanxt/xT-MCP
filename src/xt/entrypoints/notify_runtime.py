# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Notification event relay. Telegram delivery runs after the inbox transaction commits."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any
from uuid import UUID

from xt.adapters.db.integration import tenant_by_id
from xt.adapters.db.notification_sources import NotificationSources
from xt.adapters.db.notify import PgNotificationRepository
from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.adapters.notify.telegram import HttpxTelegramTransport
from xt.application.notify.service import NotificationService, comparison_code
from xt.application.notify_events import NotifyEvent, notification_event
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.domain.model.context import ModelActor
from xt.entrypoints.composition.process import ProcessContext
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE, Runtime

log = logging.getLogger(__name__)


def telegram_sender(env: Mapping[str, str]) -> HttpxTelegramTransport | None:
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    return HttpxTelegramTransport(
        token,
        desk_base_url=env.get("XT_DESK_URL", ""),
        api_base_url=env.get("TELEGRAM_API_BASE_URL", "https://api.telegram.org"),
    )


def _process_event(row: Mapping[str, Any]) -> NotifyEvent | None:
    if not isinstance(row["payload"], Mapping):
        raise ValueError("Notification event payload must be an object")
    return notification_event(
        str(row["id"]), row["type"], row["payload"], subject_ref=row.get("ref")
    )


def _confirmation_event(cid: str, approved: int, payload: Mapping[str, Any]) -> NotifyEvent | None:
    return notification_event(
        f"{cid}:{approved}",
        "confirmation_required",
        {**payload, "confirmation_code": comparison_code(cid, str(payload["confirmation_code"]))},
    )


class NotificationRelay:
    def __init__(self, rt: Runtime, engine: Any, env: Mapping[str, str]) -> None:
        self.rt, self.engine = rt, engine
        self.desk_url = env.get("XT_DESK_URL", "").rstrip("/")
        self.sender = telegram_sender(env)

    def run(self, tenant_id: UUID) -> int:
        tenant = tenant_by_id(self.rt.app_db, tenant_id)
        if tenant is None:
            raise LookupError("Unknown notification tenant")
        actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-worker")

        def enqueue() -> None:
            repo = PgNotificationRepository(self.rt.uow, actor)
            sources = NotificationSources(self.rt.uow, actor)
            service = NotificationService(repo, confirm_base_url=self.desk_url)

            def send(key: str, build: Callable[[], NotifyEvent | None]) -> None:
                def enqueue_one() -> None:
                    # Each source is isolated: a recipient quota must not roll back the batch.
                    if not sources.claim(key):
                        return
                    event = build()
                    if event is None:
                        return  # Permanently unassigned/irrelevant sources are checkpointed.
                    recipients = [repo.resolve_person(ref) for ref in event.recipients]
                    active = [person for person in recipients if person is not None]
                    if active:
                        service.send(
                            actor_id=NIL,
                            recipients=active,
                            message=event.message,
                            idempotency_key=event.idempotency_key,
                            kind=event.kind,
                            subject_ref=event.subject_ref,
                            confirmation_code=event.confirmation_code,
                            confirm_url=event.confirm_url,
                        )

                try:
                    self.rt.uow.run(enqueue_one)
                except ToolError as error:
                    if error.code == ErrorCode.RATE_LIMITED:
                        # Savepoint rolled back the source claim. Retry it in a later relay.
                        log.info("Notification source deferred: recipient rate limit")
                        return
                    if error.code != ErrorCode.INVALID_REQUEST:
                        raise
                    self.rt.uow.run(lambda: sources.claim(key))
                    log.warning("Malformed notification source checkpointed: invalid_request")
                except (ValueError, TypeError, KeyError):
                    self.rt.uow.run(lambda: sources.claim(key))
                    # Never log free text, recipient identities, locator URLs or link codes.
                    log.warning("Malformed notification source checkpointed: invalid_payload")

            for row in sources.process_events():
                send(
                    f"event:{row['id']}",
                    partial(_process_event, row),
                )
            for row in sources.pending_confirmations():
                pending = row["pending"]
                approved = len(pending.get("approvals", []))
                confirmers = pending.get("confirmers", [])
                if approved >= len(confirmers):
                    continue
                cid = str(row["confirmation_id"])
                payload = {
                    "recipients": [confirmers[approved]["ref"]],
                    "message": "Có thao tác đang chờ bạn xác nhận trên Desk.",
                    "confirmation_code": pending.get("plan_digest", ""),
                    "confirm_url": f"{self.desk_url}/desk/confirm/{cid}",
                }
                send(
                    f"confirm:{cid}:{approved}",
                    partial(_confirmation_event, cid, approved, payload),
                )
            profile = self.rt.store.profile(actor)
            if profile is None:
                return
            pc = ProcessContext(self.rt, self.engine, actor, profile.timezone)
            store = PgWorkflowStore(self.rt.uow)
            for iid in sources.open_instances():
                for task in pc.service().get(iid).get("tasks", []):
                    for candidate in task.get("candidates", []):
                        key = f"assignment:{iid}:{task['task_id']}:{candidate}"
                        send(
                            key,
                            partial(
                                notification_event,
                                hashlib.sha256(key.encode()).hexdigest(),
                                "assignment",
                                {
                                    "recipients": [f"person:{candidate}"],
                                    "message": "Bạn có công việc mới trong xT.",
                                },
                                subject_ref=store.ref_for(iid),
                            ),
                        )

        with self.rt.uow.scope(actor):
            self.rt.uow.run(enqueue)
        # Each repository operation commits separately: the claim and pii_egress survive a
        # network timeout or crash. Ambiguous sends are never retried automatically.
        repository = PgNotificationRepository(self.rt.app_db, actor)
        return NotificationService(repository, self.sender).deliver_pending()
