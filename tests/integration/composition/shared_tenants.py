# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Shared tenant fixtures, registered once in tests/integration/conftest.py.

Tenant runtimes live for the session; stock stays fresh for each test.
Import helpers here, but request fixtures by name instead of importing them.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.integration.composition.fixtures import PACK, ROOT, StackBase, run_admin
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT
from xt.adapters.db.integration import connect, person_id, tenant_of_alias
from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.domain.identity import PrincipalKind
from xt.domain.model.context import TenantRef
from xt.entrypoints.composition.process import ProcessContext
from xt.entrypoints.composition.runtime import Caller, Runtime
from xt.entrypoints.serve import prepare

ALIAS = "opsx"
PACKS = [PACK, ROOT / "packages" / "giao-viec-sla", ROOT / "packages" / "nhan-su-vong-doi"]
DEPTS = [("BGD", "Ban Giám đốc"), ("NS", "Nhân sự"), ("IT", "Công nghệ"), ("KT", "Kế toán"),
         ("KD", "Kinh doanh")]  # fmt: skip
# key, tên, phòng, quản lý, vai trò
PEOPLE = [
    ("NV-0001", "Giám Đốc", "BGD", None, ["director", "admin"]),
    ("NV-0002", "Trưởng Nhân Sự", "NS", "NV-0001", ["dept_head"]),
    ("NV-0003", "Chuyên Viên Nhân Sự", "NS", "NV-0002", ["hr"]),
    ("NV-0004", "Trưởng IT", "IT", "NV-0001", ["dept_head"]),
    ("NV-0005", "Quản Trị IT", "IT", "NV-0004", ["it_admin"]),
    ("NV-0006", "Trưởng Kế Toán", "KT", "NV-0001", ["dept_head"]),
    ("NV-0007", "Kế Toán Thanh Toán", "KT", "NV-0006", ["payment_accountant"]),
    ("NV-0008", "Kế Toán Trưởng", "KT", "NV-0006", ["chief_accountant"]),
    ("NV-0009", "Trưởng Kinh Doanh", "KD", "NV-0001", ["dept_head"]),
    ("NV-0010", "Nhân Viên Kinh Doanh", "KD", "NV-0009", []),
    ("NV-0011", "Nhân Viên Mới", "KD", "NV-0009", []),
    ("NV-0012", "Nhân Viên Nghỉ", "KD", "NV-0009", []),
    ("NV-0013", "Nhân Viên Mới Hai", "KD", "NV-0009", []),
]


def subject(key: str) -> str:
    return f"kc-{ALIAS}-{key}"


