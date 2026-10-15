# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Handler thật cho `xt worker` (`XT_WORKER_HANDLERS=xt.entrypoints.composition.worker:handlers`).

- `workflow.relay_timers` (mỗi `XT_WORKFLOW_RELAY_SECONDS`, mỗi tenant; chuỗi tự hẹn lại do
  `xt tenant bind-queue` khởi động): chuyển outbox hẹn giờ (`PgTimerScheduler` ghi cùng
  transaction của bước quy trình) sang `JobQueue.schedule_at`, khóa `(instance, due)`.
- `model.apply` (đề xuất model đã duyệt qua `decide_request`): `approve_and_apply` (pha 1) +
  `PgMigrator.run_jobs` (pha 2); relay cũng đẩy pha 2 còn dở mỗi chu kỳ.
- `workflow.tick` (đúng hạn): `ProcessService.tick(instance)` trong một transaction tenant
  (SLA leo thang, thông báo, sự kiện/sự cố vào `xt_meta.process_event`).

Biến: `XT_WORKER_DATABASE_URL` (xt_app), `XT_QUEUE_DATABASE_URL` (xt_control), `XT_AUDIT_KEY_FILE`,
`XT_AUDIT_KEY_ID`, `XT_WORKFLOW_RELAY_SECONDS`, `XT_DB_MIGRATOR_URL` (pha 1/2 của model
engine). Thiếu -> lỗi lúc nạp handler, không có mặc định.
"""

from __future__ import annotations

import os
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import anyio.to_thread

from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.integration import tenant_by_id
from xt.adapters.db.model_changes import APPLY_TASK, relay_model_changes
from xt.adapters.db.workflow_bridge import relay_timers, tenant_async_tx
from xt.adapters.queue.runtime import create_runtime
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.application.jobs import JobQueue, JobRequest
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.process.errors import EngineError
from xt.entrypoints.composition.process import ProcessContext
from xt.entrypoints.composition.runtime import NIL, WORKFLOW_ROLE, Runtime

TICK = "workflow.tick"
RELAY = "workflow.relay_timers"
APPLY = APPLY_TASK
Handler = Callable[[Any, JobRequest], Awaitable[None]]


def _required(env: Mapping[str, str], name: str) -> str:
    v = env.get(name, "").strip()
    if not v:
        raise ValueError(f"{name} is required")
    return v


class WorkflowJobs:
    def __init__(
        self,
        rt: Runtime,
        queue: JobQueue,
        tenant_dsn: str,
        *,
        relay_interval_s: int,
        files: Any = None,
    ) -> None:
        if relay_interval_s < 5:
            raise ValueError("relay interval must be >= 5 seconds")
        self.rt, self.queue, self.tenant_dsn = rt, queue, tenant_dsn
        self.relay_interval_s = relay_interval_s
        self.files: Any = files  # CompositionFilesBackend khi bật files + ClamAV
        self.engine = SpiffProcessEngine()

    def tick_sync(self, tenant_id: UUID, instance_id: str) -> dict[str, Any]:
        tenant = tenant_by_id(self.rt.app_db, tenant_id)
        if tenant is None:
            raise LookupError("unknown tenant")
        actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-worker")

        def work() -> dict[str, Any]:
            profile = self.rt.store.profile(actor)
            if profile is None:
                raise LookupError("tenant has no profile")
            pc = ProcessContext(self.rt, self.engine, actor, profile.timezone)
            try:
                return pc.service().tick(instance_id)
            except EngineError as e:
                # ProcessService đã lưu meta `incidents`; thêm sự kiện rồi để transaction commit.
                self.rt.store.process_event(
                    actor, instance_id, "engine.error", {"error": str(e)[:500], "reason": e.reason}
                )
                return {"instance": instance_id, "status": "incident"}

        with self.rt.uow.scope(actor):
            return self.rt.uow.run(work)

    async def tick(self, connection: Any, job: JobRequest) -> None:
        await anyio.to_thread.run_sync(
            self.tick_sync, job.tenant_id, str(job.payload["instance_id"])
        )

    def _tenant(self, tenant_id: UUID) -> TenantRef:
        tenant = tenant_by_id(self.rt.app_db, tenant_id)
        if tenant is None:
            raise LookupError("unknown tenant")
        return tenant

    def apply_sync(self, tenant_id: UUID, proposal_id: UUID) -> dict[str, Any]:
        """Áp đề xuất model đã duyệt: pha 1 (DDL + catalog, transaction của migrator) rồi pha 2
        (backfill/index). Idempotent: đã áp (catalog trùng đề xuất) -> chỉ ghi trạng thái."""
        tenant = self._tenant(tenant_id)
        actor = ModelActor(tenant, NIL, (WORKFLOW_ROLE,), client_azp="xt-worker")
        models = self.rt.models

        def read() -> dict[str, Any] | None:
            return self.rt.store.model_change(actor, proposal_id)

        with self.rt.uow.scope(actor):
            row = self.rt.uow.run(read)
        if row is None or row["state"] != "approved":
            return {"proposal": str(proposal_id), "state": row["state"] if row else None}
        state, version, error = "applied", None, None
        try:
            out = models.approve_and_apply(
                tenant,
                proposal_id,
                approved_hash=str(row["plan_hash"]),
                confirm=list(row["confirm"]),
                approved_by=",".join(row["decided_by"]),
            )
            version = out.version
        except ModelError as e:
            p = models.proposals.get(tenant, proposal_id)
            current = models.migrator.current(tenant)
            same = (
                p is not None
                and current is not None
                and current.digest()
                == p.spec.model_copy(update={"version": current.version}).digest()
            )
            if same and current is not None:
                version = current.version  # đã áp ở lần chạy trước (job retry)
            else:
                state, error = "failed", e.message
        if state == "applied":
            stats = models.migrator.run_jobs(tenant.seq)
            if stats.failed or stats.dead:
                error = f"pha 2 chưa xong: failed={stats.failed} dead={stats.dead}"

        def finish() -> None:
            self.rt.store.finish_model_change(
                actor, proposal_id, state=state, applied_version=version, error=error
            )

        with self.rt.uow.scope(actor):
            self.rt.uow.run(finish)
        return {"proposal": str(proposal_id), "state": state, "version": version, "error": error}

    def rescan_files(self, tenant: TenantRef) -> int:
        assert self.files is not None
        actor = ModelActor(tenant, NIL, (), client_azp="xt-worker")
        with self.rt.uow.scope(actor):
            profile = self.rt.uow.run(lambda: self.rt.store.profile(actor))
        return self.files.rescan(profile.alias) if profile else 0

    def purge_reveal_tickets(self, tenant: TenantRef) -> int:
        """Dọn vé xem PII đã hết hạn quá 1 giờ (audit đã ở `pii_egress`, PIIR-08)."""
        actor = ModelActor(tenant, NIL, (), client_azp="xt-worker")
        with self.rt.uow.scope(actor):
            return self.rt.uow.run(lambda: self.rt.store.pii_tickets_purge(actor, 3600))

    async def apply(self, connection: Any, job: JobRequest) -> None:
        await anyio.to_thread.run_sync(
            self.apply_sync, job.tenant_id, UUID(str(job.payload["proposal_id"]))
        )

    async def relay(self, connection: Any, job: JobRequest) -> None:
        """Chuyển outbox (hẹn giờ quy trình, đề xuất model đã duyệt) sang hàng đợi, đẩy tiếp pha 2
        của model engine, rồi tự hẹn lần relay kế tiếp (cùng transaction của job; khóa theo mốc
        thời gian nên không nhân đôi)."""
        await relay_timers(connection, self.queue, job.tenant_id, task_name=TICK)
        await relay_model_changes(connection, self.queue, job.tenant_id, task_name=APPLY)
        tenant = self._tenant(job.tenant_id)
        await anyio.to_thread.run_sync(self.rt.models.migrator.run_jobs, tenant.seq)
        await anyio.to_thread.run_sync(self.purge_reveal_tickets, tenant)
        if self.files is not None:  # ClamAV bật: quét lại tệp `scanning` (clamd từng lỗi)
            await anyio.to_thread.run_sync(self.rescan_files, tenant)
        await self._schedule_relay(connection, job.tenant_id, after=self.relay_interval_s)

    async def _schedule_relay(self, connection: Any, tenant_id: UUID, *, after: int) -> int:
        slot = (int(time.time()) // self.relay_interval_s + (1 if after else 0)) * (
            self.relay_interval_s
        )
        when = datetime.fromtimestamp(slot, UTC)
        return await self.queue.schedule_at(
            connection, JobRequest(tenant_id, RELAY, f"workflow.relay:{slot}", {}), when
        )

    async def register_relay(self, tenant_id: UUID) -> int:
        """Khởi động chuỗi relay của một tenant (idempotent theo mốc thời gian)."""
        async with tenant_async_tx(self.tenant_dsn, tenant_id) as conn:
            return await self._schedule_relay(conn, tenant_id, after=0)


def build_jobs(env: Mapping[str, str]) -> WorkflowJobs:
    tenant_dsn = _required(env, "XT_WORKER_DATABASE_URL")
    queue_dsn = _required(env, "XT_QUEUE_DATABASE_URL")
    key = Path(_required(env, "XT_AUDIT_KEY_FILE")).read_bytes().strip()
    kid = _required(env, "XT_AUDIT_KEY_ID")
    if not kid.isdigit():
        raise ValueError("XT_AUDIT_KEY_ID must be an integer")
    interval = _required(env, "XT_WORKFLOW_RELAY_SECONDS")
    if not interval.isdigit() or int(interval) < 5:
        raise ValueError("XT_WORKFLOW_RELAY_SECONDS must be an integer >= 5")
    migrator_dsn = _required(env, "XT_DB_MIGRATOR_URL")
    audit_key = AuditKey(int(kid), key)
    rt = Runtime(
        app_db=Database(tenant_dsn, min_size=1, max_size=4, audit_key=audit_key),
        migrator_dsn=migrator_dsn,
        audit_key=audit_key,
    )
    holder: dict[str, WorkflowJobs] = {}

    async def tick(c: Any, j: JobRequest) -> None:
        await holder["jobs"].tick(c, j)

    async def relay(c: Any, j: JobRequest) -> None:
        await holder["jobs"].relay(c, j)

    async def apply(c: Any, j: JobRequest) -> None:
        await holder["jobs"].apply(c, j)

    # Chỉ dùng `.queue` để enqueue trong transaction của người gọi (không mở pool/worker).
    enq = create_runtime(
        queue_dsn=queue_dsn,
        tenant_dsn=tenant_dsn,
        handlers={TICK: tick, RELAY: relay, APPLY: apply},
        concurrency=1,
        stalled_seconds=30,
        recover_seconds=5,
        max_attempts=3,
        retry_seconds=5,
    )
    files = None
    if env.get("XT_FILES_CLAMD", "").strip():
        # Profile full (ClamAV): relay mỗi chu kỳ quét lại tệp `scanning`. Cần đủ XT_FILES_*.
        from xt.entrypoints.files_http import CompositionFilesBackend, FilesPep, FilesStack

        files = CompositionFilesBackend(
            rt, FilesStack.build(env, ensure_bucket=False), FilesPep(rt)
        )
    jobs = WorkflowJobs(rt, enq.queue, tenant_dsn, relay_interval_s=int(interval), files=files)
    holder["jobs"] = jobs
    return jobs


def handlers() -> dict[str, Handler]:
    jobs = build_jobs(dict(os.environ))
    return {TICK: jobs.tick, RELAY: jobs.relay, APPLY: jobs.apply}
