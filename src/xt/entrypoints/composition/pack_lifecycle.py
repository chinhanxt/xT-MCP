# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tắt / bật lại ứng dụng Tầng 3 (gói đã cài) theo tenant — G8, D-UNI-T3-1, nghiệm thu U15.

Cùng luồng với `xt pack install` (architecture-v2 §6.1 "đề xuất + duyệt"):

1. `plan_lifecycle`: hàm thuần trên các dòng `xt_meta.pack_install` của tenant; kiểm phụ thuộc
   (không tắt gói mà gói đang bật khác phụ thuộc; không bật gói khi phụ thuộc của nó đang tắt) và
   trả kế hoạch tiếng Việt + `digest` (gắn tenant, gói, hành động và ảnh chụp trạng thái mọi gói,
   nên đổi trạng thái giữa lúc xem và lúc duyệt làm mã không khớp).
2. `apply_lifecycle(confirm=digest)`: người duyệt khác người đề xuất; dưới advisory lock của gói
   (cùng khóa với `save_pack`) lập lại kế hoạch, so digest, rồi UPDATE `pack_install`. Trigger
   audit meta (migration 0006) ghi thay đổi vào hash-chain của tenant.

Tắt không xóa gì: bảng, dữ liệu, policy và định nghĩa quy trình giữ nguyên; lõi chặn ghi/ẩn model
(`RecordService._enabled`) và coi quy trình của gói là không kích hoạt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from xt.adapters.db.connection import AuditKey
from xt.adapters.db.integration import AdminError, connect, pack_rows, set_pack_status
from xt.adapters.db.process_incidents import list_app_incidents
from xt.domain.model.context import TenantRef
from xt.entrypoints.composition.pack_recovery import recover_app_incidents

Action = Literal["disable", "enable"]
_TARGET: dict[str, str] = {"disable": "disabled", "enable": "enabled"}
_VERB = {"disable": "tắt", "enable": "bật lại"}


@dataclass
class LifecyclePlan:
    tenant: TenantRef
    alias: str
    pack: str
    action: Action
    from_status: str | None
    to_status: str
    models: list[str]
    snapshot: list[tuple[str, str, str]]
    problems: list[str] = field(default_factory=list)
    incidents: list[dict[str, str]] = field(default_factory=list)
    previous_digest: str | None = None
    retried: int = 0
    failed: int = 0
    skipped: int = 0

    @property
    def digest(self) -> str:
        body = {
            "tenant": str(self.tenant.id),
            "pack": self.pack,
            "action": self.action,
            "from": self.from_status,
            "to": self.to_status,
            "models": self.models,
            "packs": self.snapshot,
            "incidents": self.incidents,
        }
        canon = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canon.encode()).hexdigest()

    def render_vi(self) -> str:
        lines = [
            f"{_VERB[self.action].capitalize()} ứng dụng {self.pack} ở tenant {self.alias}"
            f" (seq {self.tenant.seq})",
            f"Trạng thái: {self.from_status or '(chưa cài)'} -> {self.to_status}",
            "Model ảnh hưởng: " + (", ".join(self.models) or "(không)"),
            "Dữ liệu, bảng, policy và định nghĩa quy trình được giữ nguyên.",
        ]
        if self.action == "enable":
            lines += [f"Sự cố sẽ thử lại: eligible={len(self.incidents)}"]
            lines += [
                f"  - instance={i['instance']} incident={i['incident']} model={i['model']}"
                for i in self.incidents
            ]
        if self.problems:
            lines += ["", f"KHÔNG {_VERB[self.action].upper()} ĐƯỢC:"]
            lines += [f"  - {p}" for p in self.problems]
        else:
            lines += ["", f"Mã xác nhận (digest): {self.digest}"]
        return "\n".join(lines)


