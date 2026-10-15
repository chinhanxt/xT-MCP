# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Change planner: diff Catalog cũ/mới theo ID ổn định -> kế hoạch phân loại
SAFE / RISKY / DESTRUCTIVE / BLOCKED, dry-run (đếm vi phạm + diễn tập DDL rồi
rollback), và apply nguyên tử có lock_timeout + bảng phiên bản model.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field as dc_field
from typing import Any

import psycopg
from psycopg import sql

from . import compiler as C
from .spec import Catalog, FieldSpec, ModelSpec, coerce_value

CLS_VI = {"SAFE": "AN TOÀN", "RISKY": "RỦI RO", "DESTRUCTIVE": "PHÁ HỦY", "BLOCKED": "BỊ CHẶN"}
_PII_RANK = {"none": 0, "basic": 1, "sensitive": 2}
SCOPE_RANK = {"own": 0, "team": 1, "department": 2, "all": 3}


@dataclass
class Op:
    id: str
    cls: str
    kind: str
    model: str
    message: str
    steps: list[C.Step] = dc_field(default_factory=list)
    field: str | None = None
    violations: int | None = None
    sample_ids: list[str] = dc_field(default_factory=list)
    rehearsal_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "class": self.cls, "kind": self.kind, "model": self.model, "field": self.field,
                "message_vi": self.message, "violations": self.violations, "sample_ids": self.sample_ids,
                "requires_confirmation": self.cls == "DESTRUCTIVE", "rehearsal_error": self.rehearsal_error,
                "sql": [{"phase": s.phase, "sql": s.text()} for s in self.steps]}


@dataclass
class Plan:
    base_version: int
    target_version: int
    ops: list[Op]

    @property
    def hash(self) -> str:
        canon = json.dumps({"base": self.base_version, "target": self.target_version,
                            "ops": [[o.id, o.cls, [s.text() for s in o.steps]] for o in self.ops]},
                           sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode()).hexdigest()[:16]

    def summary(self) -> dict[str, int]:
        return {k: sum(1 for o in self.ops if o.cls == k) for k in CLS_VI}

    def to_json(self) -> dict[str, Any]:
        return {"base_version": self.base_version, "target_version": self.target_version,
                "plan_hash": self.hash, "summary": self.summary(),
                "destructive_op_ids": [o.id for o in self.ops if o.cls == "DESTRUCTIVE"],
                "ops": [o.to_json() for o in self.ops]}

    def render_vi(self) -> str:
        s = self.summary()
        lines = [f"KẾ HOẠCH THAY ĐỔI MODEL v{self.base_version} → v{self.target_version}  (mã kế hoạch {self.hash})",
                 "Tổng: " + ", ".join(f"{v} {CLS_VI[k]}" for k, v in s.items())]
        for o in self.ops:
            tag = CLS_VI[o.cls] + (f" – cần xác nhận lần 2 [{o.id}]" if o.cls == "DESTRUCTIVE" else "")
            line = f"  [{tag}] {o.message}"
            if o.violations is not None:
                line += f" — {o.violations} bản ghi vi phạm" + (f" (vd: {', '.join(o.sample_ids)})" if o.sample_ids else "")
            if o.rehearsal_error:
                line += f" — DIỄN TẬP LỖI: {o.rehearsal_error}"
            lines.append(line)
        if s["BLOCKED"]:
            lines.append("→ Không thể áp dụng: xử lý các mục BỊ CHẶN trước.")
        elif s["DESTRUCTIVE"]:
            lines.append("→ Có thao tác PHÁ HỦY: người duyệt phải xác nhận lại đúng từng mã thao tác.")
        return "\n".join(lines)


