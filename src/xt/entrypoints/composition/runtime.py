# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lõi của composition root: kho Postgres dùng chung, ánh xạ danh tính và đơn vị công việc.

Một lời gọi tool = MỘT transaction Postgres (xt_app, RLS theo tenant của token): tra receipt,
plan, PendingOperation, mutation, audit và receipt cùng commit hoặc cùng rollback.

Ánh xạ danh tính (architecture-v2 §5.1, docs/identity.md):
- `Principal.kind` suy từ `azp` ở lớp danh tính; ở đây chỉ dùng lại, không đọc lại token;
- agent: người được đại diện = `on_behalf_of` (sub Keycloak) -> `xt_meta.person.subject`;
  vai trò IAM + trần tự chủ của agent lấy từ `xt_meta.agent_grant` do người đó cấp;
- người (client phiên Desk): chính `sub`;
- vai trò nghiệp vụ, đơn vị, quản lý lấy từ bảng tổ chức của xT, không bao giờ từ claim token.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any, TypeVar, cast
from uuid import UUID

from xt.adapters.db.audit import AuditReader
from xt.adapters.db.catalog import PgCatalogReader, PgProposalStore
from xt.adapters.db.confirmations import PgOperationStore, PgSingleUseStore, PgUnitOfWork
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.integration import (
    Person,
    PgIntegrationStore,
    TenantProfile,
    connect,
    tenant_by_alias,
)
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.application.model_engine.service import ModelService as EngineModels
from xt.application.model_engine.service import RecordService as EngineRecords
from xt.application.tools import ToolDispatcher, ToolOutcome
from xt.application.tools.context import Actor, CallContext, PersonRef
from xt.application.tools.errors import ToolError, access_denied
from xt.contracts.tools import ErrorCode
from xt.domain.iam import Actor as IamActor
from xt.domain.model.context import ModelActor, TenantRef

log = logging.getLogger("xt.composition")
T = TypeVar("T")
NIL = UUID(int=0)
WORKFLOW_ROLE = "xt_workflow"


@dataclass(frozen=True, slots=True)
class Caller:
    """Danh tính đã ánh xạ của một lời gọi. `person` là người thật đứng sau lời gọi."""

    alias: str
    profile: TenantProfile
    person: Person
    client_id: str
    is_agent: bool
    autonomy: int
    agent_roles: tuple[str, ...]
    actor: ModelActor

    @property
    def tenant(self) -> TenantRef:
        return self.profile.tenant

    @property
    def person_ref(self) -> PersonRef:
        return PersonRef(f"person:{self.person.id}", self.person.name, self.person.subject)

    def iam(self) -> IamActor:
        if self.is_agent:
            return IamActor(
                id=f"agent:{self.client_id}",
                tenant=self.alias,
                kind="agent",
                roles=self.agent_roles,
                autonomy=self.autonomy,
                behalf=str(self.person.id),
            )
        return iam_person(self.alias, self.person)

    def workflow(self) -> ModelActor:
        """Ngữ cảnh ghi trường `managed_by=workflow`: vai trò dịch vụ xt_workflow, người hành động
        vẫn là `person` (audit ghi actor_id + client_azp)."""
        return replace(self.actor, roles=(WORKFLOW_ROLE,), team_ids=(), department_ids=())


def iam_person(alias: str, p: Person) -> IamActor:
    return IamActor(
        id=str(p.id),
        tenant=alias,
        kind="human",
        roles=tuple(p.roles),
        team=None,
        department=p.unit_key,
        limit=p.limit_amount,
        autonomy=0,
    )


