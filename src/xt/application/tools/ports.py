# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cổng ứng dụng mà handler tool gọi. Adapter (DB, model engine, process, IAM, PII) hiện thực.

Quy ước chung:
- `args` là input đã qua `input_schema` (đóng, đúng kiểu); port tự kiểm phần phụ thuộc model
  (values theo `values_schema`, field lạ -> `invalid_request` reason `unknown_field`...).
- Lỗi nghiệp vụ ném `xt.application.tools.errors.ToolError`; không trả dict lỗi.
- Ghi theo 2 pha: `plan_*` (validate, CAS, rule, tính `effective_level`; KHÔNG ghi) rồi
  `commit(plan)` (mutation + audit + outbox, chạy trong `TransactionRunner.run`).
- Đọc trả `data` đúng `output_schema` của tool (object mở); cursor là opaque, port tự ký MAC.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeVar

from xt.application.tools.context import Actor, CallContext, ClientClass, PersonRef
from xt.domain.autonomy import RiskLevel

T = TypeVar("T")

Data = dict[str, Any]
Args = Mapping[str, Any]


# ---- IAM ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AccessCheck:
    tool: str
    action: str
    resource: str | None
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str = "allow"
    message: str = ""
    explain_ref: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


class AccessGuard(Protocol):
    """IAM + SoD + ranh giới tenant/đại diện. Chạy TRƯỚC mọi xác nhận (§3.5 bước 2)."""

    def check(self, ctx: CallContext, check: AccessCheck) -> AccessDecision: ...

    def autonomy_ceiling(self, ctx: CallContext) -> RiskLevel:
        """Trần tự chủ người chủ cấp cho agent (mặc định L1; chưa cấp -> L0)."""
        ...


# ---- ghi 2 pha -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MutationPlan:
    """Kết quả `plan_*`: đã validate + CAS + rule, chưa ghi.

    `digest_source` là nội dung plan do server tính (action, tài nguyên, revision, thay đổi);
    digest của nó gắn với PendingOperation và được kiểm lại khi người xác nhận.
    `confirmers`: người phải xác nhận theo thứ tự (decide: người được giao bước; L3 hai người:
    thêm người thứ hai). Rỗng = người được đại diện.
    """

    level: RiskLevel
    summary: str
    digest_source: Mapping[str, Any]
    target_ref: str | None = None
    confirmers: tuple[PersonRef, ...] = ()
    payload: Any = None


@dataclass(frozen=True, slots=True)
class CommitResult:
    data: Data
    operation_ref: str
    commit_state: Literal["committed", "queued"] = "committed"


class TransactionRunner(Protocol):
    """Một transaction cho claim + mutation + audit + outbox + receipt (§3.5 bước 3-8).
    Exception trong `fn` -> rollback toàn bộ (kể cả claim idempotency) rồi ném lại."""

    def run(self, fn: Callable[[], T]) -> T: ...


# ---- dịch vụ miền --------------------------------------------------------------------------


class WorkspaceService(Protocol):
    def inspect(self, ctx: CallContext, args: Args) -> Data: ...


class ModelCatalog(Protocol):
    def describe_model(self, ctx: CallContext, args: Args) -> Data | None:
        """`{"model": ...}` hoặc None (không có / không được biết)."""
        ...

    def model_primitive(self, ctx: CallContext, model_id: str) -> str | None:
        """Primitive của model active (`record`, `request`, `task`...), None nếu không thấy."""
        ...


class Directory(Protocol):
    def find_people(self, ctx: CallContext, args: Args) -> Data: ...

    def person_for_subject(self, ctx: CallContext, subject: str) -> PersonRef | None: ...


