# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hạ tầng test process: org từ seed của pack, service giả, đồng hồ giả cho timer,
nạp lại kiểu JSONB.

Dữ liệu tổ chức nằm ở `tests/fixtures/process/org/` (bản chụp seed của research/packs-draft);
engine không hard-code dữ liệu nào.
"""

from __future__ import annotations

import copy
import json
import pathlib
import random
from collections.abc import Callable, Iterator
from datetime import datetime as _RealDT
from datetime import timedelta
from typing import Any

import pytest
import SpiffWorkflow.bpmn.specs.event_definitions.timer as spiff_timer

from xt.adapters.workflow.bpmn.spiff import SpiffProcessEngine
from xt.application.process.memory import InMemoryOrgDirectory
from xt.application.process.runtime import ProcessInstance, ProcessRuntime

FIX = pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "process"
PACKS = FIX / "packs"
PACK_NAMES = [
    "de_xuat_phe_duyet", "giao_viec_thuong", "giao_viec_khan",
    "ban_tin_tuan", "nv_onboarding", "nv_offboarding",
]  # fmt: skip

# allowlist op lấy đúng tên/tham số từ pack.json của research/packs-draft
OPS: dict[str, list[str]] = {
    "post_to_ledger": ["amount", "requester"], "notify": ["msg"],
    "check_overdue_advances": ["requester"], "close_task": ["task_id"],
    "weekly_rollup": ["from_date", "to_date", "department"],
    "create_lifecycle_tasks": ["case_id", "kind"], "activate_employee": ["employee_id"],
    "check_unreturned_assets": ["employee_id"], "close_lifecycle_case": ["case_id"],
}  # fmt: skip

ENGINE = SpiffProcessEngine()


def load_pack(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((PACKS / f"{name}.json").read_text())
    return data


def org_from_seed(
    users_doc: dict[str, Any], employees_doc: dict[str, Any] | None = None
) -> InMemoryOrgDirectory:
    """users_doc: seed 00_users.json (departments[key, head], users[key, department, roles]);
    employees_doc: 02_nhan_vien.json (records[_key, manager='@employee:NV-x', state])."""
    users = {
        u["key"]: {"active": True, "department": u.get("department"),
                   "roles": list(u.get("roles", [])), "manager": None}
        for u in users_doc["users"]
    }  # fmt: skip
    for r in (employees_doc or {}).get("records", []):
        if r["_key"] not in users:
            continue
        if r.get("manager"):
            users[r["_key"]]["manager"] = r["manager"].split(":", 1)[1]
        if r.get("state") in ("resigned", "terminated", "left"):
            users[r["_key"]]["active"] = False
    depts = {d["key"]: {"head": d.get("head")} for d in users_doc["departments"]}
    return InMemoryOrgDirectory(users, depts)


def seed_org() -> InMemoryOrgDirectory:
    return org_from_seed(
        json.loads((FIX / "org" / "org_users.json").read_text()),
        json.loads((FIX / "org" / "org_nhan_vien.json").read_text()),
    )


class Services(dict[str, Callable[..., Any]]):
    def __init__(self) -> None:
        super().__init__()
        self.ledger: list[tuple[str, int]] = []
        self.notes: list[str] = []
        self.calls: list[Any] = []
        self.overdue: dict[str, int] = {}
        self.service_keys: list[str] = []
        self.unreturned = 0

        def post_to_ledger(amount: int, requester: str, idempotency_key: str) -> dict[str, Any]:
            self.service_keys.append(idempotency_key)
            self.ledger.append((requester, amount))
            return {"so": f"PC-{len(self.ledger):04d}"}

        def notify(msg: str) -> dict[str, Any]:
            self.notes.append(msg)
            return {"ok": 1}

        def simple(name: str, **ret: Any) -> Callable[..., Any]:
            def f(**kw: Any) -> dict[str, Any]:
                self.calls.append((name, kw))
                return dict(ret)

            return f

        def check_overdue(requester: str) -> dict[str, Any]:
            self.calls.append(("check_overdue_advances", requester))
            return {"overdue": self.overdue.get(requester, 0)}

        def weekly_rollup(from_date: str, to_date: str, department: str) -> dict[str, Any]:
            self.calls.append(("weekly_rollup", from_date, to_date))
            return {"code": f"BT-{to_date}", "summary": f"Tuần {from_date}..{to_date}", "id": "x"}

        self.update(
            {
                "post_to_ledger": post_to_ledger,
                "notify": notify,
                "check_overdue_advances": check_overdue,
                "close_task": simple("close_task", closed=True),
                "weekly_rollup": weekly_rollup,
                "create_lifecycle_tasks": simple("create_lifecycle_tasks", task_count=4),
                "activate_employee": simple("activate_employee", state="probation"),
                "check_unreturned_assets": lambda employee_id: {
                    "unreturned": self.unreturned,
                    "active_grants": 0,
                },
                "close_lifecycle_case": simple("close_lifecycle_case", closed=True),
            }
        )


class FakeDT(_RealDT):
    offset = timedelta(0)

    @classmethod
    def now(cls, tz: Any = None) -> Any:
        return _RealDT.now(tz) + cls.offset


class Clock:
    def advance(self, **kw: float) -> None:
        FakeDT.offset += timedelta(**kw)

    def set(self, dt: _RealDT) -> None:
        FakeDT.offset = dt - _RealDT.now(dt.tzinfo)


def clock_fixture(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    """Đồng hồ giả cho timer SLA/timer start: thay `datetime` trong module timer của SpiffWorkflow
    (monkeypatch trong test, không sửa thư viện)."""
    FakeDT.offset = timedelta(0)
    monkeypatch.setattr(spiff_timer, "datetime", FakeDT)
    yield Clock()
    FakeDT.offset = timedelta(0)


def make_runtime(
    spec: str | dict[str, Any],
    services: Services,
    org: InMemoryOrgDirectory,
    *,
    admin_role: str | None = "admin",
    events: list[dict[str, Any]] | None = None,
) -> ProcessRuntime:
    c = ENGINE.compile(load_pack(spec) if isinstance(spec, str) else spec, OPS)
    return ProcessRuntime(
        c, ENGINE, services, org, admin_role=admin_role,
        on_event=events.append if events is not None else None,
    )  # fmt: skip


def jsonb_roundtrip(state: dict[str, Any], seed: int = 0) -> dict[str, Any]:
    """Mô phỏng JSONB: thứ tự key bị đổi (sắp xếp + xáo trộn tasks)."""
    s = json.loads(json.dumps(state, sort_keys=True))
    items = list(s["wf"]["tasks"].items())
    random.Random(seed).shuffle(items)
    s["wf"]["tasks"] = dict(items)
    return copy.deepcopy(s)


def only(inst: ProcessInstance, user: str | None = None) -> dict[str, Any]:
    box = inst.inbox(user)
    assert len(box) == 1, box
    return box[0]
