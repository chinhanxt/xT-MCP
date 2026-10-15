# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng mà model engine cần; `xt.adapters.db` hiện thực trên Postgres."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.expr import Expr
from xt.domain.model.plan import Plan
from xt.domain.model.spec import Catalog, ModelSpec


# --------------------------------------------------------------------- lỗi lưu trữ ----
class StoreError(Exception):
    """Lỗi do adapter phát hiện, application dịch sang ModelError tiếng Việt."""


class ConstraintViolated(StoreError):
    def __init__(
        self,
        kind: Literal["unique", "check", "fk", "not_null"],
        constraint: str | None,
        column: str | None,
    ) -> None:
        super().__init__(f"{kind}:{constraint or column}")
        self.kind, self.constraint, self.column = kind, constraint, column


class PermissionDenied(StoreError):
    """RLS WITH CHECK hoặc quyền cột từ chối (ngoài phạm vi / trường workflow)."""


class Busy(StoreError):
    """lock_timeout / statement_timeout."""


class SchemaMissing(StoreError):
    """Bảng/cột chưa tồn tại (catalog lệch DB)."""


# ---------------------------------------------------------------------- bản ghi ----
@dataclass(frozen=True)
class UpdateOutcome:
    row: dict[str, Any] | None
    current_revision: int | None  # None: không thấy bản ghi (không tồn tại/ngoài phạm vi)


@dataclass(frozen=True)
class Page:
    total: int
    rows: list[dict[str, Any]]


class RecordRepository(Protocol):
    def insert(
        self, actor: ModelActor, m: ModelSpec, values: Mapping[str, Any]
    ) -> dict[str, Any]: ...

    def update(
        self,
        actor: ModelActor,
        m: ModelSpec,
        record_id: UUID,
        expected_revision: int,
        values: Mapping[str, Any],
        *,
        workflow: bool = False,
    ) -> UpdateOutcome: ...

    def delete(
        self, actor: ModelActor, m: ModelSpec, record_id: UUID, expected_revision: int
    ) -> UpdateOutcome: ...

    def get(self, actor: ModelActor, m: ModelSpec, record_id: UUID) -> dict[str, Any] | None: ...

    def search(
        self,
        actor: ModelActor,
        m: ModelSpec,
        where: Expr | None,
        order: Sequence[tuple[str, bool]],
        limit: int,
        offset: int,
        scope_hint: Literal["all", "scoped"],
    ) -> Page: ...


class CatalogReader(Protocol):
    def current(self, actor: ModelActor) -> tuple[int, Catalog] | None: ...


# ---------------------------------------------------------------------- migration ----
@dataclass
class ApplyOutcome:
    tenant_seq: int
    version: int
    plan_hash: str
    tx_steps: int
    queued_jobs: int


@dataclass
class JobStats:
    done: int = 0
    failed: int = 0
    dead: int = 0
    pending_versions: list[tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class StoredProposal:
    id: UUID
    tenant_seq: int
    base_version: int
    spec: Catalog
    plan_hash: str
    proposed_by: str
    fingerprint: str
    created_at: str


class Migrator(Protocol):
    """Áp catalog vào schema của MỘT tenant (mỗi tenant một transaction pha 1)."""

    def current(self, tenant: TenantRef) -> Catalog | None: ...

    def plan(self, tenant: TenantRef, new: Catalog, *, rehearse: bool) -> Plan: ...

    def apply(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        *,
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome: ...

    def run_jobs(self, tenant_seq: int | None = None) -> JobStats: ...

    def post_state(self, tenant: TenantRef) -> tuple[int, str] | None: ...

    def version_distribution(self) -> dict[int, list[int]]: ...


class ProposalStore(Protocol):
    def save(
        self,
        actor: ModelActor,
        *,
        base_version: int,
        spec: Catalog,
        plan: Plan,
        rationale: str,
        proposed_by: str,
        idempotency_key: str,
        fingerprint: str,
    ) -> StoredProposal: ...

    def find(
        self, actor: ModelActor, proposed_by: str, idempotency_key: str
    ) -> StoredProposal | None: ...

    def get(self, tenant: TenantRef, proposal_id: UUID) -> StoredProposal | None: ...
