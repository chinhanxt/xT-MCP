# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""inspect_workspace, get_history (dòng thời gian + audit hash-chain), file (chưa có ở v0.1)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.application.model_engine.service import APP_ADMIN_ROLE
from xt.application.tools.context import CallContext, iso
from xt.application.tools.dispatcher import GATED_CAPABILITIES, ToolSettings
from xt.application.tools.ports import Args, CommitResult, Data, MutationPlan
from xt.contracts.tools import CATALOG
from xt.entrypoints.composition.pii import effective_mode
from xt.entrypoints.composition.runtime import Caller, Runtime
from xt.entrypoints.composition.util import engine_call, parse_record_ref, signed_page, unavailable

_OP_ACTION = {"INSERT": "created", "UPDATE": "updated", "DELETE": "deleted"}


class Workspace:
    def __init__(self, rt: Runtime, settings: ToolSettings) -> None:
        self.rt, self.settings = rt, settings

    def inspect(self, ctx: CallContext, args: Args) -> Data:
        if "workspace" in args:
            unavailable("named workspaces")
        caller = self.rt.caller(ctx)
        want = set(
            args.get("include")
            or ("me", "models", "processes", "task_types", "policies", "apps", "tools", "limits")
        )
        me: Data = {
            "ref": f"person:{caller.person.id}",
            "name": caller.person.name,
            "kind": "agent" if caller.is_agent else "human",
            "acting_for": (
                {"ref": f"person:{caller.person.id}", "name": caller.person.name}
                if caller.is_agent
                else None
            ),
            "autonomy_ceiling": f"L{caller.autonomy}" if caller.is_agent else "L3",
            "client": caller.client_id,
            "client_class": ctx.client_class.value,
        }
        out: Data = {
            "me": me,
            "tenant": {
                "name": caller.profile.alias,
                "locale": "vi-VN",
                "timezone": caller.profile.timezone,
            },
            "workspace": {"ref": f"workspace:{caller.profile.alias}", "name": caller.profile.alias},
            "pii_mode": effective_mode(caller.profile.llm_egress, ctx.client_class),
        }
        bindings = self.rt.store.bindings(caller.actor)
        if "models" in want or "task_types" in want:
            current = self.rt.catalogs.current(caller.actor)
            off = self.rt.catalogs.disabled(caller.actor).models
            models = []
            for m in current[1].models if current else []:
                if m.name in off or not any(r in m.permissions for r in caller.actor.roles):
                    continue
                b = bindings.get(m.name)
                models.append(
                    {"id": m.name, "label": m.label, "primitive": b.primitive if b else "record"}
                )
            if "models" in want:
                out["models"] = models
            if "task_types" in want:
                out["task_types"] = [
                    {"id": m["id"], "label": m["label"]} for m in models if m["primitive"] == "task"
                ]
        if "processes" in want:
            out["processes"] = [
                {"id": d.process_id, "label": str(d.spec.get("name", d.process_id)),
                 "trigger": str((d.spec.get("trigger") or {}).get("kind", "manual"))}
                for d in self.rt.store.definitions(caller.actor)
            ]  # fmt: skip
        if "policies" in want:
            pol = self.rt.store.active_policy(caller.actor)
            out["policies"] = (
                [{"id": f"iam_policy:{pol[0]}", "kind": "iam", "label": f"IAM revision {pol[0]}"}]
                if pol
                else []
            )
        if "apps" in want:
            out["apps"] = self._apps(caller)
        if "tools" in want:
            tools = []
            for c in CATALOG:
                row: Data = {"name": c.name, "available": self.settings.available(c.name)}
                if not row["available"]:
                    version, remediation = GATED_CAPABILITIES[c.name]
                    row["reason"] = f"planned for {version}"
                    row["remediation"] = remediation
                tools.append(row)
            out["tools"] = tools
        if "limits" in want:
            out["limits"] = {
                "max_page_size": 200,
                "confirmation_ttl_seconds": int(self.settings.confirmation_ttl.total_seconds()),
            }
        return out

    def _apps(self, caller: Caller) -> list[Data]:
        """Ứng dụng Tầng 3 đã cài (gói có khối `app`), theo thứ tự tên gói. Ứng dụng đang tắt chỉ
        hiện cho admin (để bật lại); người dùng khác không thấy."""
        admin = APP_ADMIN_ROLE in caller.actor.roles
        apps: list[Data] = []
        for p in self.rt.store.packs(caller.actor):
            app = p.get("app")
            status = p.get("status") or "enabled"
            if not app or (status != "enabled" and not admin):
                continue
            apps.append(
                {
                    "id": app["id"],
                    "pack": p["pack"],
                    "title": app["title"],
                    "group": app["group"],
                    "status": status,
                    "version": p["version"],
                    "ui": [
                        {"kind": u["kind"], **({"path": u["path"]} if u.get("path") else {})}
                        for u in app["ui"]
                    ],
                    "tools": list(app["tools"]),
                    "models": list(p.get("models") or []),
                }
            )
        return apps


