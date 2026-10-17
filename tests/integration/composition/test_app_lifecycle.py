# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""U15 trên PostgreSQL thật + dispatcher MCP thật: cài kho-hang -> ứng dụng hiện trong
inspect_workspace; `xt pack disable` (kế hoạch + mã xác nhận + người duyệt khác) -> model ẩn, ghi
bị chặn `app_disabled`, dữ liệu còn, admin vẫn đọc, tenant khác không đổi, audit có UPDATE
pack_install; `xt pack enable` -> dùng lại được."""

from __future__ import annotations

import argparse
import re
import uuid
from typing import Any

import pytest

from tests.integration.composition.fixtures import run_admin
from tests.integration.composition.test_warehouse import Warehouse, install_warehouse
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def two(stack_base: Any, mcp_port: int, tmp_path_factory: Any) -> Any:
    env = stack_base.env
    a = install_warehouse(env, tmp_path_factory, "applife")
    b = install_warehouse(env, tmp_path_factory, "applifeother")
    prepared = prepare(
        env, argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    )
    rt, engine, disp = prepared.services.runtime, prepared.services.engine, prepared.dispatcher
    try:
        yield (
            Warehouse(rt, engine, a, {}, disp, alias="applife"),
            Warehouse(rt, engine, b, {}, disp, alias="applifeother"),
            env,
        )
    finally:
        prepared.close()


def lifecycle(env: dict[str, str], cmd: str, alias: str) -> str:
    argv = ["pack", cmd, "--tenant", alias, "kho-hang", "--proposed-by", "agent:test",
            "--approved-by", "person:admin"]  # fmt: skip
    rc, plan = run_admin(argv, env)
    assert rc == 0, plan
    digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
    assert digest, plan
    rc, out = run_admin([*argv, "--confirm", digest.group(1)], env)
    assert rc == 0, out
    return digest.group(1)


def inspect(w: Warehouse, key: str) -> dict[str, Any]:
    out = w.tool(key, "inspect_workspace", {"include": ["models", "apps"]})
    assert out["ok"], out
    return dict(out["data"])


def create_item(w: Warehouse, who: str = "NV-0001") -> dict[str, Any]:
    return w.tool(
        who,
        "create_record",
        {
            "model_id": "stock_item",
            "values": {"sku": uuid.uuid4().hex, "name": "Hàng", "unit": "cái"},
            "idempotency_key": uuid.uuid4().hex,
        },
    )


def test_install_disable_enable_cycle(two: Any, pg: Any) -> None:
    w, other, env = two
    before = inspect(w, "NV-0002")
    apps = {a["id"]: a for a in before["apps"]}
    assert apps["kho-hang"]["group"] == "hang-hoa" and apps["kho-hang"]["status"] == "enabled"
    assert "stock_item" in {m["id"] for m in before["models"]}
    item = w.create("NV-0001", "stock_item", {"sku": uuid.uuid4().hex, "name": "A", "unit": "cái"})

    lifecycle(env, "disable", "applife")
    after = inspect(w, "NV-0002")
    assert "kho-hang" not in {a["id"] for a in after["apps"]}
    assert not {m["id"] for m in after["models"]} & {"stock_item", "stock_request"}
    admin_view = {a["id"]: a["status"] for a in inspect(w, "NV-0001")["apps"]}
    assert admin_view["kho-hang"] == "disabled"
    # NV-0001 qua IAM (có quyền tạo) và NV-0002 (nhân viên, IAM sẽ từ chối): cổng ứng dụng tắt
    # chạy trước IAM nên cả hai cùng nhận capability_unavailable/app_disabled.
    for who in ("NV-0001", "NV-0002"):
        blocked = create_item(w, who)
        assert not blocked["ok"]
        assert blocked["error"]["code"] == "capability_unavailable", (who, blocked)
        assert blocked["error"]["details"]["reason"] == "app_disabled"
    # tenant khác không lộ: nhân viên không có quyền tạo vẫn access_denied
    assert create_item(other, "NV-0002")["error"]["code"] == "access_denied"
    # admin (NV-0001) vẫn đọc qua get_record, dữ liệu còn; người thường bị chặn đọc
    ref = {"ref": f"stock_item:{item['id']}"}
    assert w.tool("NV-0001", "get_record", ref)["ok"]
    denied = w.tool("NV-0002", "get_record", ref)
    assert not denied["ok"] and denied["error"]["details"]["reason"] == "app_disabled"
    procs = w.tool("NV-0002", "inspect_workspace", {"include": ["processes"]})["data"]
    assert "stock_approval" not in {p["id"] for p in procs["processes"]}
    assert create_item(other)["ok"]  # tenant khác không đổi
    assert "kho-hang" in {a["id"] for a in inspect(other, "NV-0002")["apps"]}

    lifecycle(env, "enable", "applife")
    assert create_item(w)["ok"]
    procs = w.tool("NV-0002", "inspect_workspace", {"include": ["processes"]})["data"]
    assert "stock_approval" in {p["id"] for p in procs["processes"]}
    assert "kho-hang" in {a["id"] for a in inspect(w, "NV-0002")["apps"]}

    with pg.su() as su:
        rows = su.execute(
            "SELECT op, diff FROM xt_audit.event WHERE tenant_id = %s"
            " AND model = 'xt_meta.pack_install' ORDER BY seq",
            (w.tenant.id,),
        ).fetchall()
    statuses = [r[1]["new"]["status"] for r in rows if r[0] == "UPDATE"]
    assert statuses[-2:] == ["disabled", "enabled"], rows


def test_lifecycle_sod_on_real_db(two: Any) -> None:
    _, _, env = two
    argv = ["pack", "disable", "--tenant", "applifeother", "kho-hang", "--proposed-by", "a",
            "--approved-by", "a", "--confirm", "sha256:" + "0" * 64]  # fmt: skip
    rc, out = run_admin(argv, env)
    assert rc == 2 and "tách nhiệm vụ" in out