# ------------------------------------------------------------ đếm vi phạm ----
class Counter:
    """Đếm trên dữ liệu HIỆN TẠI (tên cột/bảng cũ) bằng vai trò migrator (BYPASSRLS)."""

    def __init__(self, conn: psycopg.Connection | None):
        self.conn = conn

    def _run(self, q: sql.Composable) -> tuple[int, list[str]]:
        if self.conn is None:
            return 0, []
        row = self.conn.execute(sql.SQL("SELECT count(*), (array_agg(id::text ORDER BY id))[1:3] FROM ({}) v").format(q)).fetchone()
        return row[0], row[1] or []

    def rows(self, tbl: str) -> int:
        return self._run(sql.SQL("SELECT id FROM {}").format(C.table(tbl)))[0]

    def false_rows(self, tbl: str, expr: sql.Composable) -> tuple[int, list[str]]:
        return self._run(sql.SQL("SELECT id FROM {} WHERE ({}) IS FALSE").format(C.table(tbl), expr))

    def null_rows(self, tbl: str, col: str) -> tuple[int, list[str]]:
        return self._run(sql.SQL("SELECT id FROM {} WHERE {} IS NULL").format(C.table(tbl), sql.Identifier(col)))

    def dup_rows(self, tbl: str, cols: list[str]) -> tuple[int, list[str]]:
        c = sql.SQL(", ").join(sql.Identifier(x) for x in cols)
        return self._run(sql.SQL(
            "SELECT id FROM (SELECT id, count(*) OVER (PARTITION BY tenant_id, {c}) n FROM {t} WHERE {nn}) d WHERE n > 1").format(
            c=c, t=C.table(tbl), nn=sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(sql.Identifier(x)) for x in cols)))


def _old_resolver(old_m: ModelSpec | None):
    """Map trường MỚI -> biểu thức trên dữ liệu CŨ (cột cũ, hoặc default nếu chưa có cột)."""
    old_by_id = {f.id: f for f in old_m.fields} if old_m else {}

    def col(f: FieldSpec) -> sql.Composable:
        if f.id in old_by_id:
            return sql.Identifier(old_by_id[f.id].name)
        if f.default is not None:
            return sql.SQL("{}::{}").format(sql.Literal(coerce_value(f, f.default)), sql.SQL(C.PG_TYPE[f.type]))
        return sql.SQL("NULL::{}").format(sql.SQL(C.PG_TYPE[f.type]))
    return col


def _looser(kind: str, old_f: FieldSpec | None, new_f: FieldSpec | None) -> bool:
    if old_f is None or new_f is None:
        return False
    if kind == "min":
        return old_f.min is not None and new_f.min is not None and new_f.min <= old_f.min
    if kind == "max":
        return old_f.max is not None and new_f.max is not None and new_f.max >= old_f.max
    if kind == "enum":
        return {o.value for o in old_f.options or []} <= {o.value for o in new_f.options or []}
    return False


def _checks_by_name(m: ModelSpec, col=C._col) -> dict[str, C.Check]:
    out = {}
    for f in m.fields:
        for c in C.field_checks(m, f, col):
            out[c.name] = c
    for c in C.rule_checks(m, col):
        out[c.name] = c
    return out


def _check_owner(m: ModelSpec, name: str) -> FieldSpec | None:
    for f in m.fields:
        if name in {C.cname(m, f, "ck", k) for k in ("min", "max", "re", "enum", "json", "nn")}:
            return f
    return None


def _perm_grants(m: ModelSpec) -> dict[tuple[str, str], int]:
    return {(r, a): SCOPE_RANK[p.scope] for r, p in m.permissions.items() for a in p.actions}


