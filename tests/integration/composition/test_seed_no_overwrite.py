# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""G7 / #25: demo-seed không ghi đè tenant đã có dữ liệu (vd vừa khôi phục từ backup).

`make demo-seed` (e2e/xte2e/seed.py) gọi `xt org import --if-empty` cho từng tenant: tenant đã có dữ
liệu tổ chức thì bỏ qua, không đổi gì; tenant thứ hai (rỗng) vẫn được nạp bên cạnh. Đây là đúng
lệnh quản trị mà seed chạy, trên tenant dùng chung `opsx` (đã có tổ chức + 3 gói).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.integration.composition.fixtures import StackBase, run_admin
from tests.integration.composition.shared_tenants import Ops
from xt.adapters.db.integration import connect

pytestmark = pytest.mark.integration

META = ("person", "org_unit", "agent_grant", "pack_install")


def snapshot(conn: psycopg.Connection[Any], tenant: Any) -> dict[str, tuple[int, str]]:
    """{bảng: (số dòng, md5 các dòng)} của xt_meta (theo tenant) + schema dữ liệu của tenant."""
    schema = f"xt_t{tenant.seq}"
    tables = [("xt_meta", t) for t in META]
    tables += [
        (schema, r[0])
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY 1",
            (schema,),
        )
    ]
    out: dict[str, tuple[int, str]] = {}
    for sch, table in tables:
        query = sql.SQL(
            "SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '')"
            " FROM {}.{} t"
        ).format(sql.Identifier(sch), sql.Identifier(table))
        if sch == "xt_meta":
            row = conn.execute(query + sql.SQL(" WHERE t.tenant_id = %s"), (tenant.id,)).fetchone()
        else:
            row = conn.execute(query).fetchone()
        assert row is not None
        out[f"{sch}.{table}"] = (int(row[0]), str(row[1]))
    return out


def org_file(folder: Path, name: str) -> Path:
    """Tổ chức "mẫu" khác hẳn dữ liệu hiện có (khác tên đơn vị/người, vai trò)."""
    org = folder / f"{name}.json"
    org.write_text(
        json.dumps(
            {
                "units": [{"key": "BGD", "name": f"Đổi tên {name}", "head": "NV-0001"}],
                "people": [
                    {
                        "key": "NV-0001",
                        "name": f"Bị ghi đè {name}",
                        "roles": ["employee"],
                        "unit": "BGD",
                        "subject": f"kc-{name}-NV-0001",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return org


def seed_import(alias: str, org: Path, env: dict[str, str]) -> tuple[int, str]:
    # Đúng lệnh e2e/xte2e/seed.py:import_org chạy khi không có --force.
    return run_admin(
        ["org", "import", "--tenant", alias, str(org), "--if-empty"],
        env,
    )


def create_tenant(env: dict[str, str], name: str) -> str:
    alias = f"seed{uuid.uuid4().hex[:8]}"
    rc, out = run_admin(
        ["tenant", "create", "--alias", alias, "--name", name,
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    return alias


def person_names(env: dict[str, str], alias: str) -> list[str]:
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        rows = conn.execute(
            "SELECT p.name FROM xt_meta.person p JOIN xt_meta.tenant_profile t"
            " ON t.tenant_id = p.tenant_id WHERE t.alias = %s",
            (alias,),
        ).fetchall()
    return [r[0] for r in rows]


def test_seed_skips_populated_tenant_and_seeds_second_alongside(
    ops: Ops, stack_base: StackBase, tmp_path: Path
) -> None:
    env = stack_base.env
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        before = snapshot(conn, ops.tenant)
    assert before["xt_meta.person"][0] > 0 and before["xt_meta.pack_install"][0] > 0

    # 1. Seed chạy trên tenant đã có dữ liệu: bỏ qua, rc 0, không đổi một dòng nào.
    rc, out = seed_import(ops.alias, org_file(tmp_path, "opsx"), env)
    assert rc == 0 and "bỏ qua" in out, out
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        assert snapshot(conn, ops.tenant) == before

    # 2. Tenant thứ hai (rỗng) được nạp bên cạnh mà không đụng tenant thứ nhất.
    alias = create_tenant(env, "Seed second")
    org2 = org_file(tmp_path, "second")
    rc, out = seed_import(alias, org2, env)
    assert rc == 0 and "đã nạp" in out, out
    assert person_names(env, alias) == ["Bị ghi đè second"]
    with connect(env["XT_DB_MIGRATOR_URL"]) as conn:
        assert snapshot(conn, ops.tenant) == before

    # 3. Seed lần nữa trên tenant thứ hai: cũng bỏ qua (idempotent, không đổi).
    rc, out = seed_import(alias, org2, env)
    assert rc == 0 and "bỏ qua" in out, out
    assert person_names(env, alias) == ["Bị ghi đè second"]


def test_import_without_if_empty_still_upserts(stack_base: StackBase, tmp_path: Path) -> None:
    """`--if-empty` là thứ bảo vệ: bỏ cờ (đường --force của seed) thì upsert như cũ."""
    env = stack_base.env
    alias = create_tenant(env, "Seed force")
    org = org_file(tmp_path, "force")
    assert seed_import(alias, org, env)[0] == 0
    org.write_text(org.read_text(encoding="utf-8").replace("Bị ghi đè", "Đã đổi"), encoding="utf-8")
    rc, out = run_admin(["org", "import", "--tenant", alias, str(org)], env)
    assert rc == 0 and "đã nạp" in out, out
    assert person_names(env, alias) == ["Đã đổi force"]