class RecordService(Protocol):
    def search(self, ctx: CallContext, args: Args) -> Data: ...

    def aggregate(self, ctx: CallContext, args: Args) -> Data: ...

    def get(self, ctx: CallContext, args: Args) -> Data | None: ...

    def plan_create(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def plan_update(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def plan_transition(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def plan_create_task(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult: ...


class ProcessService(Protocol):
    def describe_process(self, ctx: CallContext, args: Args) -> Data | None: ...

    def plan_submit(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def plan_decide(self, ctx: CallContext, args: Args) -> MutationPlan:
        """`confirmers[0]` bắt buộc là người được giao bước (human_only)."""
        ...

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult: ...


class InboxService(Protocol):
    def get_inbox(self, ctx: CallContext, args: Args) -> Data: ...


class HistoryService(Protocol):
    def get_history(self, ctx: CallContext, args: Args) -> Data | None: ...


class FileService(Protocol):
    def plan_upload(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult: ...


class ModelChangeService(Protocol):
    def preview(self, ctx: CallContext, args: Args) -> Data:
        """dry_run=true: diff, phân loại rủi ro, số bản ghi bị ảnh hưởng; không lưu gì."""
        ...

    def plan_propose(self, ctx: CallContext, args: Args) -> MutationPlan: ...

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult: ...


# ---- làn PII -------------------------------------------------------------------------------

PiiMode = Literal["full", "masked", "omitted"]


@dataclass(frozen=True, slots=True)
class PiiProjection:
    data: Data
    mode: PiiMode
    masked_fields: tuple[str, ...] = ()
    omitted_fields: tuple[str, ...] = ()

    def meta(self) -> dict[str, Any]:
        m: dict[str, Any] = {"mode": self.mode}
        if self.masked_fields:
            m["masked_fields"] = list(self.masked_fields)
        if self.omitted_fields:
            m["omitted_fields"] = list(self.omitted_fields)
        return m


class PiiLane(Protocol):
    """§3.8: chiếu output theo (policy `llm_egress` của tenant, lớp client), ghi `egress` vào
    audit; chiều vào: mở token `[PII:…]` trong `where` (so bằng), chặn token ghi ngược."""

    def inbound(self, ctx: CallContext, tool: str, args: Args) -> Args: ...

    def outbound(self, ctx: CallContext, tool: str, data: Data) -> PiiProjection: ...

    def scrub_error(self, ctx: CallContext, tool: str, error: Data) -> Data:
        """Quét sâu object `error` (message, details, plan_summary, continuation) theo lớp client
        trước khi trả agent: engine thật echo giá trị trong lỗi (spike-pii-lane §2)."""
        ...


# ---- idempotency + xác nhận ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OperationKey:
    """Namespace của idempotency_key: tenant + effective actor + tool (§3.4)."""

    tenant: str
    actor: str
    tool: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class Receipt:
    key: OperationKey
    fingerprint: str
    operation_ref: str
    commit_state: Literal["committed", "queued"]
    effective_level: RiskLevel
    data: Data
    committed_at: datetime


class PendingStatus(StrEnum):
    PENDING = "pending"
    AWAITING_SECOND_APPROVER = "awaiting_second_approver"
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"

    @property
    def open(self) -> bool:
        return self in (PendingStatus.PENDING, PendingStatus.AWAITING_SECOND_APPROVER)


@dataclass(frozen=True, slots=True)
class PendingOperation:
    """Server giữ; agent không cầm bằng chứng nào (C7). `arguments` là args chuẩn hóa đầy đủ."""

    confirmation_id: str
    key: OperationKey
    tool: str
    arguments: Mapping[str, Any]
    fingerprint: str
    actor: Actor
    client_class: ClientClass
    plan_digest: str
    plan_summary: str
    level: RiskLevel
    confirmers: tuple[PersonRef, ...]
    created_at: datetime
    expires_at: datetime
    status: PendingStatus = PendingStatus.PENDING
    approvals: tuple[str, ...] = ()

    @property
    def next_confirmer(self) -> PersonRef | None:
        i = len(self.approvals)
        return self.confirmers[i] if i < len(self.confirmers) else None


class ClaimBusy(StrEnum):
    IN_PROGRESS = "in_progress"


IN_PROGRESS = ClaimBusy.IN_PROGRESS
"""`claim` chờ quá hạn vì một transaction khác đang giữ cùng key."""


class OperationStore(Protocol):
    """Receipt + PendingOperation theo `OperationKey`. Prod: bảng UNIQUE(key), cùng UoW với
    mutation. Mọi hàm ghi chạy bên trong `TransactionRunner.run`; rollback nhả claim."""

    def claim(
        self, key: OperationKey, fingerprint: str, now: datetime
    ) -> Receipt | PendingOperation | ClaimBusy | None:
        """Insert-or-get, TRƯỚC khi plan/commit. None = đã chèn hàng `in_progress`, người gọi
        giữ key tới hết transaction. Hàng của transaction khác chưa xong -> chờ (unique index chặn
        INSERT) rồi trả kết quả; quá `lock_timeout` -> `IN_PROGRESS`. Không bao giờ để hai
        transaction cùng thực thi một key.

        Hàng đã có phải được đọc KÈM KHÓA (`SELECT … FOR UPDATE`): nếu `ConfirmationService`
        đang giữ `lock_pending` trên cùng key thì `claim` chờ nó commit rồi thấy receipt
        (audit R2-M3; `INSERT … ON CONFLICT DO NOTHING` một mình không chờ row lock)."""
        ...

    def save_receipt(self, receipt: Receipt) -> None:
        """Hoàn tất hàng đã claim (hoặc PendingOperation đã khóa) trong cùng transaction."""
        ...

    def save_pending(self, op: PendingOperation) -> None:
        """Chuyển hàng đã claim thành PendingOperation, cùng transaction."""
        ...

    def update_pending(self, op: PendingOperation) -> bool:
        """Ghi trạng thái mới CHỈ KHI hàng hiện tại còn mở (`pending`/`awaiting_second_approver`)
        và key chưa có receipt; trả False nếu không ghi (Postgres: `UPDATE … WHERE status IN
        (…) AND receipt IS NULL`, kiểm `rowcount`). Chống ghi đè CONFIRMED (audit R2-M3)."""
        ...

    def get_pending(self, confirmation_id: str) -> PendingOperation | None:
        """Đọc không khóa (hiển thị trên Desk)."""
        ...

    def lock_pending(self, confirmation_id: str) -> PendingOperation | None:
        """`SELECT … FOR UPDATE` trong transaction của ConfirmationService (bấm hai lần)."""
        ...

    def count_open_pending(self, tenant: str, actor: str, now: datetime) -> int: ...


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str: ...
