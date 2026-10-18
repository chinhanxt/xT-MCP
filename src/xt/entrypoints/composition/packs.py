# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nạp và cài gói nghiệp vụ `packages/<gói>/` (định dạng `xt-pack/1`) vào một tenant.

Gói là DỮ LIỆU: model (catalog), quy trình (ProcessSpec v2), policy IAM, ràng buộc primitive
(`request` gắn quy trình, `task`). Cài = hai bước có xác nhận tường minh của người (architecture-v2
§6.1: "gói cài vào tenant qua chính luồng đề xuất + duyệt"):

1. `plan_install`: diễn tập DDL thật (PgMigrator, rollback), compile quy trình, gộp policy, kiểm
   op của deployment; trả kế hoạch tiếng Việt + `digest` của toàn bộ nội dung sẽ áp.
2. `install(confirm=digest)`: tính lại kế hoạch, phải trùng digest người đã duyệt, rồi áp DDL
   (pha 1 + pha 2) và ghi policy/định nghĩa/ràng buộc trong một transaction. Người đề xuất và
   người duyệt phải khác nhau (SoD). Gói chỉ được THÊM: kế hoạch có thao tác phá hủy/bị chặn thì từ
   chối (đổi phá hủy đi qua `propose_model_change`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from xt.adapters.db.connection import AuditKey
from xt.adapters.db.integration import (
    AdminError,
    active_definitions,
    connect,
    current_policy,
    installed_packs,
    save_pack,
)
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.contracts.tools import BY_NAME
from xt.domain.iam import PolicyDocument, loads_policy_json, policy_digest
from xt.domain.model.context import TenantRef
from xt.domain.model.ident import SYSTEM_COLUMNS
from xt.domain.model.plan import RiskClass, build_plan
from xt.domain.model.spec import Catalog, FieldSpec, load_spec_json
from xt.domain.process.errors import SpecError
from xt.domain.process.spec import parse_spec
from xt.entrypoints.composition.process import check_pack_ops


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InputSource(_Strict):
    const: str | int | bool | None = None
    field_: str | None = Field(None, alias="field")
    arg: Literal["note"] | None = None


class BindingSpec(_Strict):
    primitive: Literal["request", "task"]
    process: str | None = None
    state_field: str = "state"
    inputs: dict[str, InputSource] = {}
    fields: dict[str, str] = {}
    priorities: dict[Literal["low", "normal", "high", "urgent"], str] = {}


PACK_NAME = r"^[a-z0-9][a-z0-9-]{0,63}$"
AppGroup = Literal["hang-hoa", "thoi-gian-du-an", "trai-nghiem-thue-bao", "tai-san-khong-gian"]


class AppUi(_Strict):
    """`generic`: giao diện chung sinh từ describe_model (không path). `module`: giao diện riêng
    phục vụ cùng origin ở `path` (tuyệt đối, kết thúc `/`, không `..`, không scheme)."""

    kind: Literal["generic", "module"]
    path: str | None = Field(None, pattern=r"^/(?:[a-z0-9_-]+/)+$", max_length=128)

    @model_validator(mode="after")
    def _path_matches_kind(self) -> AppUi:
        if (self.kind == "module") != (self.path is not None):
            raise ValueError("ui module cần path; ui generic không có path")
        return self


class AppManifest(_Strict):
    """Ứng dụng Tầng 3 (D-UNI-T3-1): một gói nghiệp vụ + nhóm, giao diện và tool được phép."""

    id: str = Field(pattern=PACK_NAME)
    title: str = Field(min_length=1, max_length=120)
    group: AppGroup
    depends_on: list[str] = []
    ui: list[AppUi] = Field(min_length=1, max_length=2)
    tools: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> AppManifest:
        if len({u.kind for u in self.ui}) != len(self.ui):
            raise ValueError("ui: mỗi loại (generic/module) tối đa một lần")
        unknown = [t for t in self.tools if t not in BY_NAME]
        if unknown:
            raise ValueError(f"tools: không có trong catalog {unknown}")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("tools: trùng tên")
        return self


class PackManifest(_Strict):
    format: Literal["xt-pack/1"]
    pack: str = Field(pattern=PACK_NAME)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    title: str
    depends_on: list[str] = []
    models: list[str]
    processes: list[str] = []
    policies: list[str] = []
    allowed_ops: dict[str, list[str]] = {}
    bindings: dict[str, BindingSpec] = {}
    notes: dict[str, Any] = {}
    app: AppManifest | None = None

    @model_validator(mode="after")
    def _app_matches_pack(self) -> PackManifest:
        if self.app is None:
            return self
        if self.app.id != self.pack:
            raise ValueError(f"app.id phải trùng tên gói {self.pack!r}")
        extra = [d for d in self.app.depends_on if d not in self.depends_on]
        if extra:
            raise ValueError(f"app.depends_on {extra} không nằm trong depends_on của gói")
        return self

    def install_view(self) -> dict[str, Any]:
        """Bản dump đi vào digest cài; không có `app` thì giữ nguyên dạng cũ (digest không đổi)."""
        out = self.model_dump(mode="json", by_alias=True)
        if self.app is None:
            out.pop("app")
        return out


def _priority_binding_problems(
    model: str, binding: BindingSpec, fields: Mapping[str, FieldSpec]
) -> list[str]:
    if not binding.priorities:
        return []
    problems = []
    if len(set(binding.priorities.values())) != len(binding.priorities):
        problems.append(f"ràng buộc {model}: priorities phải ánh xạ một-một")
    priority = fields.get(binding.fields.get("priority", ""))
    if priority is None or priority.type != "enum":
        problems.append(f"ràng buộc {model}: priorities cần trường priority kiểu enum")
    elif any(
        v not in {o.value for o in priority.options or []} for v in binding.priorities.values()
    ):
        problems.append(f"ràng buộc {model}: priorities chứa giá trị không có trong enum")
    return problems


@dataclass(frozen=True)
class LoadedPack:
    root: Path
    manifest: PackManifest
    models: list[dict[str, Any]]
    processes: list[dict[str, Any]]
    policies: list[dict[str, Any]]


def _json(path: Path) -> Any:
    try:
        return load_spec_json(path.read_bytes())
    except OSError as e:
        raise AdminError(f"{path}: không đọc được ({e.strerror})") from e
    except ValueError as e:
        raise AdminError(f"{path}: {e}") from e


def load_pack(root: Path) -> LoadedPack:
    root = root.resolve()
    raw = json.loads((root / "pack.json").read_text(encoding="utf-8"))
    try:
        manifest = PackManifest.model_validate(raw)
    except ValueError as e:
        raise AdminError(f"{root / 'pack.json'}: manifest không hợp lệ: {e}") from e
    models = [_json(root / "models" / f"{n}.json") for n in manifest.models]
    processes = [_json(root / "processes" / f"{n}.json") for n in manifest.processes]
    policies = [
        loads_policy_json((root / "policies" / f"{n}.json").read_bytes()) for n in manifest.policies
    ]
    return LoadedPack(root, manifest, models, processes, policies)


def _retenant(doc: Mapping[str, Any], alias: str) -> dict[str, Any]:
    """Policy của gói viết cho tenant mẫu: đổi `xrn:<mẫu>:` thành tenant đích (theo cấu trúc)."""
    src = str(doc["tenant"])
    prefix = f"xrn:{src}:"

    def fix(p: str) -> str:
        return f"xrn:{alias}:" + p[len(prefix) :] if p.startswith(prefix) else p

    statements = []
    for s in doc["statements"]:
        s = dict(s)
        s["resources"] = [fix(r) for r in s["resources"]]
        statements.append(s)
    return {"version": doc["version"], "tenant": alias, "statements": statements}


@dataclass
class InstallPlan:
    pack: LoadedPack
    tenant: TenantRef
    alias: str
    catalog: Catalog
    model_plan_hash: str
    model_plan_text: str
    destructive: list[str]
    blocked: list[str]
    processes: list[tuple[str, int, dict[str, Any], str, dict[str, list[str]]]]
    policy: tuple[dict[str, Any], str] | None
    bindings: list[tuple[str, str, dict[str, Any]]]
    problems: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        body = {
            "pack": self.pack.manifest.install_view(),
            "tenant": str(self.tenant.id),
            "models": self.model_plan_hash,
            "processes": [(p[0], p[1], p[3]) for p in self.processes],
            "policy": self.policy[1] if self.policy else None,
            "bindings": self.bindings,
        }
        canon = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()

    def render_vi(self) -> str:
        m = self.pack.manifest
        lines = [
            f"Gói {m.pack} {m.version} — {m.title}",
            f"Tenant: {self.alias} (seq {self.tenant.seq})",
            "",
            "Model:",
            self.model_plan_text,
            "",
            "Quy trình: "
            + (", ".join(f"{p[0]} v{p[1]} ({p[3][:12]})" for p in self.processes) or "(không)"),
            "Policy IAM: "
            + (
                f"{len(self.policy[0]['statements'])} statement ({self.policy[1][:12]})"
                if self.policy
                else "(giữ nguyên)"
            ),
            "Ràng buộc: " + (", ".join(f"{b[0]}={b[1]}" for b in self.bindings) or "(không)"),
        ]
        if self.problems:
            lines += ["", "KHÔNG CÀI ĐƯỢC:", *(f"  - {p}" for p in self.problems)]
        else:
            lines += ["", f"Mã xác nhận (digest): {self.digest}"]
        return "\n".join(lines)


def plan_install(
    pack: LoadedPack,
    *,
    migrator_dsn: str,
    tenant: TenantRef,
    alias: str,
    installed: set[str],
    audit_key: AuditKey | None = None,
) -> InstallPlan:
    m = pack.manifest
    problems: list[str] = []
    missing = [d for d in m.depends_on if d not in installed]
    if missing:
        problems.append(f"thiếu gói phụ thuộc: {missing}")
    problems += check_pack_ops(m.allowed_ops)
    migrator = PgMigrator(lambda: connect(migrator_dsn), audit_key=audit_key)
    current = migrator.current(tenant)
    names = {x["name"] for x in pack.models}
    keep = [x.model_dump(mode="json", by_alias=True) for x in (current.models if current else [])]
    merged = [x for x in keep if x["name"] not in names] + list(pack.models)
    try:
        catalog = Catalog.model_validate(
            {"version": (current.version if current else 0) + 1, "models": merged}
        )
    except ValueError as e:
        raise AdminError(f"model của gói không hợp lệ: {e}") from e
    plan = migrator.plan(tenant, catalog, rehearse=True)
    destructive = plan.ids(RiskClass.DESTRUCTIVE)
    blocked = plan.ids(RiskClass.BLOCKED)
    if destructive:
        problems.append(f"kế hoạch model có thao tác PHÁ HỦY {destructive}: gói chỉ được thêm")
    if blocked:
        problems.append(f"kế hoạch model có thao tác BỊ CHẶN {blocked}")
    processes, bad = _compile(pack)
    problems += bad
    with connect(migrator_dsn) as conn:
        active = active_definitions(conn, tenant)
        cur_policy = current_policy(conn, tenant)
    for pid, ver, _, _sha, _ in processes:
        if pid in active and active[pid][0] > ver:
            problems.append(f"quy trình {pid}: tenant đang chạy v{active[pid][0]} mới hơn v{ver}")
    policy: tuple[dict[str, Any], str] | None = None
    if pack.policies:
        merged_doc = _merge_policy(pack.policies, alias, cur_policy[1] if cur_policy else None)
        dumped = merged_doc.model_dump(mode="json")
        digest = policy_digest(merged_doc)
        if (
            cur_policy is None
            or policy_digest(PolicyDocument.model_validate(cur_policy[1])) != digest
        ):
            policy = (dumped, digest)
    by_name = {x.name: x for x in catalog.models}
    problems += _complete_via_problems(processes, by_name)
    bad, bindings = binding_problems(m, by_name, {p[0] for p in processes} | set(active))
    problems += bad
    return InstallPlan(
        pack,
        tenant,
        alias,
        catalog,
        plan.hash,
        plan.render_vi(),
        destructive,
        blocked,
        processes,
        policy,
        bindings,
        problems,
    )


Processes = list[tuple[str, int, dict[str, Any], str, dict[str, list[str]]]]


def _compile(pack: LoadedPack) -> tuple[Processes, list[str]]:
    engine = SpiffProcessEngine()
    processes: Processes = []
    problems: list[str] = []
    allowed = pack.manifest.allowed_ops
    for spec in pack.processes:
        try:
            parsed = parse_spec(spec)
            compiled = engine.compile(spec, allowed)
        except SpecError as e:
            problems.append(f"quy trình {spec.get('id')}: {e.message}")
            continue
        processes.append((parsed.id, parsed.version, spec, compiled.sha256, dict(allowed)))
    return processes, problems


def _merge_policy(
    docs: list[dict[str, Any]], alias: str, current: Mapping[str, Any] | None
) -> PolicyDocument:
    statements: dict[str, Any] = {}
    if current:
        statements = {s["id"]: s for s in current["statements"]}
    for raw_doc in docs:
        for s in _retenant(raw_doc, alias)["statements"]:
            statements[s["id"]] = s
    try:
        return PolicyDocument.model_validate(
            {"version": "1", "tenant": alias, "statements": list(statements.values())}
        )
    except ValueError as e:
        raise AdminError(f"policy gộp không hợp lệ: {e}") from e


def binding_problems(
    m: PackManifest, by_name: Mapping[str, Any], proc_ids: set[str]
) -> tuple[list[str], list[tuple[str, str, dict[str, Any]]]]:
    problems: list[str] = []
    bindings: list[tuple[str, str, dict[str, Any]]] = []
    for model, b in m.bindings.items():
        spec_m = by_name.get(model)
        if spec_m is None:
            problems.append(f"ràng buộc {model}: model không tồn tại")
            continue
        fields = spec_m.by_name()
        if b.primitive == "request":
            if not b.process or b.process not in proc_ids:
                problems.append(f"ràng buộc {model}: quy trình {b.process!r} không có")
            sf = fields.get(b.state_field)
            if sf is None or sf.managed_by != "workflow" or sf.type != "enum":
                problems.append(
                    f"ràng buộc {model}: trường trạng thái {b.state_field!r} phải là enum"
                    " managed_by=workflow"
                )
            for name, src in b.inputs.items():
                # System columns (`id`, `owner_id`...) are on every row submit_request reads.
                known = src.field_ in fields or src.field_ in SYSTEM_COLUMNS
                if src.field_ is not None and not known:
                    problems.append(f"ràng buộc {model}: input {name} lấy trường lạ {src.field_!r}")
        else:
            for arg, f in b.fields.items():
                if f not in fields:
                    problems.append(f"ràng buộc {model}: {arg} -> trường lạ {f!r}")
            problems += _priority_binding_problems(model, b, fields)
        bindings.append(
            (model, b.primitive, b.model_dump(mode="json", by_alias=True, exclude_none=True))
        )
    return problems, bindings


def static_problems(pack: LoadedPack, alias: str, deps: list[LoadedPack]) -> list[str]:
    """Every check of `plan_install` that needs no database, for a fresh tenant with `deps`."""
    problems = check_pack_ops(pack.manifest.allowed_ops)
    models = [x for d in deps for x in d.models] + list(pack.models)
    try:
        catalog = Catalog.model_validate({"version": 1, "models": models})
    except ValueError as e:
        return [*problems, f"model của gói không hợp lệ: {e}"]
    plan = build_plan(None, catalog)
    for cls in (RiskClass.DESTRUCTIVE, RiskClass.BLOCKED):
        if plan.ids(cls):
            problems.append(f"kế hoạch model có thao tác {cls.value}: {plan.ids(cls)}")
    processes, bad = _compile(pack)
    problems += bad
    _merge_policy([p for d in deps for p in d.policies] + list(pack.policies), alias, None)
    by_name = {x.name: x for x in catalog.models}
    problems += _complete_via_problems(processes, by_name)
    dep_ids = {str(spec["id"]) for d in deps for spec in d.processes}
    problems += binding_problems(pack.manifest, by_name, {p[0] for p in processes} | dep_ids)[0]
    return problems


def _complete_via_problems(
    processes: list[tuple[str, int, dict[str, Any], str, dict[str, list[str]]]],
    models: Mapping[str, Any],
) -> list[str]:
    """`complete_via` phải trỏ model có thật, trường match/state có thật, `writable_fields` chỉ
    gồm trường thường (không ref/user/file, không managed_by) — PSA-01."""
    out: list[str] = []
    for pid, _ver, spec, _sha, _ops in processes:
        for st, _ in parse_spec(spec).iter_steps():
            via = getattr(st, "complete_via", None)
            if via is None:
                continue
            m = models.get(via.model)
            where = f"quy trình {pid} bước {st.id}: complete_via"
            if m is None:
                out.append(f"{where}: model {via.model!r} không có")
                continue
            by = m.by_name()
            for f in [*via.match, via.state_field]:
                if f != "id" and f not in by:
                    out.append(f"{where}: trường {f!r} không có trong {via.model}")
            for f in via.writable_fields:
                fs = by.get(f)
                if fs is None or fs.type in ("ref", "user", "file") or fs.managed_by:
                    out.append(
                        f"{where}: writable_fields {f!r} không ghi được (không có / ref /"
                        " user / file / managed_by)"
                    )
    return out


def install(
    pack: LoadedPack,
    *,
    migrator_dsn: str,
    tenant: TenantRef,
    alias: str,
    installed: set[str],
    confirm: str,
    proposed_by: str,
    approved_by: str,
    audit_key: AuditKey | None = None,
) -> InstallPlan:
    if not approved_by or approved_by == proposed_by:
        raise AdminError("người duyệt phải khác người đề xuất (tách nhiệm vụ)")
    plan = plan_install(
        pack,
        migrator_dsn=migrator_dsn,
        tenant=tenant,
        alias=alias,
        installed=installed,
        audit_key=audit_key,
    )
    if plan.problems:
        raise AdminError("; ".join(plan.problems))
    if plan.digest != confirm:
        raise AdminError(
            f"mã xác nhận không khớp kế hoạch hiện tại ({plan.digest}); xem lại kế hoạch rồi duyệt"
        )
    with connect(migrator_dsn) as conn, conn.transaction():
        # Lifecycle changes and every pack install share this tenant lock. Keep it
        # through DDL and save_pack so a dependency cannot be disabled after this read.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"xt:pack:{tenant.id}",)
        )
        enabled = installed_packs(conn, tenant)
        missing = [dep for dep in pack.manifest.depends_on if dep not in enabled]
        if missing:
            raise AdminError(f"thiếu gói phụ thuộc: {missing}")
        migrator = PgMigrator(lambda: connect(migrator_dsn), audit_key=audit_key)
        if plan.model_plan_text and plan.catalog.models:
            current = migrator.current(tenant)
            if (
                current is None
                or current.digest()
                != plan.catalog.model_copy(update={"version": current.version}).digest()
            ):
                migrator.apply(
                    tenant,
                    plan.catalog,
                    plan.model_plan_hash,
                    set(),
                    proposed_by=proposed_by,
                    approved_by=approved_by,
                )
                stats = migrator.run_jobs(tenant.seq)
                if stats.failed or stats.dead:
                    raise AdminError(f"pha 2 của DDL lỗi: {stats}")
        save_pack(
            conn,
            tenant,
            pack=pack.manifest.pack,
            version=pack.manifest.version,
            digest=plan.digest,
            proposed_by=proposed_by,
            approved_by=approved_by,
            policy=plan.policy,
            processes=plan.processes,
            bindings=plan.bindings,
            models=[str(x["name"]) for x in pack.models],
            depends_on=pack.manifest.depends_on,
            app=pack.manifest.app.model_dump(mode="json") if pack.manifest.app else None,
        )
    return plan
