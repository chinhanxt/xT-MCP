# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Change planner thuần: diff hai catalog theo ID ỔN ĐỊNH → danh sách thay đổi đã phân loại.

- Cùng id khác tên → RENAME thật (không drop + add); id biến mất → DROP; id mới → ADD.
- Phân loại SAFE / RISKY / DESTRUCTIVE / BLOCKED. Phần phụ thuộc dữ liệu (số dòng, số vi phạm,
  giá trị trùng) được mô tả bằng `Probe`; adapter DB chạy probe trên dữ liệu HIỆN TẠI rồi
  `finalize` nâng hạng (vi phạm > 0 → BLOCKED). Module này không chạm DB, không sinh SQL.
- Hạ mức DLCN (G-M2) và bỏ `managed_by` (G-M1) là RISKY.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from enum import StrEnum
from typing import Any, Literal

from xt.domain.model.checks import CheckDef, checks_by_name
from xt.domain.model.spec import PII_RANK, SCOPE_RANK, Catalog, FieldSpec, ModelSpec


class RiskClass(StrEnum):
    SAFE = "SAFE"
    RISKY = "RISKY"
    DESTRUCTIVE = "DESTRUCTIVE"
    BLOCKED = "BLOCKED"


CLASS_VI = {
    RiskClass.SAFE: "AN TOÀN",
    RiskClass.RISKY: "RỦI RO",
    RiskClass.DESTRUCTIVE: "PHÁ HỦY",
    RiskClass.BLOCKED: "BỊ CHẶN",
}
_RANK = {RiskClass.SAFE: 0, RiskClass.RISKY: 1, RiskClass.DESTRUCTIVE: 2, RiskClass.BLOCKED: 3}

ChangeKind = Literal[
    "add_model", "drop_model", "rename_model", "meta_model",
    "add_field", "drop_field", "rename_field", "retype_field", "label_field", "default_field",
    "nullable_field", "require_field", "unique_field", "ununique_field",
    "drop_check", "check", "index", "pii", "grants", "permission",
]  # fmt: skip


# --------------------------------------------------------------------------- probe ----
@dataclass(frozen=True)
class RowCount:
    model_id: str


@dataclass(frozen=True)
class NullCount:
    model_id: str
    field_id: str


@dataclass(frozen=True)
class CheckViolations:
    """Đếm dòng hiện có làm điều kiện MỚI ra FALSE (trường mới chưa có cột → default/NULL)."""

    model_id: str
    check_name: str


@dataclass(frozen=True)
class Duplicates:
    model_id: str
    field_ids: tuple[str, ...]


Probe = RowCount | NullCount | CheckViolations | Duplicates


@dataclass(frozen=True)
class ProbeResult:
    count: int
    sample_ids: tuple[str, ...] = ()


# -------------------------------------------------------------------------- change ----
@dataclass
class Change:
    id: str
    kind: ChangeKind
    cls: RiskClass
    model_id: str
    model: str
    message: str
    field_id: str | None = None
    field: str | None = None
    probe: Probe | None = None
    # Hạng khi probe > 0 (None: probe chỉ để báo số dòng ảnh hưởng)
    on_violation: RiskClass | None = None
    violation_note: str = ""
    violations: int | None = None
    affected: int | None = None
    sample_ids: list[str] = dc_field(default_factory=list)
    rehearsal_error: str | None = None
    details: dict[str, Any] = dc_field(default_factory=dict)

    @property
    def requires_confirmation(self) -> bool:
        return self.cls is RiskClass.DESTRUCTIVE

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "class": self.cls.value,
            "kind": self.kind,
            "model": self.model,
            "field": self.field,
            "message_vi": self.message,
            "violations": self.violations,
            "affected": self.affected,
            "sample_ids": list(self.sample_ids),
            "requires_confirmation": self.requires_confirmation,
            "rehearsal_error": self.rehearsal_error,
            "details": self.details,
        }