class History:
    def __init__(self, rt: Runtime, cursor_key: bytes, human_clients: frozenset[str]) -> None:
        self.rt, self.cursor_key, self.human_clients = rt, cursor_key, human_clients

    def _actor(self, caller: Caller, row: dict[str, Any]) -> Data:
        pid = row.get("actor_id")
        azp = row.get("client_azp")
        p = None
        if pid is not None and pid != UUID(int=0):
            p = self.rt.store.person(caller.actor, pid)
        if p is None:
            kind = "system"
        else:
            kind = "agent" if azp and azp not in self.human_clients else "human"
        ref = f"person:{p.id}" if p else "system:xt"
        out: Data = {"ref": ref, "kind": kind}
        if p:
            out["name"] = p.name
        return out

    def get_history(self, ctx: CallContext, args: Args) -> Data | None:
        if str(args["ref"]).startswith("explain:"):
            unavailable("get_history view=explain")
        caller = self.rt.caller(ctx)
        view = str(args.get("view", "timeline"))
        if view == "explain":
            unavailable("get_history view=explain")
        model, rid = parse_record_ref(ctx, args["ref"])
        try:
            rec = engine_call(lambda: self.rt.records.get(caller.actor, model, rid))["record"]
        except Exception:
            return None
        events = self.rt.audit.events(caller.actor, rid, limit=1000)
        rows: list[Data] = []
        for e in events:
            diff = e.get("diff") or {}
            new, old = diff.get("new") or {}, diff.get("old") or {}
            row: Data = {
                "at": iso(e["at"]),
                "actor": self._actor(caller, e),
                "action": f"{e['model']}.{_OP_ACTION.get(e['op'], e['op'].lower())}",
                "outcome": "ok",
                "seq": int(e["seq"]),
                "revision": new.get("revision") if isinstance(new.get("revision"), int) else None,
                "from_state": old.get("state") if isinstance(old.get("state"), str) else None,
                "to_state": new.get("state") if isinstance(new.get("state"), str) else None,
            }
            changed = sorted(k for k in new if k not in ("revision", "updated_at", "updated_by"))
            row["summary"] = (
                "Thay đổi: " + ", ".join(changed[:10]) if e["op"] == "UPDATE" else "Tạo bản ghi"
            )
            if e.get("client_azp"):
                row["on_behalf_of"] = None
            rows.append(row)
        if view == "timeline":
            iid = PgWorkflowStore(self.rt.uow).instance_for(f"{model}:{rid}")
            if iid is not None:
                for ev in self.rt.store.process_events(caller.workflow(), iid):
                    rows.append(
                        {
                            "at": iso(ev["created_at"]),
                            "actor": {"ref": "system:xt-workflow", "kind": "system"},
                            "action": f"process.{ev['type']}",
                            "outcome": "failed" if ev["type"] == "engine.error" else "ok",
                            "summary": str(
                                ev["payload"].get("msg") or ev["payload"].get("task") or ev["type"]
                            )[:300],
                        }
                    )
            rows.sort(key=lambda r: r["at"])
        limit = int(args.get("limit", 20))
        page = signed_page(self.cursor_key, ctx, args)
        off = page.offset()
        out: Data = {
            "subject": {"ref": f"{model}:{rid}", "title": str(rec.get("id"))},
            "view": view,
            "events": rows[off : off + limit],
            "next_cursor": page.next(off + limit) if off + limit < len(rows) else None,
        }
        if view == "audit":
            status = self.rt.audit.verify(caller.actor)
            out["chain_verified"] = status.intact
        return out


class Files:
    """upload_file tới ở v0.2 (SeaweedFS + Desk upload): không giả thành công."""

    def plan_upload(self, ctx: CallContext, args: Args) -> MutationPlan:
        unavailable("upload_file")

    def commit(self, ctx: CallContext, plan: MutationPlan) -> CommitResult:
        unavailable("upload_file")