def plan_lifecycle(
    rows: Sequence[Mapping[str, Any]],
    *,
    tenant: TenantRef,
    alias: str,
    pack: str,
    action: Action,
    incidents: Sequence[Mapping[str, str]] = (),
) -> LifecyclePlan:
    by = {str(r["pack"]): r for r in rows}
    target = _TARGET[action]
    snapshot = sorted((p, str(r["status"]), str(r["version"])) for p, r in by.items())
    cur = by.get(pack)
    problems: list[str] = []
    if cur is None:
        return LifecyclePlan(tenant, alias, pack, action, None, target, [], snapshot,
                             [f"gói {pack!r} chưa cài ở tenant này"])  # fmt: skip
    status = str(cur["status"])
    models = sorted(cur["models"] or [])
    if status == target and action == "disable":
        problems.append(f"gói {pack!r} đã ở trạng thái {target}")
    if cur["models"] is None or cur["depends_on"] is None:
        problems.append(
            f"gói {pack!r} cài trước khi có vòng đời ứng dụng (chưa ghi model/phụ thuộc):"
            " cài lại gói bằng `xt pack install` rồi thử lại"
        )
    if action == "disable":
        for p, r in sorted(by.items()):
            if p == pack or r["status"] != "enabled":
                continue
            if r["depends_on"] is None:
                problems.append(f"gói {p!r} chưa rõ phụ thuộc (cài lại gói đó trước)")
            elif pack in r["depends_on"]:
                problems.append(f"gói đang bật {p!r} phụ thuộc {pack!r}: tắt {p!r} trước")
    else:
        for d in cur["depends_on"] or []:
            if d not in by or by[d]["status"] != "enabled":
                problems.append(f"gói phụ thuộc {d!r} chưa bật: bật {d!r} trước")
    return LifecyclePlan(
        tenant,
        alias,
        pack,
        action,
        status,
        target,
        models,
        snapshot,
        problems,
        sorted((dict(i) for i in incidents), key=lambda i: (i["instance"], i["incident"])),
        cur.get("status_digest"),
    )


def read_lifecycle_plan(
    conn: Any, *, tenant: TenantRef, alias: str, pack: str, action: Action
) -> LifecyclePlan:
    plan = plan_lifecycle(
        pack_rows(conn, tenant), tenant=tenant, alias=alias, pack=pack, action=action
    )
    if plan.action == "enable" and not plan.problems:
        plan.incidents = list_app_incidents(conn, plan.tenant, plan.pack, plan.models)
    return plan


def apply_lifecycle(
    migrator_dsn: str,
    *,
    tenant: TenantRef,
    alias: str,
    pack: str,
    action: Action,
    confirm: str,
    proposed_by: str,
    approved_by: str,
    app_dsn: str | None = None,
    audit_key: AuditKey | None = None,
) -> LifecyclePlan:
    if not approved_by or approved_by == proposed_by:
        raise AdminError("người duyệt phải khác người đề xuất (tách nhiệm vụ)")
    with connect(migrator_dsn) as conn:
        # Keep disable/install out across the status commit AND xt_app retry transactions.
        # A session lock is necessary: xt_migrator cannot assume xt_app's trusted RLS context.
        session_lock = action == "enable"
        key = (f"xt:pack:{tenant.id}",)
        if session_lock:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", key)
        try:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", key)
                plan = read_lifecycle_plan(
                    conn, tenant=tenant, alias=alias, pack=pack, action=action
                )
                if plan.problems:
                    raise AdminError("; ".join(plan.problems))
                replay = (
                    action == "enable"
                    and plan.from_status == "enabled"
                    and not plan.incidents
                    and plan.previous_digest == confirm
                )
                if plan.digest != confirm and not replay:
                    raise AdminError(
                        f"mã xác nhận không khớp kế hoạch hiện tại ({plan.digest}); "
                        "xem lại kế hoạch rồi duyệt"
                    )
                if plan.incidents and not app_dsn:
                    raise AdminError("XT_DB_APP_URL chưa đặt (cần role xt_app để thử lại sự cố)")
                if plan.from_status != plan.to_status:
                    set_pack_status(
                        conn,
                        tenant,
                        pack=pack,
                        status=plan.to_status,
                        digest=plan.digest,
                        proposed_by=proposed_by,
                        approved_by=approved_by,
                    )
            if plan.incidents:
                assert app_dsn is not None
                counts = recover_app_incidents(
                    app_dsn,
                    tenant=tenant,
                    pack=pack,
                    models=plan.models,
                    incidents=plan.incidents,
                    approved_by=approved_by,
                    digest=plan.digest,
                    audit_key=audit_key,
                )
                plan.retried, plan.failed, plan.skipped = (
                    counts["retried"],
                    counts["failed"],
                    counts["skipped"],
                )
        finally:
            if session_lock:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", key)
    return plan
