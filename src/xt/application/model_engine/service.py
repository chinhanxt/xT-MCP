# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Điều phối model engine: describe/propose/approve-apply/rollout theo tenant và CRUD tổng quát.

Thứ tự kiểm trong một mutation CRUD (tool-contracts-v1 §3.5): model → quyền → trường workflow
(G-M1) → validate payload (Poka-yoke, lỗi tiếng Việt) → CAS `expected_revision` → rule 3 trị →
ghi (RLS + CHECK/FK ở DB là chốt chặn cuối) → dịch lỗi DB qua tên constraint ổn định.
"""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError

from xt.application.model_engine.operations import apply_operations
from xt.application.model_engine.ports import (
    AggregateQuery,
    ApplyOutcome,
    Busy,
    CatalogReader,
    ConstraintViolated,
    Migrator,
    PermissionDenied,
    ProposalStore,
    RecordRepository,
    SchemaMissing,
    StoreError,
)
from xt.application.warehouse.validation import validate_write as validate_warehouse_write
from xt.contracts.model import ProposeModelChange, describe_model, propose_result
from xt.domain.model.checks import constraint_map, fk_sources
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.expr import Expr, check_expr_types
from xt.domain.model.plan import PlanRejected, RiskClass
from xt.domain.model.spec import Catalog, ModelSpec
from xt.domain.model.values import (
    Mode,
    build_values_model,
    reject_managed,
    rule_violations,
    translate_validation,
    with_system_fields,
)

MAX_LIMIT, MAX_OFFSET = 200, 10_000
MAX_GROUPS = 1000
GROUP_TYPES = ("text", "enum", "ref", "user", "bool", "date")
BUCKET_UNITS = ("day", "week", "month", "quarter", "year")
Action = Literal["create", "read", "update", "delete"]


def jsonable(v: Any) -> Any:
    if isinstance(v, UUID | Decimal):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [jsonable(x) for x in v]
    return v


def _fingerprint(obj: Mapping[str, Any]) -> str:
    canon = json.dumps(
        jsonable(dict(obj)), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()


def _store_error(m: ModelSpec, cat: Catalog, e: StoreError, op: str) -> ModelError:
    if isinstance(e, ConstraintViolated):
        if e.kind == "fk" and op == "delete":
            src = fk_sources(cat).get(e.constraint or "")
            who = f"'{src[1].label}' của '{src[0].label}'" if src else "bản ghi khác"
            return ModelError(
                "precondition_failed",
                f"Không xóa được bản ghi '{m.label}': đang được {who} tham chiếu",
                remediation="Gỡ tham chiếu ở các bản ghi kia trước.",
                details={"reason": "dependency_open"},
            )
        info = constraint_map(cat).get(e.constraint or "")
        if info is not None:
            mm = cat.model(info.model)
            by = mm.by_name()
            items = [
                {
                    "field": x,
                    "label": by[x].label if x in by else x,
                    "rule": e.constraint,
                    "message": info.message,
                }
                for x in info.fields
            ]
            if info.kind == "fk":
                return ModelError(
                    "invalid_request",
                    info.message,
                    details={"reason": "bad_reference", "fields": items},
                )
            return ModelError("rule_violation", info.message, details={"violations": items})
        if e.kind == "not_null":
            by = m.by_name()
            label = by[e.column].label if e.column in by else e.column
            return ModelError(
                "invalid_request",
                f"'{label}' là trường bắt buộc",
                details={"reason": "validation", "fields": [{"field": e.column, "label": label}]},
            )
        return ModelError("rule_violation", f"Dữ liệu vi phạm ràng buộc {e.constraint}")
    if isinstance(e, PermissionDenied):
        return ModelError(
            "access_denied",
            f"Bạn không có quyền {op} bản ghi này trên '{m.label}' (ngoài phạm vi được cấp)",
            remediation="Kiểm tra người sở hữu/phòng ban của bản ghi hoặc nhờ người có quyền.",
        )
    if isinstance(e, Busy):
        return ModelError("timeout", "Hệ thống đang bận (khóa/timeout), thử lại sau ít giây")
    if isinstance(e, SchemaMissing):
        return ModelError(
            "internal_error", "Cấu trúc dữ liệu chưa khớp phiên bản model, báo quản trị"
        )
    return ModelError("internal_error", "Lỗi lưu trữ dữ liệu")


class _CatalogMixin:
    catalogs: CatalogReader

    def _catalog(self, actor: ModelActor) -> tuple[int, Catalog]:
        cur = self.catalogs.current(actor)
        if cur is None:
            raise ModelError(
                "not_found", "Đơn vị của bạn chưa có model nào", details={"reason": "no_catalog"}
            )
        return cur

    @staticmethod
    def _model(cat: Catalog, name: str) -> ModelSpec:
        try:
            return cat.model(name)
        except KeyError:
            names = [m.name for m in cat.models]
            sug = difflib.get_close_matches(name, names, n=1)
            raise ModelError(
                "not_found",
                f"Không có model '{name}'" + (f" — có phải '{sug[0]}'?" if sug else ""),
                details={"reason": "unknown_model", "models": sorted(names)},
            ) from None

    @staticmethod
    def _require(actor: ModelActor, m: ModelSpec, action: Action) -> None:
        if any(r in m.permissions and action in m.permissions[r].actions for r in actor.roles):
            return
        allowed = sorted(r for r, p in m.permissions.items() if action in p.actions)
        raise ModelError(
            "access_denied",
            f"Vai trò {list(actor.roles)} không có quyền '{action}' trên '{m.label}'",
            remediation="Nhờ người có vai trò được phép thực hiện.",
            details={"allowed_roles": allowed, "action": action},
        )


# ------------------------------------------------------------------------- CRUD ----
class RecordService(_CatalogMixin):
    def __init__(self, catalogs: CatalogReader, repo: RecordRepository) -> None:
        self.catalogs, self.repo = catalogs, repo
        self._models: dict[tuple[int, int, str, Mode], type[BaseModel]] = {}

    def _values(
        self, actor: ModelActor, ver: int, m: ModelSpec, mode: Mode, data: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(data, Mapping):
            raise ModelError(
                "invalid_request",
                "values phải là object phẳng {trường: giá trị}",
                details={"reason": "validation"},
            )
        key = (actor.tenant.seq, ver, m.name, mode)
        vm = self._models.get(key)
        if vm is None:
            vm = self._models[key] = build_values_model(m, mode)
        try:
            obj = vm.model_validate(dict(data))
        except ValidationError as e:
            raise translate_validation(m, e, mode) from e
        return obj.model_dump(exclude_unset=True)

    @staticmethod
    def _defaults(m: ModelSpec) -> dict[str, Any]:
        return {f.name: f.coerced("default") for f in m.fields}

    def create(self, actor: ModelActor, model: str, values: Mapping[str, Any]) -> dict[str, Any]:
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "create")
        reject_managed(m, dict(values))
        vals = self._values(actor, ver, m, "create", values)
        row = {**self._defaults(m), **vals}
        validate_warehouse_write(self, actor, model, row)
        bad = rule_violations(m, row)
        if bad:
            raise bad
        try:
            rec = self.repo.insert(actor, m, vals)
        except StoreError as e:
            raise _store_error(m, cat, e, "create") from e
        return {"record": jsonable(rec), "model_revision": ver}

    def get(self, actor: ModelActor, model: str, record_id: UUID) -> dict[str, Any]:
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "read")
        try:
            rec = self.repo.get(actor, m, record_id)
        except StoreError as e:
            raise _store_error(m, cat, e, "read") from e
        if rec is None:
            raise ModelError(
                "not_found",
                f"Không tìm thấy bản ghi trong '{m.label}' (hoặc ngoài phạm vi bạn được xem)",
            )
        return {"record": jsonable(rec), "model_revision": ver}

    def _write(
        self,
        actor: ModelActor,
        model: str,
        record_id: UUID,
        expected_revision: int,
        patch: Mapping[str, Any],
        *,
        workflow: bool,
    ) -> dict[str, Any]:
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "update")
        if not workflow:
            reject_managed(m, dict(patch), f"{m.name}:{record_id}")
        vals = self._values(actor, ver, m, "workflow" if workflow else "update", patch)
        if not vals:
            raise ModelError(
                "invalid_request",
                "Không có trường nào để cập nhật",
                details={"reason": "empty_patch"},
            )
        try:
            cur = self.repo.get(actor, m, record_id)
        except StoreError as e:
            raise _store_error(m, cat, e, "update") from e
        if cur is None:
            raise ModelError(
                "not_found",
                f"Không tìm thấy bản ghi trong '{m.label}' (hoặc ngoài phạm vi bạn được xem)",
            )
        if int(cur["revision"]) != expected_revision:
            raise self._conflict(int(cur["revision"]), expected_revision)
        validate_warehouse_write(
            self, actor, model, {**cur, **vals}, previous=cur, workflow=workflow
        )
        bad = rule_violations(m, {**cur, **vals})
        if bad:
            raise bad
        try:
            out = self.repo.update(actor, m, record_id, expected_revision, vals, workflow=workflow)
        except StoreError as e:
            raise _store_error(m, cat, e, "update") from e
        if out.row is None:
            if out.current_revision is None:
                raise ModelError("not_found", f"Không tìm thấy bản ghi trong '{m.label}'")
            if out.current_revision != expected_revision:
                raise self._conflict(out.current_revision, expected_revision)
            raise ModelError(
                "access_denied",
                f"Bạn xem được nhưng không có quyền sửa bản ghi này trên '{m.label}'",
            )
        return {"record": jsonable(out.row), "model_revision": ver}

    @staticmethod
    def _conflict(current: int, sent: int) -> ModelError:
        return ModelError(
            "revision_conflict",
            f"Bản ghi đã bị sửa bởi người khác (revision hiện tại {current}, bạn gửi {sent})",
            details={"current_revision": current},
        )

    def update(
        self,
        actor: ModelActor,
        model: str,
        record_id: UUID,
        expected_revision: int,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._write(actor, model, record_id, expected_revision, patch, workflow=False)

    def workflow_write(
        self,
        actor: ModelActor,
        model: str,
        record_id: UUID,
        expected_revision: int,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Đường DUY NHẤT ghi trường managed_by=workflow (transition/decide gọi, không qua CRUD)."""
        return self._write(actor, model, record_id, expected_revision, patch, workflow=True)

    def delete(
        self, actor: ModelActor, model: str, record_id: UUID, expected_revision: int
    ) -> dict[str, Any]:
        _, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "delete")
        try:
            out = self.repo.delete(actor, m, record_id, expected_revision)
        except StoreError as e:
            raise _store_error(m, cat, e, "delete") from e
        if out.row is None:
            if out.current_revision is None:
                raise ModelError("not_found", f"Không tìm thấy bản ghi trong '{m.label}'")
            if out.current_revision != expected_revision:
                raise self._conflict(out.current_revision, expected_revision)
            raise ModelError(
                "access_denied", f"Bạn không có quyền xóa bản ghi này trên '{m.label}'"
            )
        return {"deleted": str(record_id)}

    def search(
        self,
        actor: ModelActor,
        model: str,
        where: Mapping[str, Any] | None = None,
        sort: Sequence[str] = ("-created_at",),
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "read")
        if not 1 <= limit <= MAX_LIMIT or not 0 <= offset <= MAX_OFFSET:
            raise ModelError(
                "invalid_request",
                f"limit 1..{MAX_LIMIT}, offset 0..{MAX_OFFSET}",
                details={"reason": "validation"},
            )
        expr = self._filter(m, where)
        by = with_system_fields(m)
        order: list[tuple[str, bool]] = []
        for s in sort[:3]:
            col = s.removeprefix("-")
            if col not in by or by[col].type == "json":
                raise ModelError(
                    "invalid_request",
                    f"Không sắp xếp được theo '{col}'",
                    details={"reason": "validation", "valid_fields": sorted(by)},
                )
            order.append((col, s.startswith("-")))
        try:
            page = self.repo.search(actor, m, expr, order, limit, offset, self._scope(actor, m))
        except StoreError as e:
            raise _store_error(m, cat, e, "read") from e
        return {
            "total": page.total,
            "items": jsonable(page.rows),
            "has_more": offset + len(page.rows) < page.total,
            "model_revision": ver,
        }

    @staticmethod
    def _filter(m: ModelSpec, where: Mapping[str, Any] | None) -> Expr | None:
        if not where:
            return None
        from pydantic import TypeAdapter

        try:
            expr: Expr = TypeAdapter(Expr).validate_python(dict(where))
            check_expr_types(expr, with_system_fields(m), for_filter=True)
        except (ValidationError, ValueError) as e:
            msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
            raise ModelError(
                "invalid_request",
                f"Bộ lọc không hợp lệ: {msg}",
                details={"reason": "validation", "valid_fields": sorted(with_system_fields(m))},
            ) from e
        return expr

    @staticmethod
    def _scope(actor: ModelActor, m: ModelSpec) -> Literal["all", "scoped"]:
        scopes = {
            m.permissions[r].scope
            for r in actor.roles
            if r in m.permissions and "read" in m.permissions[r].actions
        }
        return "all" if "all" in scopes else "scoped"

    def aggregate(
        self,
        actor: ModelActor,
        model: str,
        where: Mapping[str, Any] | None,
        query: AggregateQuery,
    ) -> dict[str, Any]:
        """count/sum per group under the same read permission, filter and RLS as search."""
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        self._require(actor, m, "read")
        expr = self._filter(m, where)
        by = with_system_fields(m)

        def field(name: str, types: tuple[str, ...], role: str) -> None:
            f = by.get(name)
            if f is None or f.type not in types or f.pii != "none":
                raise ModelError(
                    "invalid_request",
                    f"Không dùng được '{name}' cho {role}: cần trường {'/'.join(types)}"
                    " không chứa dữ liệu cá nhân",
                    details={"reason": "validation", "field": name},
                )

        if len(set(query.group_by)) != len(query.group_by):
            raise ModelError(
                "invalid_request", "group_by bị trùng", details={"reason": "validation"}
            )
        for name in query.group_by:
            field(name, GROUP_TYPES, "group_by")
        for name in query.sums:
            field(name, ("number", "money"), "sum")
        if query.bucket is not None:
            unit, name, _ = query.bucket
            if unit not in BUCKET_UNITS:
                raise ModelError(
                    "invalid_request", f"time_bucket không hợp lệ: {unit}",
                    details={"reason": "validation"},
                )  # fmt: skip
            field(name, ("date", "datetime"), "time_bucket")
        try:
            page = self.repo.aggregate(actor, m, expr, query, MAX_GROUPS, self._scope(actor, m))
        except StoreError as e:
            raise _store_error(m, cat, e, "read") from e
        if page.truncated:
            raise ModelError(
                "resource_constraint",
                f"Quá {MAX_GROUPS} nhóm; thu hẹp bộ lọc hoặc bớt group_by",
                details={"reason": "too_many_groups", "max_groups": MAX_GROUPS},
            )
        return {"model_revision": ver, "as_of": page.as_of, "rows": page.rows}