class CallerError(Exception):
    def __init__(self, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error


_CURRENT: ContextVar[tuple[Actor, Caller] | None] = ContextVar("xt_caller", default=None)
# Ghi phải sống sót khi savepoint của commit rollback (sự cố engine: AUD-3, integration-notes).
_AFTER_ROLLBACK: ContextVar[list[Callable[[], None]] | None] = ContextVar(
    "xt_after_rollback", default=None
)


def after_rollback(fn: Callable[[], None]) -> None:
    """Hẹn `fn` chạy trong transaction ngoài của lời gọi, sau khi phần commit đã rollback."""
    pending = _AFTER_ROLLBACK.get()
    if pending is None:
        raise RuntimeError("after_rollback chỉ dùng trong Runtime.run_as")
    pending.append(fn)


class Runtime:
    """Kho và engine dùng chung (một pool xt_app cho cả tiến trình)."""

    def __init__(
        self,
        *,
        app_db: Database,
        migrator_dsn: str | None,
        audit_key: AuditKey | None = None,
        tenant_cache_s: float = 60.0,
    ) -> None:
        self.app_db = app_db
        self.migrator_db = (
            Database(migrator_dsn, min_size=0, max_size=2) if migrator_dsn is not None else None
        )
        self.uow = PgUnitOfWork(app_db)
        db = cast(Database, self.uow)
        self.store = PgIntegrationStore(self.uow)
        self.catalogs = PgCatalogReader(db)
        self.repo = PgRecordRepository(db)
        self.records = EngineRecords(self.catalogs, self.repo)
        self._models: EngineModels | None = None
        if migrator_dsn is not None and self.migrator_db is not None:
            dsn = migrator_dsn
            self._models = EngineModels(
                self.catalogs,
                PgMigrator(lambda: connect(dsn), audit_key=audit_key),
                PgProposalStore(db, self.migrator_db),
            )
        self.audit = AuditReader(db)
        self.operations = PgOperationStore(self.uow, self.tenant_id)
        self.single_use = PgSingleUseStore(self.uow)
        self._tenants: dict[str, tuple[float, TenantRef | None]] = {}
        self._lock = threading.Lock()
        self._ttl = tenant_cache_s

    @property
    def models(self) -> EngineModels:
        if self._models is None:
            raise RuntimeError("tiến trình này không cấu hình XT_DB_MIGRATOR_URL (model engine)")
        return self._models

    def close(self) -> None:
        if self.migrator_db is not None:
            self.migrator_db.close()

    # ---- tenant ---------------------------------------------------------------------------
    def tenant(self, alias: str) -> TenantRef | None:
        now = time.monotonic()
        with self._lock:
            hit = self._tenants.get(alias)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        ref = tenant_by_alias(self.app_db, alias)
        with self._lock:
            if len(self._tenants) > 4096:
                self._tenants.clear()
            self._tenants[alias] = (now, ref)
        return ref

    def tenant_id(self, alias: str) -> UUID:
        ref = self.tenant(alias)
        if ref is None:
            raise ValueError("unknown tenant alias")
        return ref.id

    @staticmethod
    def lookup_actor(tenant: TenantRef) -> ModelActor:
        """Ngữ cảnh RLS chỉ để đọc cấu hình tổ chức của tenant (không vai trò, không ghi)."""
        return ModelActor(tenant, NIL, ())

    # ---- danh tính -------------------------------------------------------------------------
    def resolve(self, actor: Actor, trace_id: str) -> Caller:
        tenant = self.tenant(actor.tenant)
        if tenant is None:
            raise CallerError(
                ToolError(
                    ErrorCode.UNAUTHENTICATED,
                    "Your organization is not registered in xT.",
                    remediation="Ask the xT administrator to register the organization.",
                )
            )
        look = self.lookup_actor(tenant)
        profile = self.store.profile(look)
        if profile is None:  # pragma: no cover - tenant_by_alias đi qua tenant_profile
            raise CallerError(ToolError(ErrorCode.UNAUTHENTICATED, "Organization not found."))
        represented = actor.represented_subject
        if represented is None:
            raise CallerError(
                access_denied(
                    "Service accounts cannot act in xT without a represented person.",
                    "invalid_behalf",
                    None,
                )
            )
        person = self.store.person_by_subject(look, represented)
        if person is None or not person.active:
            raise CallerError(
                access_denied(
                    "Your account is not linked to an active person in this organization.",
                    "invalid_behalf",
                    None,
                    remediation="Ask the xT administrator to import you into the org chart.",
                )
            )
        autonomy: int = 0
        roles: tuple[str, ...] = ()
        if actor.is_agent:
            grant = self.store.agent_grant(look, person.id, actor.client_id)
            if grant is not None:
                autonomy, roles = grant
        model_actor = ModelActor(
            tenant,
            person.id,
            tuple(person.roles),
            department_ids=(person.unit,) if person.unit else (),
            client_azp=actor.client_id,
            trace_id=trace_id[:128],
        )
        return Caller(
            actor.tenant,
            profile,
            person,
            actor.client_id,
            actor.is_agent,
            autonomy,
            tuple(roles),
            model_actor,
        )

    def caller(self, ctx: CallContext) -> Caller:
        cur = _CURRENT.get()
        if cur is not None and cur[0] == ctx.actor:
            return cur[1]
        c = self.resolve(ctx.actor, ctx.trace_id)
        _CURRENT.set((ctx.actor, c))
        return c

    def run_as(self, caller: Caller, actor: Actor, fn: Callable[[], T]) -> T:
        """Chạy `fn` trong MỘT transaction với ngữ cảnh RLS của `caller`."""
        token = _CURRENT.set((actor, caller))
        later: list[Callable[[], None]] = []
        token2 = _AFTER_ROLLBACK.set(later)

        def body() -> T:
            out = fn()
            for job in later:
                job()
            return out

        try:
            with self.uow.scope(caller.actor):
                return self.uow.run(body)
        finally:
            _AFTER_ROLLBACK.reset(token2)
            _CURRENT.reset(token)


class ScopedDispatcher(ToolDispatcher):
    """`ToolDispatcher` bọc mỗi lời gọi trong một transaction có ngữ cảnh tenant của token."""

    def __init__(self, runtime: Runtime, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.runtime = runtime

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        ctx: CallContext,
        *,
        duplicate_keys: Sequence[str] = (),
    ) -> ToolOutcome:
        def body() -> ToolOutcome:
            return ToolDispatcher.call(self, name, arguments, ctx, duplicate_keys=duplicate_keys)

        return self.scoped(name, ctx, body)

    def scoped(self, name: str, ctx: CallContext, body: Callable[[], ToolOutcome]) -> ToolOutcome:
        """Chạy `body` (tool của catalog hoặc tool app-only) trong MỘT transaction tenant."""
        try:
            caller = self.runtime.resolve(ctx.actor, ctx.trace_id)
        except CallerError as e:
            return self.reject(name, ctx.trace_id, e.error)
        except Exception:
            log.exception("caller resolution failed trace_id=%s", ctx.trace_id)
            return self.reject(
                name,
                ctx.trace_id,
                ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure."),
            )

        try:
            return self.runtime.run_as(caller, ctx.actor, body)
        except Exception:
            log.exception("transaction failed trace_id=%s tool=%s", ctx.trace_id, name)
            return self.reject(
                name,
                ctx.trace_id,
                ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure."),
            )