def diff_model(o: ModelSpec, n: ModelSpec, cnt: Counter) -> list[Op]:
    ops: list[Op] = []
    t_old, T = o.name, C.table(n)
    oid = lambda *p: ":".join([p[0], n.id] + list(p[1:]))
    if o.name != n.name:
        ops.append(Op(oid("rename_model"), "DESTRUCTIVE", "rename_model", n.name,
                      f"Đổi tên model '{o.name}' → '{n.name}' (giữ dữ liệu; client/agent dùng tên cũ sẽ lỗi)",
                      [C.Step(sql.SQL("ALTER TABLE {} RENAME TO {}").format(C.table(o.name), sql.Identifier(n.name)), order=5)]))
    if o.label != n.label:
        ops.append(Op(oid("label_model"), "SAFE", "label", n.name, f"Đổi nhãn model thành '{n.label}'",
                      [C.Step(sql.SQL("COMMENT ON TABLE {} IS {}").format(T, sql.Literal(n.label)), order=90)]))
    of, nf = {f.id: f for f in o.fields}, {f.id: f for f in n.fields}
    # cột bị xóa
    for fid in of.keys() - nf.keys():
        f = of[fid]
        nrows = cnt.rows(t_old)
        ops.append(Op(oid("drop_field", fid), "DESTRUCTIVE", "drop_field", n.name,
                      f"Xóa trường '{f.label}' ({f.name}) khỏi '{n.label}' — mất dữ liệu cột này của {nrows} bản ghi",
                      [C.Step(sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(T, sql.Identifier(f.name)), order=22)], field=f.name))
    # rename
    old_names_kept = {of[i].name for i in of.keys() & nf.keys()}
    for fid in sorted(of.keys() & nf.keys()):
        a, b = of[fid], nf[fid]
        if a.name != b.name:
            if b.name in old_names_kept and b.name != a.name:
                ops.append(Op(oid("rename_field", fid), "BLOCKED", "rename_field", n.name,
                              f"Đổi tên '{a.name}' → '{b.name}' trùng tên một cột đang tồn tại (hoán đổi tên): tách thành 2 lần thay đổi", field=b.name))
                continue
            ops.append(Op(oid("rename_field", fid), "DESTRUCTIVE", "rename_field", n.name,
                          f"Đổi tên trường '{a.name}' → '{b.name}' trong '{n.label}' (RENAME COLUMN, giữ dữ liệu)",
                          [C.Step(sql.SQL("ALTER TABLE {} RENAME COLUMN {} TO {}").format(T, sql.Identifier(a.name), sql.Identifier(b.name)), order=25)],
                          field=b.name))
    # thêm cột
    for fid in [f.id for f in n.fields if f.id not in of]:
        f = nf[fid]
        steps = [C.Step(sql.SQL("ALTER TABLE {} ADD COLUMN {}").format(T, C.column_def(f)), order=30)]
        cls, msg = "SAFE", f"Thêm trường '{f.label}' ({f.name}, {f.type}) vào '{n.label}' — cho phép trống"
        viol = None
        if f.required:
            nrows = cnt.rows(t_old)
            if f.default is not None:
                cls, msg = "RISKY", (f"Thêm trường BẮT BUỘC '{f.label}' ({f.name}) với giá trị mặc định {f.default!r} — "
                                     f"{nrows} bản ghi hiện có sẽ nhận giá trị này (PG11+: không rewrite bảng)")
            elif nrows:
                cls, msg = "BLOCKED", (f"Thêm trường BẮT BUỘC '{f.label}' ({f.name}) không có default nhưng bảng đã có {nrows} bản ghi — "
                                       f"hãy khai báo 'default' (backfill) hoặc để không bắt buộc")
                viol = nrows
            else:
                cls, msg = "RISKY", f"Thêm trường BẮT BUỘC '{f.label}' ({f.name}) — bảng đang trống nên áp dụng được"
        for c in C.field_checks(n, f):
            steps += [C.Step(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK ({}) NOT VALID").format(T, sql.Identifier(c.name), c.expr), order=40),
                      C.Step(sql.SQL("ALTER TABLE {} VALIDATE CONSTRAINT {}").format(T, sql.Identifier(c.name)), phase="post", order=40)]
        if f.unique:
            steps.append(C.unique_index(n, f, True))
        if f.type == "ref":
            fk = C.fk_step(n, f)
            steps += [C.Step(sql.SQL("{} NOT VALID").format(fk.sql), order=60),
                      C.Step(sql.SQL("ALTER TABLE {} VALIDATE CONSTRAINT {}").format(T, sql.Identifier(C.cname(n, f, "fk"))), phase="post", order=60),
                      C.Step(sql.SQL("CREATE INDEX CONCURRENTLY IF NOT EXISTS {} ON {} (tenant_id, {})").format(
                          sql.Identifier(C.iname(C.cname(n, f, "ixr"))), T, sql.Identifier(f.name)), phase="concurrent", order=70)]
        steps.append(C.Step(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(T, sql.Identifier(f.name), sql.Literal(f.label)), order=90))
        ops.append(Op(oid("add_field", fid), cls, "add_field", n.name, msg, steps, field=f.name, violations=viol))
    # thay đổi thuộc tính trường
    col_old = _old_resolver(o)
    for fid in sorted(of.keys() & nf.keys()):
        a, b = of[fid], nf[fid]
        c = sql.Identifier(b.name)
        if a.type != b.type or a.ref != b.ref:
            ops.append(Op(oid("retype_field", fid), "BLOCKED", "retype_field", n.name,
                          f"Đổi kiểu '{b.label}' {a.type}→{b.type}{' ref '+str(a.ref)+'→'+str(b.ref) if a.ref != b.ref else ''}: "
                          f"chưa hỗ trợ — tạo trường mới, chuyển dữ liệu, rồi xóa trường cũ", field=b.name))
            continue
        if a.label != b.label:
            ops.append(Op(oid("label_field", fid), "SAFE", "label", n.name, f"Đổi nhãn trường {b.name} thành '{b.label}'",
                          [C.Step(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(T, c, sql.Literal(b.label)), order=90)], field=b.name))
        if a.default != b.default:
            st = (sql.SQL("ALTER TABLE {} ALTER COLUMN {} SET DEFAULT {}").format(T, c, sql.Literal(coerce_value(b, b.default)))
                  if b.default is not None else sql.SQL("ALTER TABLE {} ALTER COLUMN {} DROP DEFAULT").format(T, c))
            ops.append(Op(oid("default_field", fid), "SAFE", "default", n.name, f"Đổi giá trị mặc định '{b.label}' → {b.default!r}",
                          [C.Step(st, order=32)], field=b.name))
        if a.required and not b.required:
            ops.append(Op(oid("nullable_field", fid), "SAFE", "loosen", n.name, f"Bỏ bắt buộc '{b.label}'",
                          [C.Step(sql.SQL("ALTER TABLE {} ALTER COLUMN {} DROP NOT NULL").format(T, c), order=35)], field=b.name))
        if b.required and not a.required:
            nn, sample = cnt.null_rows(t_old, a.name)
            ck = sql.Identifier(C.cname(n, b, "ck", "nn"))
            steps = []
            if nn and b.default is not None:
                steps.append(C.Step(sql.SQL("UPDATE {} SET {} = {} WHERE {} IS NULL").format(T, c, sql.Literal(coerce_value(b, b.default)), c), order=34))
            steps += [C.Step(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK ({} IS NOT NULL) NOT VALID").format(T, ck, c), order=40),
                      C.Step(sql.SQL("ALTER TABLE {} VALIDATE CONSTRAINT {}").format(T, ck), phase="post", order=40),
                      C.Step(sql.SQL("ALTER TABLE {} ALTER COLUMN {} SET NOT NULL").format(T, c), phase="post", order=41),
                      C.Step(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(T, ck), phase="post", order=42)]
            if nn and b.default is None:
                ops.append(Op(oid("require_field", fid), "BLOCKED", "tighten", n.name,
                              f"Đặt '{b.label}' thành bắt buộc nhưng có bản ghi đang trống — điền dữ liệu hoặc khai báo default để backfill",
                              steps, field=b.name, violations=nn, sample_ids=sample))
            else:
                ops.append(Op(oid("require_field", fid), "RISKY", "tighten", n.name,
                              f"Đặt '{b.label}' thành bắt buộc" + (f" (backfill {nn} bản ghi = {b.default!r})" if nn else ""),
                              steps, field=b.name, violations=nn, sample_ids=sample))
        if b.unique and not a.unique:
            d, sample = cnt.dup_rows(t_old, [a.name])
            ops.append(Op(oid("unique_field", fid), "BLOCKED" if d else "RISKY", "tighten", n.name,
                          f"Đặt '{b.label}' là duy nhất (CREATE UNIQUE INDEX CONCURRENTLY)" + (" — có giá trị trùng, xử lý trước" if d else ""),
                          [C.unique_index(n, b, True)], field=b.name, violations=d, sample_ids=sample))
        if a.unique and not b.unique:
            ops.append(Op(oid("ununique_field", fid), "SAFE", "loosen", n.name, f"Bỏ ràng buộc duy nhất '{b.label}'",
                          [C.Step(sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(sql.Identifier(C.LAYOUT.get().schema, C.iname(C.cname(n, b, "ux")))), phase="concurrent", order=70)],
                          field=b.name))
    # CHECK (min/max/regex/enum/json/rule) theo tên ổn định
    oc, nc = _checks_by_name(o), _checks_by_name(n)
    nc_old = _checks_by_name(n, col_old)   # cùng điều kiện mới nhưng trên cột cũ, để đếm
    oc_renamed = _checks_by_name(o, _rename_to(o, n))  # điều kiện cũ viết bằng tên cột mới
    for name in sorted(oc.keys() - nc.keys()):
        fo_old = _check_owner(o, name)
        if fo_old is not None and fo_old.id not in nf:
            continue  # DROP COLUMN tự xóa constraint của cột
        ops.append(Op(f"drop_check:{n.id}:{name}", "SAFE", "loosen", n.name, f"Bỏ ràng buộc: {oc[name].message}",
                      [C.Step(sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(T, sql.Identifier(name)), order=35)]))
    for name in sorted(nc.keys()):
        new = nc[name]
        if name in oc and oc_renamed[name].expr.as_string() == new.expr.as_string():
            continue  # không đổi (so sánh sau khi áp tên cột mới)
        fo = _check_owner(n, name)
        if fo is not None and fo.id not in of:
            continue  # thuộc trường mới: đã xử lý ở add_field
        steps = []
        if name in oc:
            steps.append(C.Step(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(T, sql.Identifier(name)), order=35))
        steps += [C.Step(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} CHECK ({}) NOT VALID").format(T, sql.Identifier(name), new.expr), order=40),
                  C.Step(sql.SQL("ALTER TABLE {} VALIDATE CONSTRAINT {}").format(T, sql.Identifier(name)), phase="post", order=40)]
        if name in oc and fo is not None and _looser(new.kind, of.get(fo.id), fo):
            ops.append(Op(f"check:{n.id}:{name}", "SAFE", "loosen", n.name, f"Nới ràng buộc: {new.message}", steps, field=fo.name))
            continue
        v, sample = cnt.false_rows(t_old, nc_old[name].expr)
        verb = "Siết ràng buộc" if name in oc else "Thêm ràng buộc"
        ops.append(Op(f"check:{n.id}:{name}", "BLOCKED" if v else "RISKY", "tighten", n.name,
                      f"{verb}: {new.message}" + (" — dữ liệu hiện có vi phạm, sửa trước khi áp dụng" if v else " (đã kiểm dữ liệu hiện có)"),
                      steps, field=fo.name if fo else None, violations=v, sample_ids=sample))
    # index
    oi, ni = {x.id: x for x in o.indexes}, {x.id: x for x in n.indexes}
    for iid in sorted(oi.keys() | ni.keys()):
        a, b = oi.get(iid), ni.get(iid)
        if a and b and a.unique == b.unique and [_fid(o, x) for x in a.fields] == [_fid(n, x) for x in b.fields]:
            continue
        steps, cls, viol, sample = [], "SAFE", None, []
        if a:
            steps.append(C.Step(sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(sql.Identifier(C.LAYOUT.get().schema, C.iname(f"ix_{n.id}_{iid}"))), phase="concurrent", order=69))
        if b:
            steps.append(C.index_step(n, b, True))
            if b.unique:
                viol, sample = (cnt.dup_rows(t_old, [of[_fid(n, x)].name for x in b.fields])
                                if all(_fid(n, x) in of for x in b.fields) else (0, []))
                cls = "BLOCKED" if viol else "RISKY"
        ops.append(Op(f"index:{n.id}:{iid}", cls, "index", n.name,
                      ("Tạo" if not a else "Xóa" if not b else "Đổi") + f" index {iid} trên '{n.label}'" + (" (CONCURRENTLY, không khóa ghi)"),
                      steps, violations=viol, sample_ids=sample))
    # G-M2: nhãn PII đổi => tạo lại trigger audit (danh sách cột sensitive)
    o_sens = {f.id for f in o.fields if f.pii == "sensitive"}
    n_sens = {f.id for f in n.fields if f.pii == "sensitive"}
    lowered = sorted(nf[i].name for i in (of.keys() & nf.keys()) if _PII_RANK[nf[i].pii] < _PII_RANK[of[i].pii])
    pii_changed = [nf[i].name for i in (of.keys() & nf.keys()) if nf[i].pii != of[i].pii]
    if o_sens != n_sens or pii_changed:
        ops.append(Op(f"pii:{n.id}", "RISKY" if lowered else "SAFE", "pii", n.name,
                      (f"HẠ mức DLCN: {lowered} (audit/egress sẽ xử lý như mức thấp hơn); " if lowered else "") +
                      f"đổi nhãn DLCN: {[f'{of[i].name}: {of[i].pii}→{nf[i].pii}' for i in sorted(of.keys() & nf.keys()) if nf[i].pii != of[i].pii]}; "
                      f"cột audit chỉ lưu HMAC: {sorted(nf[i].name for i in n_sens)}",
                      C.audit_trigger(n, replace=True)))
    # G-M1: quyền cột (thêm cột hoặc đổi managed_by)
    freed = sorted(nf[i].name for i in (of.keys() & nf.keys()) if of[i].managed_by and not nf[i].managed_by)
    if freed or any(of.get(i) is None or of[i].managed_by != nf[i].managed_by for i in nf):
        ops.append(Op(f"grants:{n.id}", "RISKY" if freed else "SAFE", "grants", n.name,
                      (f"Bỏ quản lý workflow cho {freed}: CRUD tổng quát sẽ ghi được" if freed else
                       f"Cập nhật quyền cột: trường do workflow quản = {sorted(f.name for f in n.fields if f.managed_by)}"),
                      [C.Step(st.sql, order=87) for st in C.grant_steps(n)]))
    # quyền
    if o.permissions != n.permissions:
        og, ng = _perm_grants(o), _perm_grants(n)
        wider = sorted(f"{r}:{a}" + ("" if (r, a) not in og else f" {_scope(o, r)}→{_scope(n, r)}")
                       for (r, a), rank in ng.items() if og.get((r, a), -1) < rank)
        ops.append(Op(f"perm:{n.id}", "RISKY" if wider else "SAFE", "permission", n.name,
                      (f"MỞ RỘNG quyền trên '{n.label}': {', '.join(wider)}" if wider else f"Thu hẹp/sắp xếp lại quyền trên '{n.label}'"),
                      C.policy_steps(n, drop_existing=True)))
    return ops


def _fid(m: ModelSpec, name: str) -> str:
    return m.field(name).id


def _scope(m: ModelSpec, role: str) -> str:
    return m.permissions[role].scope


def _rename_to(o: ModelSpec, n: ModelSpec):
    nf = {f.id: f for f in n.fields}

    def col(f: FieldSpec) -> sql.Composable:
        return sql.Identifier(nf[f.id].name if f.id in nf else f.name)
    return col


def plan(old: Catalog | None, new: Catalog, conn: psycopg.Connection | None = None, rehearse: bool = False) -> Plan:
    base = old.version if old else 0
    if new.version != base + 1:
        raise ValueError(f"spec mới phải có version = {base + 1} (đang là {new.version})")
    cnt = Counter(conn)
    om = {m.id: m for m in old.models} if old else {}
    nm = {m.id: m for m in new.models}
    ops: list[Op] = []
    for mid in [m.id for m in new.models if m.id not in om]:
        m = nm[mid]
        ops.append(Op(f"add_model:{mid}", "SAFE", "add_model", m.name,
                      f"Tạo model mới '{m.label}' ({m.name}) với {len(m.fields)} trường, RLS theo tenant + quyền {sorted(m.permissions)}",
                      C.create_model_steps(m)))
    for mid in sorted(om.keys() - nm.keys()):
        m = om[mid]
        ops.append(Op(f"drop_model:{mid}", "DESTRUCTIVE", "drop_model", m.name,
                      f"XÓA model '{m.label}' ({m.name}) cùng {cnt.rows(m.name)} bản ghi",
                      [C.Step(sql.SQL("DROP TABLE {} RESTRICT").format(C.table(m)), order=3)]))
    for mid in [m.id for m in new.models if m.id in om]:
        ops += diff_model(om[mid], nm[mid], cnt)
    ops.sort(key=lambda o: (min((s.order for s in o.steps), default=99), o.id))
    p = Plan(base, new.version, ops)
    if rehearse and conn is not None:
        _rehearse(conn, p)
    return p


def _rehearse(conn: psycopg.Connection, p: Plan) -> None:
    """Chạy thử toàn bộ bước 'tx' trong transaction rồi ROLLBACK; ghi lỗi theo thao tác."""
    try:
        with conn.transaction(force_rollback=True):
            conn.execute("SET LOCAL lock_timeout = '2s'")
            for st, op in _ordered_steps(p, "tx"):
                try:
                    with conn.transaction():
                        conn.execute(st.sql)
                except psycopg.Error as e:
                    if op.rehearsal_error is None:   # giữ lỗi gốc, bỏ lỗi dây chuyền
                        op.rehearsal_error = (e.diag.message_primary or str(e))[:200]
                    op.cls = "BLOCKED"
    except psycopg.Error:
        pass


def _ordered_steps(p: Plan, phase: str):
    pairs = [(s, o) for o in p.ops for s in o.steps if s.phase == phase]
    return sorted(pairs, key=lambda x: x[0].order)  # sort ổn định: giữ thứ tự trong cùng order


# ------------------------------------------------------------------ apply ----
def load_current(conn: psycopg.Connection, tenant_seq: int = 0) -> Catalog | None:
    row = conn.execute("SELECT spec FROM xt_meta.catalog_version WHERE tenant_seq = %s ORDER BY version DESC LIMIT 1",
                       (tenant_seq,)).fetchone()
    return Catalog.model_validate(row[0]) if row else None


class ApplyError(Exception):
    pass


def apply(conn: psycopg.Connection, new: Catalog, approved_plan_hash: str, confirm: set[str],
          proposed_by: str, approved_by: str, lock_timeout: str = "3s", tenant_seq: int = 0,
          defer_post: bool = False) -> dict[str, Any]:
    """conn: vai trò xt_migrator, autocommit=True. defer_post=True: pha 2 vào xt_meta.apply_job
    (cùng transaction pha 1) để worker chạy; False: chạy pha 2 ngay (như spike-model-engine)."""
    assert conn.autocommit, "cần autocommit để chạy bước CONCURRENTLY sau commit"
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('xt_meta:catalog:' || %s, 0))", (str(tenant_seq),))
        conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(lock_timeout)))
        conn.execute("SET LOCAL statement_timeout = '60s'")
        old = load_current(conn, tenant_seq)
        p = plan(old, new, conn)   # lập lại kế hoạch trên dữ liệu hiện tại, trong cùng transaction
        if p.hash != approved_plan_hash:
            raise ApplyError(f"Kế hoạch đã thay đổi kể từ lúc duyệt (duyệt {approved_plan_hash}, hiện {p.hash}) — cần duyệt lại")
        blocked = [o.id for o in p.ops if o.cls == "BLOCKED"]
        if blocked:
            raise ApplyError(f"Còn thao tác BỊ CHẶN: {blocked}")
        need = {o.id for o in p.ops if o.cls == "DESTRUCTIVE"}
        if need - confirm:
            raise ApplyError(f"Thiếu xác nhận lần 2 cho thao tác PHÁ HỦY: {sorted(need - confirm)}")
        for st, _ in _ordered_steps(p, "tx"):
            conn.execute(st.sql)
        spec_json = new.model_dump(mode="json", by_alias=True)
        conn.execute("INSERT INTO xt_meta.catalog_version (tenant_seq, version, spec, spec_hash, plan, plan_hash, proposed_by, approved_by) "
                     "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                     (tenant_seq, new.version, psycopg.types.json.Jsonb(spec_json),
                      hashlib.sha256(json.dumps(spec_json, sort_keys=True).encode()).hexdigest(),
                      psycopg.types.json.Jsonb(p.to_json()), p.hash, proposed_by, approved_by))
        post_steps = _ordered_steps(p, "post") + _ordered_steps(p, "concurrent")
        if defer_post:
            for i, (st, op) in enumerate(post_steps):
                conn.execute("INSERT INTO xt_meta.apply_job (tenant_seq, version, ord, phase, op_id, sql) VALUES (%s,%s,%s,%s,%s,%s)",
                             (tenant_seq, new.version, i, st.phase, op.id, st.text()))
    if defer_post:
        return {"version": new.version, "plan_hash": p.hash, "tx_steps": len(_ordered_steps(p, "tx")), "queued": len(post_steps)}
    # Pha sau commit: VALIDATE (transaction ngắn riêng), CREATE/DROP INDEX CONCURRENTLY (autocommit).
    post = []
    for st, op in _ordered_steps(p, "post") + _ordered_steps(p, "concurrent"):
        try:
            if st.phase == "post":
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(lock_timeout)))
                    conn.execute(st.sql)
            else:
                conn.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(lock_timeout)))
                conn.execute(st.sql)
            post.append({"op": op.id, "sql": st.text(), "ok": True})
        except psycopg.Error as e:
            post.append({"op": op.id, "sql": st.text(), "ok": False, "error": e.diag.message_primary})
    conn.execute("RESET lock_timeout")
    conn.execute("UPDATE xt_meta.catalog_version SET post_status = %s WHERE tenant_seq = %s AND version = %s",
                 (psycopg.types.json.Jsonb(post), tenant_seq, new.version))
    return {"version": new.version, "plan_hash": p.hash, "tx_steps": len(_ordered_steps(p, "tx")), "post": post}