@dataclass
class Ops:
    rt: Runtime
    engine: Any
    tenant: TenantRef
    plans: dict[str, str]
    dispatcher: Any = None
    alias: str = ALIAS

    def sub(self, key: str) -> str:
        return f"kc-{self.alias}-{key}"

    def agent(self, key: str) -> Actor:
        return Actor(self.alias, self.sub(key), PrincipalKind.AGENT, AGENT_CLIENT, self.sub(key))

    def tool(self, key: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Agent (it-agent, trần L1) đại diện người `key` gọi tool qua dispatcher thật của MCP."""
        ctx = CallContext(
            self.agent(key), ClientClass.CLOUD, f"it-{uuid.uuid4().hex[:8]}", datetime.now(UTC)
        )
        out: dict[str, Any] = self.dispatcher.call(name, args, ctx).envelope
        return out

    def confirm(self, pending: dict[str, Any], by: str, agent_of: str) -> Any:
        """Người `by` bấm xác nhận trên Desk (sau step-up) cho thao tác agent của `agent_of` tạo:
        đúng đường ConfirmationService của trang /desk/confirm."""
        from xt.application.tools import ConfirmationService

        assert pending["error"]["code"] == "confirmation_required", pending
        cid = pending["error"]["details"]["confirmation_id"]
        actor, trace = self.agent(agent_of), f"it-{uuid.uuid4().hex[:8]}"
        svc = ConfirmationService(self.dispatcher, DESK_CLIENT)
        return self.rt.run_as(
            self.rt.resolve(actor, trace),
            actor,
            lambda: svc.confirm(cid, self.sub(by), datetime.now(UTC), trace),
        )

    def complete(
        self, key: str, ref: str, to_state: str, key_id: str, **extra: Any
    ) -> dict[str, Any]:
        """transition_record của bước người do agent của `key` gửi (revision đọc từ inbox)."""
        item = self.inbox_item(key, ref)
        args = {"ref": ref, "to_state": to_state, "expected_revision": item["revision"],
                "idempotency_key": key_id, **extra}  # fmt: skip
        return self.tool(key, "transition_record", args)

    def inbox_item(self, key: str, ref: str) -> dict[str, Any]:
        out = self.tool(key, "get_inbox", {"kinds": ["task", "approval"], "limit": 200})
        assert out["ok"], out
        found = [i for i in out["data"]["items"] if i["ref"] == ref]
        assert found, (key, ref, out["data"]["items"])
        return dict(found[0])

    def pid(self, key: str) -> str:
        return str(person_id(self.tenant.id, key))

    def caller(self, key: str) -> tuple[Caller, Actor]:
        actor = Actor(ALIAS, subject(key), PrincipalKind.HUMAN, DESK_CLIENT, None)
        return self.rt.resolve(actor, f"it-{uuid.uuid4().hex[:8]}"), actor

    def run(self, key: str, fn: Callable[[Caller, ProcessContext], Any]) -> Any:
        caller, actor = self.caller(key)
        pc = ProcessContext(self.rt, self.engine, caller.workflow(), caller.profile.timezone)
        return self.rt.run_as(caller, actor, lambda: fn(caller, pc))

    def create(self, key: str, model: str, values: dict[str, Any]) -> dict[str, Any]:
        out = self.run(key, lambda c, _: self.rt.records.create(c.actor, model, values))
        return dict(out["record"])

    def get(self, model: str, rid: Any) -> dict[str, Any]:
        out = self.run(
            "NV-0001", lambda _, pc: self.rt.records.get(pc.actor, model, uuid.UUID(str(rid)))
        )
        return dict(out["record"])

    def search(self, model: str, where: dict[str, Any]) -> list[dict[str, Any]]:
        out = self.run(
            "NV-0001",
            lambda _, pc: self.rt.records.search(pc.actor, model, where, ("created_at",), 100, 0),
        )
        return list(out["items"])

    def start(self, pid: str, data: dict[str, Any], requester: str) -> tuple[str, dict[str, Any]]:
        iid = str(uuid.uuid4())
        out = self.run(
            requester,
            lambda _, pc: pc.service().start(pid, data, self.pid(requester), instance_id=iid),
        )
        return iid, out

    def act(self, iid: str, step: str, who: str, outcome: str = "done") -> dict[str, Any]:
        def body(_: Caller, pc: ProcessContext) -> dict[str, Any]:
            svc = pc.service()
            info = svc.get(iid)
            task = next(t for t in info["tasks"] if t["step"] == step)
            assert self.pid(who) in task["candidates"], (step, task["candidates"])
            return svc.act(iid, task["task_id"], self.pid(who), outcome, f"{step} xong")

        out: dict[str, Any] = self.run(who, body)
        return out

    def steps(self, summary: dict[str, Any]) -> set[str]:
        return {t["step"] for t in summary["tasks"]}


@pytest.fixture(scope="session")
def ops(
    stack_base: StackBase, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Ops]:
    env = stack_base.env
    d = tmp_path_factory.mktemp("xt-ops")
    rc, out = run_admin(
        ["tenant", "create", "--alias", ALIAS, "--name", "Pack ops (X)",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    org = {
        "departments": [{"id": k, "name": n, "parent_id": None} for k, n in DEPTS],
        "positions": [],
        "employees": [
            {"id": k, "full_name": n, "email": f"{k.lower()}@ops.example", "department_id": u,
             "position_id": None, "manager_id": m, "active": True, "roles": r}
            for k, n, u, m, r in PEOPLE
        ],
    }  # fmt: skip
    (d / "org.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    (d / "links.json").write_text(
        json.dumps(
            {
                "subjects": {k: subject(k) for k, *_ in PEOPLE},
                "agent_grants": [
                    {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
                    for k, *_ in PEOPLE
                ],
            }
        ),
        encoding="utf-8",
    )
    rc, out = run_admin(
        ["org", "import", "--tenant", ALIAS, str(d / "org.json"), "--links",
         str(d / "links.json"), "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    plans: dict[str, str] = {}
    for pack in PACKS:
        common = ["pack", "install", "--tenant", ALIAS, str(pack)]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, done = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, done
        plans[pack.name] = plan
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        tenant = tenant_of_alias(c, ALIAS)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    comp: Any = prepared.services
    try:
        yield Ops(comp.runtime, comp.engine, tenant, plans, prepared.dispatcher)
    finally:
        prepared.close()


class Warehouse(Ops):
    def agent(self, key: str) -> Actor:
        # The UI is a human OIDC client; decide still requires actual confirmation.
        return Actor(self.alias, self.sub(key), PrincipalKind.HUMAN, DESK_CLIENT, None)

    def caller(self, key: str) -> tuple[Caller, Actor]:
        actor = self.agent(key)
        return self.rt.resolve(actor, f"wh-{uuid.uuid4().hex}"), actor

    def request(self, values: dict[str, Any]) -> dict[str, Any]:
        result = self.tool(
            "NV-0002",
            "create_record",
            {
                "model_id": "stock_request",
                "values": {"code": uuid.uuid4().hex, **values},
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert result["ok"], result
        row = result["data"]["record"]
        result = self.tool(
            "NV-0002",
            "submit_request",
            {
                "ref": row["ref"],
                "expected_revision": row["revision"],
                "idempotency_key": uuid.uuid4().hex,
            },
        )
        assert result["ok"], result
        request: dict[str, Any] = result["data"]["request"]
        return request

    def approval(
        self, row: dict[str, Any], decision: str = "approve"
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        args = {
            "ref": row["ref"],
            "expected_revision": row["revision"],
            "decision": decision,
            "reason": "Warehouse review",
            "idempotency_key": uuid.uuid4().hex,
        }
        pending = self.tool("NV-0001", "decide_request", args)
        assert not pending["ok"] and pending["error"]["code"] == "confirmation_required", pending
        return pending, args


def install_warehouse(
    env: dict[str, str], tmp_path_factory: pytest.TempPathFactory, alias: str
) -> TenantRef:
    folder = tmp_path_factory.mktemp("warehouse-org")
    rc, out = run_admin(
        [
            "tenant",
            "create",
            "--alias",
            alias,
            "--name",
            "Warehouse integration",
            "--timezone",
            "Asia/Ho_Chi_Minh",
            "--llm-egress",
            "masked",
        ],
        env,
    )
    assert rc == 0, out
    people = [("NV-0001", ["warehouse_manager", "admin"]), ("NV-0002", [])]
    org = {
        "departments": [{"id": "WH", "name": "Kho", "parent_id": None}],
        "positions": [],
        "employees": [
            {
                "id": key,
                "full_name": key,
                "email": f"{key.lower()}@warehouse.example",
                "department_id": "WH",
                "position_id": None,
                "manager_id": None,
                "active": True,
                "roles": roles,
            }
            for key, roles in people
        ],
    }
    (folder / "org.json").write_text(json.dumps(org))
    (folder / "links.json").write_text(
        json.dumps(
            {"subjects": {key: f"kc-{alias}-{key}" for key, _ in people}, "agent_grants": []}
        )
    )
    rc, out = run_admin(
        [
            "org",
            "import",
            "--tenant",
            alias,
            str(folder / "org.json"),
            "--links",
            str(folder / "links.json"),
            "--base-roles",
            "employee",
        ],
        env,
    )
    assert rc == 0, out
    common = [
        "pack",
        "install",
        "--tenant",
        alias,
        str(ROOT / "packages/kho-hang"),
        "--proposed-by",
        "agent:test",
        "--approved-by",
        "person:admin",
    ]
    rc, plan = run_admin(common, env)
    assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
    digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
    assert digest, plan
    rc, out = run_admin([*common, "--confirm", digest.group(1)], env)
    assert rc == 0, out
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        tenant = tenant_of_alias(conn, alias)
    return tenant


@pytest.fixture(scope="session")
def warehouse(
    stack_base: StackBase, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Warehouse]:
    env = stack_base.env
    alias = "warehouseit"
    tenant = install_warehouse(env, tmp_path_factory, alias)
    prepared = prepare(
        env, argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    )
    comp: Any = prepared.services
    try:
        yield Warehouse(comp.runtime, comp.engine, tenant, {}, prepared.dispatcher, alias=alias)
    finally:
        prepared.close()


@pytest.fixture(scope="session")
def other_warehouse(
    warehouse: Warehouse, stack_base: StackBase, tmp_path_factory: pytest.TempPathFactory
) -> Warehouse:
    alias = "warehouseother"
    tenant = install_warehouse(stack_base.env, tmp_path_factory, alias)
    return Warehouse(warehouse.rt, warehouse.engine, tenant, {}, warehouse.dispatcher, alias=alias)


@pytest.fixture
def stock(warehouse: Warehouse) -> dict[str, Any]:
    wh = warehouse.create("NV-0001", "warehouse", {"code": uuid.uuid4().hex, "name": "Kho"})
    loc = warehouse.create(
        "NV-0001",
        "warehouse_location",
        {"code": uuid.uuid4().hex, "name": "Kệ", "warehouse": wh["id"]},
    )
    item = warehouse.create(
        "NV-0001", "stock_item", {"sku": uuid.uuid4().hex, "name": "Hàng", "unit": "cái"}
    )
    return {
        "warehouse": wh["id"],
        "kind": "receive",
        "lines": [{"item": item["id"], "location": loc["id"], "quantity_milli": 1000}],
    }


def movements(w: Warehouse, ref: str) -> list[dict[str, Any]]:
    return w.search("stock_movement", {"field": "request", "op": "=", "value": ref.split(":")[1]})


def balance(w: Warehouse, stock: dict[str, Any]) -> list[dict[str, Any]]:
    line = stock["lines"][0]
    return w.search(
        "stock_balance", {"field": "key", "op": "=", "value": f"{line['item']}:{line['location']}"}
    )


def confirm(
    w: Warehouse, row: dict[str, Any], decision: str = "approve"
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    pending, args = w.approval(row, decision)
    result = w.confirm(pending, "NV-0001", "NV-0001")
    return result, pending, args