@dataclass
class Plan:
    base_version: int
    target_version: int
    changes: list[Change]
    spec_digest: str

    @property
    def hash(self) -> str:
        """Mã kế hoạch chống TOCTOU: đổi spec, đổi phân loại hay đổi base → mã khác."""
        canon = json.dumps(
            {
                "base": self.base_version,
                "target": self.target_version,
                "spec": self.spec_digest,
                "changes": [[c.id, c.cls.value] for c in self.changes],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()

    def summary(self) -> dict[str, int]:
        out = {k.value: 0 for k in RiskClass}
        for c in self.changes:
            out[c.cls.value] += 1
        return out

    @property
    def overall(self) -> RiskClass:
        return max((c.cls for c in self.changes), key=_RANK.__getitem__, default=RiskClass.SAFE)

    def ids(self, cls: RiskClass) -> list[str]:
        return [c.id for c in self.changes if c.cls is cls]

    def to_json(self) -> dict[str, Any]:
        return {
            "base_version": self.base_version,
            "target_version": self.target_version,
            "plan_hash": self.hash,
            "summary": self.summary(),
            "destructive_op_ids": self.ids(RiskClass.DESTRUCTIVE),
            "changes": [c.to_json() for c in self.changes],
        }

    def render_vi(self) -> str:
        s = self.summary()
        lines = [
            f"KẾ HOẠCH THAY ĐỔI MODEL v{self.base_version} → v{self.target_version}"
            f" (mã kế hoạch {self.hash[7:23]})",
            "Tổng: " + ", ".join(f"{s[k.value]} {CLASS_VI[k]}" for k in RiskClass),
        ]
        for c in self.changes:
            tag = CLASS_VI[c.cls]
            if c.requires_confirmation:
                tag += f" - cần xác nhận lần 2 [{c.id}]"
            line = f"  [{tag}] {c.message}"
            if c.violations:
                line += f" — {c.violations} bản ghi vi phạm"
                if c.sample_ids:
                    line += f" (vd: {', '.join(c.sample_ids)})"
            if c.rehearsal_error:
                line += f" — DIỄN TẬP LỖI: {c.rehearsal_error}"
            lines.append(line)
        if s["BLOCKED"]:
            lines.append("→ Không thể áp dụng: xử lý các mục BỊ CHẶN trước.")
        elif s["DESTRUCTIVE"]:
            lines.append("→ Có thao tác PHÁ HỦY: người duyệt phải xác nhận lại đúng từng mã.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------- diff ----
def _looser(c_old: CheckDef, c_new: CheckDef) -> bool:
    if c_old.kind != c_new.kind or c_old.key[1] != c_new.key[1]:
        return False
    a, b = c_old.value, c_new.value
    if c_new.kind == "min":
        return bool(b <= a)  # type: ignore[operator]
    if c_new.kind == "max":
        return bool(b >= a)  # type: ignore[operator]
    if c_new.kind == "enum":
        return bool(set(a) <= set(b))  # type: ignore[call-overload]
    return False


def _perm_grants(m: ModelSpec) -> dict[tuple[str, str], int]:
    return {(r, a): SCOPE_RANK[p.scope] for r, p in m.permissions.items() for a in p.actions}


def _add_field_change(n: ModelSpec, f: FieldSpec) -> Change:
    cid = f"add_field:{n.id}:{f.id}"
    if not f.required:
        return Change(
            cid, "add_field", RiskClass.SAFE, n.id, n.name,
            f"Thêm trường '{f.label}' ({f.name}, {f.type}) vào '{n.label}' — cho phép trống",
            f.id, f.name,
        )  # fmt: skip
    if f.default is not None:
        return Change(
            cid, "add_field", RiskClass.RISKY, n.id, n.name,
            f"Thêm trường BẮT BUỘC '{f.label}' ({f.name}) với mặc định {f.default!r}:"
            " bản ghi hiện có nhận giá trị này (PG11+: không rewrite bảng)",
            f.id, f.name, probe=RowCount(n.id),
        )  # fmt: skip
    return Change(
        cid, "add_field", RiskClass.RISKY, n.id, n.name,
        f"Thêm trường BẮT BUỘC '{f.label}' ({f.name}) không có default",
        f.id, f.name, probe=RowCount(n.id), on_violation=RiskClass.BLOCKED,
        violation_note=(
            " — bảng đã có dữ liệu: khai báo 'default' (backfill) hoặc để không bắt buộc"
        ),
    )  # fmt: skip


def diff_model(o: ModelSpec, n: ModelSpec) -> list[Change]:
    out: list[Change] = []
    mid = n.id

    def ch(kind: ChangeKind, cls: RiskClass, msg: str, *sub: str, **kw: Any) -> Change:
        c = Change(":".join([kind, mid, *sub]), kind, cls, mid, n.name, msg, **kw)
        out.append(c)
        return c

    if o.name != n.name:
        ch(
            "rename_model", RiskClass.DESTRUCTIVE,
            f"Đổi tên model '{o.name}' → '{n.name}' (giữ dữ liệu; client/agent dùng tên cũ sẽ lỗi)",
            details={"from": o.name, "to": n.name},
        )  # fmt: skip
    if (o.label, o.description, o.title_field) != (n.label, n.description, n.title_field):
        ch("meta_model", RiskClass.SAFE, f"Đổi nhãn/mô tả của model thành '{n.label}'")
    of, nf = o.by_id(), n.by_id()
    for fid in sorted(of.keys() - nf.keys()):
        f = of[fid]
        ch(
            "drop_field", RiskClass.DESTRUCTIVE,
            f"Xóa trường '{f.label}' ({f.name}) khỏi '{n.label}' — mất dữ liệu cột này",
            fid, field_id=fid, field=f.name, probe=RowCount(mid),
        )  # fmt: skip
    kept_old_names = {of[i].name for i in of.keys() & nf.keys()}
    for fid in sorted(of.keys() & nf.keys()):
        a, b = of[fid], nf[fid]
        if a.name == b.name:
            continue
        if b.name in kept_old_names:
            ch(
                "rename_field", RiskClass.BLOCKED,
                f"Đổi tên '{a.name}' → '{b.name}' trùng tên một trường đang tồn tại (hoán đổi tên):"
                " tách thành 2 lần thay đổi",
                fid, field_id=fid, field=b.name,
            )  # fmt: skip
            continue
        ch(
            "rename_field", RiskClass.DESTRUCTIVE,
            f"Đổi tên trường '{a.name}' → '{b.name}' trong '{n.label}' (RENAME COLUMN, giữ dữ"
            " liệu)",
            fid, field_id=fid, field=b.name, details={"from": a.name, "to": b.name},
        )  # fmt: skip
    for f in n.fields:
        if f.id not in of:
            c = _add_field_change(n, f)
            out.append(c)
    for fid in sorted(of.keys() & nf.keys()):
        a, b = of[fid], nf[fid]
        kw: dict[str, Any] = {"field_id": fid, "field": b.name}
        if a.type != b.type or a.ref != b.ref:
            ref = f" (ref {a.ref}→{b.ref})" if a.ref != b.ref else ""
            ch(
                "retype_field", RiskClass.BLOCKED,
                f"Đổi kiểu '{b.label}' {a.type}→{b.type}{ref}: chưa hỗ trợ — tạo trường mới, chuyển"
                " dữ liệu, rồi xóa trường cũ",
                fid, **kw,
            )  # fmt: skip
            continue
        if (a.label, a.description) != (b.label, b.description):
            ch(
                "label_field",
                RiskClass.SAFE,
                f"Đổi nhãn trường {b.name} thành '{b.label}'",
                fid,
                **kw,
            )
        if a.coerced("default") != b.coerced("default"):
            ch(
                "default_field", RiskClass.SAFE,
                f"Đổi giá trị mặc định '{b.label}' → {b.default!r}", fid, **kw,
            )  # fmt: skip
        if a.required and not b.required:
            ch("nullable_field", RiskClass.SAFE, f"Bỏ bắt buộc '{b.label}'", fid, **kw)
        if b.required and not a.required:
            if b.default is not None:
                ch(
                    "require_field", RiskClass.RISKY,
                    f"Đặt '{b.label}' thành bắt buộc (bản ghi đang trống được điền {b.default!r})",
                    fid, probe=NullCount(mid, fid), **kw,
                )  # fmt: skip
            else:
                ch(
                    "require_field", RiskClass.RISKY, f"Đặt '{b.label}' thành bắt buộc",
                    fid, probe=NullCount(mid, fid), on_violation=RiskClass.BLOCKED,
                    violation_note=" — có bản ghi đang trống: điền dữ liệu hoặc khai báo default",
                    **kw,
                )  # fmt: skip
        if b.unique and not a.unique:
            ch(
                "unique_field", RiskClass.RISKY,
                f"Đặt '{b.label}' là duy nhất (CREATE UNIQUE INDEX CONCURRENTLY)",
                fid, probe=Duplicates(mid, (fid,)), on_violation=RiskClass.BLOCKED,
                violation_note=" — có giá trị trùng, xử lý trước", **kw,
            )  # fmt: skip
        if a.unique and not b.unique:
            ch("ununique_field", RiskClass.SAFE, f"Bỏ ràng buộc duy nhất '{b.label}'", fid, **kw)
    # CHECK theo tên ổn định
    oc, nc = checks_by_name(o), checks_by_name(n)
    retyped = {
        i for i in of.keys() & nf.keys() if (of[i].type, of[i].ref) != (nf[i].type, nf[i].ref)
    }
    for name in sorted(oc.keys() - nc.keys()):
        c_old = oc[name]
        if c_old.field_id is not None and (c_old.field_id not in nf or c_old.field_id in retyped):
            continue  # DROP COLUMN tự xóa ràng buộc của cột; đổi kiểu đã BLOCKED
        out.append(
            Change(
                f"drop_check:{mid}:{name}", "drop_check", RiskClass.SAFE, mid, n.name,
                f"Bỏ ràng buộc: {c_old.message}", details={"check": name},
            )
        )  # fmt: skip
    for name in sorted(nc):
        c_new = nc[name]
        if c_new.field_id is not None and (c_new.field_id not in of or c_new.field_id in retyped):
            continue  # thuộc trường mới (xử lý ở add_field) hoặc trường đổi kiểu
        c_old_opt = oc.get(name)
        if c_old_opt is not None and c_old_opt.key == c_new.key:
            if c_old_opt.message != c_new.message:
                out.append(
                    Change(
                        f"check:{mid}:{name}", "check", RiskClass.SAFE, mid, n.name,
                        f"Đổi thông điệp ràng buộc: {c_new.message}",
                        details={"check": name, "message_only": True},
                    )
                )  # fmt: skip
            continue
        fname = nf[c_new.field_id].name if c_new.field_id else None
        if c_old_opt is not None and _looser(c_old_opt, c_new):
            out.append(
                Change(
                    f"check:{mid}:{name}", "check", RiskClass.SAFE, mid, n.name,
                    f"Nới ràng buộc: {c_new.message}", c_new.field_id, fname,
                    details={"check": name, "replace": True},
                )
            )  # fmt: skip
            continue
        verb = "Siết ràng buộc" if c_old_opt is not None else "Thêm ràng buộc"
        out.append(
            Change(
                f"check:{mid}:{name}", "check", RiskClass.RISKY, mid, n.name,
                f"{verb}: {c_new.message} (đã kiểm dữ liệu hiện có)", c_new.field_id, fname,
                probe=CheckViolations(mid, name), on_violation=RiskClass.BLOCKED,
                violation_note=" — dữ liệu hiện có vi phạm, sửa trước khi áp dụng",
                details={"check": name, "replace": c_old_opt is not None},
            )
        )  # fmt: skip
    # index (so theo id trường)
    oi, ni = {x.id: x for x in o.indexes}, {x.id: x for x in n.indexes}
    o_name_to_id, n_name_to_id = {f.name: f.id for f in o.fields}, {f.name: f.id for f in n.fields}
    for iid in sorted(oi.keys() | ni.keys()):
        ia, ib = oi.get(iid), ni.get(iid)
        if (
            ia is not None
            and ib is not None
            and ia.unique == ib.unique
            and [o_name_to_id[x] for x in ia.fields] == [n_name_to_id[x] for x in ib.fields]
        ):
            continue
        verb = "Tạo" if ia is None else "Xóa" if ib is None else "Đổi"
        c = Change(
            f"index:{mid}:{iid}", "index", RiskClass.SAFE, mid, n.name,
            f"{verb} index {iid} trên '{n.label}' (CONCURRENTLY, không khóa ghi)",
            details={"index": iid, "drop": ia is not None, "create": ib is not None},
        )  # fmt: skip
        if ib is not None and ib.unique:
            fids = tuple(n_name_to_id[x] for x in ib.fields)
            c.cls = RiskClass.RISKY
            if all(x in of for x in fids):
                c.probe, c.on_violation = Duplicates(mid, fids), RiskClass.BLOCKED
                c.violation_note = " — có tổ hợp giá trị trùng, xử lý trước"
        out.append(c)
    # G-M2: nhãn DLCN
    common = sorted(of.keys() & nf.keys())
    changed = [i for i in common if of[i].pii != nf[i].pii]
    o_sens = {i for i in of if of[i].pii == "sensitive"}
    n_sens = {i for i in nf if nf[i].pii == "sensitive"}
    # so cả TÊN cột sensitive: rename trường sensitive phải sinh lại trigger audit (AUD-01)
    names_changed = {of[i].name for i in o_sens} != {nf[i].name for i in n_sens}
    if changed or o_sens != n_sens or names_changed:
        lowered = sorted(nf[i].name for i in changed if PII_RANK[nf[i].pii] < PII_RANK[of[i].pii])
        desc = [f"{nf[i].name}: {of[i].pii}→{nf[i].pii}" for i in changed]
        msg = f"HẠ mức DLCN: {lowered} (audit/egress xử lý theo mức thấp hơn); " if lowered else ""
        msg += (
            f"đổi nhãn DLCN: {desc}; cột audit chỉ lưu HMAC: {sorted(nf[i].name for i in n_sens)}"
        )
        out.append(
            Change(
                f"pii:{mid}", "pii", RiskClass.RISKY if lowered else RiskClass.SAFE,
                mid, n.name, msg,
                details={"lowered": lowered, "changed": desc},
            )
        )  # fmt: skip
    # G-M1: quyền cột
    freed = sorted(nf[i].name for i in common if of[i].managed_by and not nf[i].managed_by)
    managed_changed = any(of[i].managed_by != nf[i].managed_by for i in common)
    if freed or managed_changed or (nf.keys() - of.keys()):
        managed = sorted(f.name for f in n.fields if f.managed_by)
        out.append(
            Change(
                f"grants:{mid}", "grants", RiskClass.RISKY if freed else RiskClass.SAFE,
                mid, n.name,
                (f"Bỏ quản lý workflow cho {freed}: CRUD tổng quát sẽ ghi được" if freed else
                 f"Cấp lại quyền cột: trường do workflow quản = {managed}"),
                details={"freed": freed, "managed": managed},
            )
        )  # fmt: skip
    if o.permissions != n.permissions:
        og, ng = _perm_grants(o), _perm_grants(n)
        wider = sorted(
            f"{r}:{a}"
            + ("" if (r, a) not in og else f" {o.permissions[r].scope}→{n.permissions[r].scope}")
            for (r, a), rank in ng.items()
            if og.get((r, a), -1) < rank
        )
        out.append(
            Change(
                f"permission:{mid}", "permission", RiskClass.RISKY if wider else RiskClass.SAFE,
                mid, n.name,
                (f"MỞ RỘNG quyền trên '{n.label}': {', '.join(wider)}" if wider else
                 f"Thu hẹp/sắp xếp lại quyền trên '{n.label}'"),
                details={"wider": wider},
            )
        )  # fmt: skip
    return out


def diff_catalogs(old: Catalog | None, new: Catalog) -> list[Change]:
    om = old.by_id() if old else {}
    nm = new.by_id()
    out: list[Change] = []
    old_names_kept = {om[i].name for i in om.keys() & nm.keys()}
    for m in new.models:
        if m.id in om:
            continue
        clash = m.name in {om[i].name for i in om.keys() - nm.keys()}
        out.append(
            Change(
                f"add_model:{m.id}", "add_model",
                RiskClass.BLOCKED if clash else RiskClass.SAFE, m.id, m.name,
                f"Tạo model mới '{m.label}' ({m.name}) với {len(m.fields)} trường, RLS theo tenant"
                f" + quyền {sorted(m.permissions)}"
                + (" — trùng tên model đang bị xóa trong cùng kế hoạch" if clash else ""),
            )
        )  # fmt: skip
    for mid in sorted(om.keys() - nm.keys()):
        m = om[mid]
        out.append(
            Change(
                f"drop_model:{mid}", "drop_model", RiskClass.DESTRUCTIVE, mid, m.name,
                f"XÓA model '{m.label}' ({m.name}) cùng toàn bộ bản ghi", probe=RowCount(mid),
            )
        )  # fmt: skip
    for m in new.models:
        if m.id not in om:
            continue
        cs = diff_model(om[m.id], m)
        for c in cs:
            if c.kind == "rename_model" and m.name in old_names_kept and om[m.id].name != m.name:
                c.cls = RiskClass.BLOCKED
                c.message += " — tên mới trùng một model đang tồn tại"
        out += cs
    return out


def probes(changes: Iterable[Change]) -> list[tuple[str, Probe]]:
    return [(c.id, c.probe) for c in changes if c.probe is not None]


def finalize(changes: list[Change], results: Mapping[str, ProbeResult]) -> list[Change]:
    """Áp kết quả probe: ghi số dòng ảnh hưởng/vi phạm, nâng hạng khi có vi phạm."""
    for c in changes:
        r = results.get(c.id)
        if c.probe is None or r is None:
            continue
        if c.on_violation is None:
            c.affected = r.count
            if r.count:
                c.message += f" ({r.count} bản ghi hiện có)"
            continue
        c.violations = r.count
        c.sample_ids = list(r.sample_ids)
        if r.count and _RANK[c.on_violation] > _RANK[c.cls]:
            c.cls = c.on_violation
            c.message += c.violation_note
    return changes


def build_plan(
    old: Catalog | None, new: Catalog, results: Mapping[str, ProbeResult] | None = None
) -> Plan:
    base = old.version if old else 0
    if new.version != base + 1:
        raise ValueError(f"spec mới phải có version = {base + 1} (đang là {new.version})")
    changes = diff_catalogs(old, new)
    if results is not None:
        finalize(changes, results)
    return Plan(base, new.version, changes, new.digest())


# --------------------------------------------------------------------- cổng apply ----
class PlanRejected(Exception):
    def __init__(self, reason: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason, self.message, self.details = reason, message, details or {}


def check_gate(plan: Plan, approved_hash: str, confirm: Iterable[str]) -> None:
    """Pha 1 gọi lại hàm này TRONG transaction đã khóa catalog, trên kế hoạch lập lại."""
    if plan.hash != approved_hash:
        raise PlanRejected(
            "plan_changed",
            f"Kế hoạch đã thay đổi kể từ lúc duyệt (duyệt {approved_hash[7:23]},"
            f" hiện {plan.hash[7:23]}) — cần duyệt lại",
            {"approved": approved_hash, "current": plan.hash},
        )
    blocked = plan.ids(RiskClass.BLOCKED)
    if blocked:
        raise PlanRejected("blocked", f"Còn thao tác BỊ CHẶN: {blocked}", {"blocked": blocked})
    missing = sorted(set(plan.ids(RiskClass.DESTRUCTIVE)) - set(confirm))
    if missing:
        raise PlanRejected(
            "confirmation_missing",
            f"Thiếu xác nhận lần 2 cho thao tác PHÁ HỦY: {missing}",
            {"missing": missing},
        )


TCV1_CLASS = {
    RiskClass.SAFE: "additive",
    RiskClass.RISKY: "tightening",
    RiskClass.DESTRUCTIVE: "destructive",
    RiskClass.BLOCKED: "destructive",
}