def run_jobs(conn: psycopg.Connection, lock_timeout: str = "3s", max_attempts: int = 3, limit: int = 10_000) -> dict[str, int]:
    """Worker pha 2: lấy job pending theo thứ tự (tenant, version, ord), mỗi job 1 transaction ngắn
    (post) hoặc autocommit (CONCURRENTLY). Lỗi: tăng attempts; quá max => 'dead' và dừng các job
    sau của CÙNG tenant/version (thứ tự có ý nghĩa). Index CONCURRENTLY lỗi để lại index INVALID
    => DROP INDEX CONCURRENTLY IF EXISTS trước khi thử lại (câu CREATE có IF NOT EXISTS sẽ bỏ qua index invalid)."""
    stats = {"done": 0, "failed": 0, "dead": 0}
    blocked: set[tuple[int, int]] = set()
    jobs = conn.execute("SELECT id, tenant_seq, version, phase, sql, attempts FROM xt_meta.apply_job "
                        "WHERE status IN ('pending','failed') ORDER BY tenant_seq, version, ord LIMIT %s", (limit,)).fetchall()
    for jid, ts, ver, phase, q, att in jobs:
        if (ts, ver) in blocked:
            continue
        try:
            if phase == "post":
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(lock_timeout)))
                    conn.execute(q)
            else:
                conn.execute(sql.SQL("SET lock_timeout = {}").format(sql.Literal(lock_timeout)))
                if q.startswith("CREATE") and " INDEX CONCURRENTLY IF NOT EXISTS " in q:
                    idx = q.split(" IF NOT EXISTS ", 1)[1].split(" ON ", 1)[0]
                    sch = q.split(" ON ", 1)[1].split(".", 1)[0]
                    conn.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {sch}.{idx}") if _invalid_index(conn, sch, idx) else None
                conn.execute(q)
                conn.execute("RESET lock_timeout")
            conn.execute("UPDATE xt_meta.apply_job SET status='done', attempts=attempts+1, last_error=NULL WHERE id=%s", (jid,))
            stats["done"] += 1
        except psycopg.Error as e:
            st = "dead" if att + 1 >= max_attempts else "failed"
            conn.execute("UPDATE xt_meta.apply_job SET status=%s, attempts=attempts+1, last_error=%s WHERE id=%s",
                         (st, (e.diag.message_primary or str(e))[:300], jid))
            stats[st] += 1
            blocked.add((ts, ver))
    return stats


def _invalid_index(conn: psycopg.Connection, schema_q: str, idx_q: str) -> bool:
    r = conn.execute("SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                     "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s",
                     (schema_q.strip('"'), idx_q.strip('"').replace('""', '"'))).fetchone()
    return bool(r and r[0])