# ------------------------------------------------------------------ model engine ----
@dataclass
class TenantRollout:
    tenant_seq: int
    status: Literal["applied", "unchanged", "blocked", "needs_approval", "failed"]
    from_version: int
    to_version: int | None = None
    plan_hash: str | None = None
    reason: str | None = None
    post_pending: bool = False


@dataclass
class RolloutReport:
    results: list[TenantRollout] = field(default_factory=list)
    distribution: dict[int, list[int]] = field(default_factory=dict)

    def by_status(self, status: str) -> list[int]:
        return [r.tenant_seq for r in self.results if r.status == status]


class ModelService(_CatalogMixin):
    def __init__(
        self, catalogs: CatalogReader, migrator: Migrator, proposals: ProposalStore
    ) -> None:
        self.catalogs, self.migrator, self.proposals = catalogs, migrator, proposals

    def describe_model(
        self, actor: ModelActor, model: str, *, include_values_schema: bool = False
    ) -> dict[str, Any]:
        ver, cat = self._catalog(actor)
        m = self._model(cat, model)
        if not any(r in m.permissions for r in actor.roles):
            raise ModelError(
                "access_denied",
                f"Vai trò {list(actor.roles)} không có quyền nào trên '{m.label}'",
                details={"allowed_roles": sorted(m.permissions)},
            )
        return describe_model(m, ver, include_values_schema=include_values_schema)

    def propose(
        self, actor: ModelActor, req: ProposeModelChange, *, proposed_by: str
    ) -> dict[str, Any]:
        cur = self.catalogs.current(actor)
        version, cat = cur if cur else (0, None)
        exists = cat is not None and any(m.name == req.model_id for m in cat.models)
        base = version if exists else 0
        if req.base_revision is not None and req.base_revision != base:
            raise ModelError(
                "revision_conflict",
                f"Model '{req.model_id}' đã đổi kể từ lần xem trước (revision hiện tại {base},"
                " bạn gửi"
                f" {req.base_revision})",
                details={"current_revision": base},
            )
        fp = _fingerprint(req.model_dump(mode="json", exclude={"dry_run", "idempotency_key"}))
        if not req.dry_run:
            prev = self.proposals.find(actor, proposed_by, req.idempotency_key)
            if prev is not None:
                if prev.fingerprint != fp:
                    raise ModelError(
                        "invalid_request",
                        "idempotency_key đã dùng cho một đề xuất khác",
                        remediation="Tạo key mới cho đề xuất mới.",
                        details={"reason": "idempotency_conflict"},
                    )
                try:
                    plan = self.migrator.plan(actor.tenant, prev.spec, rehearse=False)
                except PlanRejected as e:
                    raise ModelError(
                        "revision_conflict", e.message, details={"reason": e.reason, **e.details}
                    ) from e
                out = propose_result(
                    plan, base_revision=base, dry_run=False, proposal=f"model_proposal:{prev.id}"
                )
                out["replayed"] = True
                return out
        new = apply_operations(cat, req.model_id, req.operations)
        plan = self.migrator.plan(actor.tenant, new, rehearse=True)
        extra: list[dict[str, Any]] = []
        state = self.migrator.post_state(actor.tenant)
        if state is not None and state[1] == "failed":
            extra.append(
                {
                    "severity": "blocker",
                    "message": f"Phiên bản v{state[0]} còn ràng buộc/index chưa xác thực (CSDL lệch"
                    " catalog): quản trị phải sửa dữ liệu và chạy lại hậu kỳ trước",
                    "affected_count": None,
                }
            )
        nm = new.model(req.model_id)
        if not nm.permissions:
            extra.append(
                {
                    "severity": "warning",
                    "message": f"Model '{nm.label}' chưa cấp quyền cho vai trò nào: sau khi áp"
                    " dụng chưa ai đọc/ghi được qua CRUD",
                    "affected_count": None,
                }
            )
        if req.dry_run:
            return propose_result(plan, base_revision=base, dry_run=True, extra_findings=extra)
        if plan.ids(RiskClass.BLOCKED):
            raise ModelError(
                "rule_violation",
                "Đề xuất còn thao tác BỊ CHẶN, chưa gửi duyệt được: "
                + "; ".join(c.message for c in plan.changes if c.cls is RiskClass.BLOCKED),
                details={"blocked": plan.ids(RiskClass.BLOCKED)},
            )
        stored = self.proposals.save(
            actor,
            base_version=version,
            spec=new,
            plan=plan,
            rationale=req.rationale,
            proposed_by=proposed_by,
            idempotency_key=req.idempotency_key,
            fingerprint=fp,
        )
        return propose_result(
            plan,
            base_revision=base,
            dry_run=False,
            proposal=f"model_proposal:{stored.id}",
            extra_findings=extra,
        )

    def approve_and_apply(
        self,
        tenant: TenantRef,
        proposal_id: UUID,
        *,
        approved_hash: str,
        confirm: Iterable[str],
        approved_by: str,
    ) -> ApplyOutcome:
        p = self.proposals.get(tenant, proposal_id)
        if p is None:
            raise ModelError("not_found", "Không tìm thấy đề xuất thay đổi model")
        if approved_by == p.proposed_by:
            raise ModelError(
                "access_denied",
                "Người đề xuất không được tự duyệt thay đổi model (tách nhiệm vụ)",
                details={"reason": "sod"},
            )
        return self._apply(tenant, p.spec, approved_hash, set(confirm), p.proposed_by, approved_by)

    def apply_catalog(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome:
        """Áp một catalog đã được người duyệt (mã kế hoạch + xác nhận từng mã PHÁ HỦY)."""
        if approved_by == proposed_by:
            raise ModelError(
                "access_denied",
                "Người đề xuất không được tự duyệt thay đổi model (tách nhiệm vụ)",
                details={"reason": "sod"},
            )
        return self._apply(tenant, new, approved_hash, confirm, proposed_by, approved_by)

    def _apply(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome:
        try:
            return self.migrator.apply(
                tenant,
                new,
                approved_hash,
                confirm,
                proposed_by=proposed_by,
                approved_by=approved_by,
            )
        except PlanRejected as e:
            code = {"plan_changed": "revision_conflict", "blocked": "rule_violation"}.get(
                e.reason, "precondition_failed"
            )
            raise ModelError(code, e.message, details={"reason": e.reason, **e.details}) from e  # type: ignore[arg-type]
        except Busy as e:
            raise ModelError(
                "timeout",
                f"Không lấy được khóa bảng trong thời hạn (có giao dịch khác đang giữ): {e}",
                details={"commit_state": "rolled_back"},
            ) from e
        except StoreError as e:
            raise ModelError("internal_error", f"Áp dụng thất bại, đã rollback: {e}") from e

    def rollout(
        self,
        tenants: Sequence[TenantRef],
        transform: Callable[[Catalog | None], Catalog],
        *,
        allow: frozenset[RiskClass] = frozenset({RiskClass.SAFE}),
        confirm: frozenset[str] = frozenset(),
        proposed_by: str,
        approved_by: str,
        run_post: bool = True,
    ) -> RolloutReport:
        """Áp một thay đổi lên từng tenant, mỗi tenant một transaction: lỗi của tenant này không
        chặn tenant khác; tenant lỗi giữ version cũ (lệch phiên bản là hợp lệ).

        Mặc định chỉ tự áp thay đổi SAFE; RISKY phải được người duyệt cho phép tường minh qua
        `allow`. Thao tác PHÁ HỦY không bao giờ tự xác nhận: phải có đúng mã trong `confirm`."""
        if approved_by == proposed_by:
            raise ModelError(
                "access_denied",
                "Người đề xuất không được tự duyệt rollout",
                details={"reason": "sod"},
            )
        report = RolloutReport()
        for t in tenants:
            base = 0
            try:
                cur = self.migrator.current(t)
                base = cur.version if cur else 0
                new = transform(cur)
                plan = self.migrator.plan(t, new, rehearse=True)
                if not plan.changes:
                    report.results.append(TenantRollout(t.seq, "unchanged", base))
                    continue
                blocked = [c for c in plan.changes if c.cls is RiskClass.BLOCKED]
                if blocked:
                    report.results.append(
                        TenantRollout(
                            t.seq,
                            "blocked",
                            base,
                            plan_hash=plan.hash,
                            reason="; ".join(c.message for c in blocked),
                        )
                    )
                    continue
                over = sorted({c.cls.value for c in plan.changes if c.cls not in allow})
                if over:
                    report.results.append(
                        TenantRollout(
                            t.seq,
                            "needs_approval",
                            base,
                            plan_hash=plan.hash,
                            reason=f"cần duyệt riêng: {over}",
                        )
                    )
                    continue
                out = self._apply(
                    t,
                    new,
                    plan.hash,
                    set(confirm),
                    proposed_by,
                    approved_by,
                )
                pending = False
                if run_post:
                    stats = self.migrator.run_jobs(t.seq)
                    pending = bool(stats.pending_versions)
                report.results.append(
                    TenantRollout(
                        t.seq, "applied", base, out.version, out.plan_hash, post_pending=pending
                    )
                )
            except (ModelError, StoreError, ValueError) as e:
                if isinstance(e, ModelError):
                    reason = e.message
                elif isinstance(e, Busy):
                    reason = (
                        f"Hết thời gian chờ khóa bảng (giao dịch khác đang giữ), thử lại sau: {e}"
                    )
                else:
                    reason = str(e)
                report.results.append(TenantRollout(t.seq, "failed", base, reason=reason))
        report.distribution = self.migrator.version_distribution()
        return report
