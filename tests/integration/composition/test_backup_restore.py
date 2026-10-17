# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""deploy/scripts/backup_unified.py trên PostgreSQL thật: backup -> restore vào DB mới -> đối chiếu.

Đã chạy pass trên PostgreSQL 17 (17/10/2026, client 17 qua wrapper docker). Cần `pg_dump`,
`pg_restore`, `psql` cùng hoặc mới hơn server trên PATH; thiếu hoặc cũ hơn thì bỏ qua. Kiểm: tồn kho và sổ biến động của tenant giống hệt sau restore, tệp đối tượng giống
byte, và `xt audit verify` đạt trên database đã khôi phục.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import secrets
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from psycopg import sql

from tests.integration.composition import pgfixture
from tests.integration.composition.shared_tenants import (
    Warehouse,
    balance,
    confirm,
    install_warehouse,
    movements,
)
from xt.application.tools.ports import PendingStatus
from xt.entrypoints import audit_cli
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "backup_unified", ROOT / "deploy/scripts/backup_unified.py"
)
assert spec and spec.loader
bu = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bu)


@pytest.fixture(scope="module")
def warehouse(stack_base, mcp_port, tmp_path_factory):
    """Tenant riêng (`backupit`) để backup/restore độc lập với các test kho khác."""
    alias = "backupit"
    tenant = install_warehouse(stack_base.env, tmp_path_factory, alias)
    prepared = prepare(
        stack_base.env,
        argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full"),
    )
    comp = prepared.services
    try:
        yield Warehouse(comp.runtime, comp.engine, tenant, {}, prepared.dispatcher, alias=alias)
    finally:
        prepared.close()


def client_major(tool: str) -> int:
    out = subprocess.run([tool, "--version"], capture_output=True, text=True, check=True).stdout
    found = re.search(r"(\d+)", out)
    assert found, out
    return int(found.group(1))


def fingerprint(conn: psycopg.Connection[Any], schema: str) -> dict[str, tuple[int, str]]:
    """{bảng: (số dòng, md5 của các dòng đã sắp xếp)} cho mọi bảng trong schema."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY 1",
            (schema,),
        )
    ]
    out: dict[str, tuple[int, str]] = {}
    for table in tables:
        row = conn.execute(
            sql.SQL(
                "SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '')"
                " FROM {}.{} t"
            ).format(sql.Identifier(schema), sql.Identifier(table))
        ).fetchone()
        assert row is not None
        out[table] = (int(row[0]), str(row[1]))
    return out


def test_pg_dump_restore_into_fresh_database_keeps_ledger_and_audit(
    warehouse,
    stock,
    pg,
    tmp_path,
    monkeypatch,
):
    for tool in ("pg_dump", "pg_restore", "psql"):
        if shutil.which(tool) is None:
            pytest.skip(f"cần {tool} trên PATH")
    with pg.su() as su:
        row = su.execute("SHOW server_version_num").fetchone()
    server_major = int(row[0]) // 10000
    for tool in ("pg_dump", "pg_restore"):
        if client_major(tool) < server_major:
            pytest.skip(f"{tool} cũ hơn server PostgreSQL {server_major}")
    w = warehouse
    # 1. Dữ liệu nghiệp vụ thật: một phiếu nhập được duyệt và ghi sổ.
    row = w.request(stock)
    assert confirm(w, row)[0].status is PendingStatus.CONFIRMED
    assert int(balance(w, stock)[0]["quantity_milli"]) == 1000
    assert len(movements(w, row["ref"])) == 1

    # 2. Checkpoint audit ký Ed25519, lưu NGOÀI PostgreSQL (như vận hành thật).
    key = Ed25519PrivateKey.generate()
    private = tmp_path / "audit-sign.pem"
    public = tmp_path / "audit-verify.pem"
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    checkpoints = tmp_path / "checkpoints"
    checkpoints.mkdir()
    audit_env = {
        "XT_DB_APP_URL": pg.app_dsn,
        "XT_AUDIT_CHECKPOINT_DIR": str(checkpoints),
        "XT_AUDIT_SIGNING_KEY_FILE": str(private),
        "XT_AUDIT_SIGNING_KEY_ID": "it-1",
        "XT_AUDIT_VERIFY_KEY_FILE": str(public),
    }
    assert audit_cli.run(["checkpoint"], audit_env) == 0
    assert audit_cli.run(["verify", "--against-checkpoint", "latest"], audit_env) == 0

    # 3. Backup: dump -Fc của database `xt` + một cây tệp đối tượng mẫu.
    objects = tmp_path / "objects"
    (objects / "tenant/ab").mkdir(parents=True)
    blob = secrets.token_bytes(4096)
    (objects / "tenant/ab/file.bin").write_bytes(blob)
    url = f"postgresql://xt_owner:{pg.owner_password}@127.0.0.1:{pgfixture.PORT}/xt"
    monkeypatch.setenv("XT_IT_BACKUP_URL", url)
    archive = bu.backup(tmp_path / "out", pg_url_env="XT_IT_BACKUP_URL", objects_dir=objects)

    # 4. Restore vào database MỚI trong cùng cụm (role xt_app/xt_migrator đã tồn tại).
    fresh = f"xt_restore_{uuid.uuid4().hex[:8]}"
    with pg.su() as su:
        su.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(fresh)))
    monkeypatch.setenv(
        "XT_IT_RESTORE_URL",
        f"postgresql://xt_owner:{pg.owner_password}@127.0.0.1:{pgfixture.PORT}/{fresh}",
    )
    restored_objects = tmp_path / "objects-restored"
    try:
        done = bu.restore(
            archive,
            pg_url_env="XT_IT_RESTORE_URL",
            objects_dir=restored_objects,
        )
        assert sorted(done) == ["objects", "postgres"]
        assert (restored_objects / "tenant/ab/file.bin").read_bytes() == blob

        # 5. Số liệu tenant giống hệt: mọi bảng trong schema tenant (gồm balance, movement).
        schema = f"xt_t{w.tenant.seq}"
        base = f"host=127.0.0.1 port={pgfixture.PORT} user=xt_owner password={pg.owner_password}"
        with (
            psycopg.connect(f"{base} dbname=xt", autocommit=True) as src,
            psycopg.connect(f"{base} dbname={fresh}", autocommit=True) as dst,
        ):
            before, after = fingerprint(src, schema), fingerprint(dst, schema)
        assert before and before == after
        assert after["stock_balance"][0] >= 1 and after["stock_movement"][0] >= 1

        # 6. Chuỗi audit của database đã khôi phục khớp checkpoint đã ký trước backup.
        restored_env = {
            **audit_env,
            "XT_DB_APP_URL": (
                f"host=127.0.0.1 port={pgfixture.PORT} dbname={fresh}"
                f" user=xt_app password={pg.app_password}"
            ),
        }
        assert audit_cli.run(["verify", "--against-checkpoint", "latest"], restored_env) == 0
    finally:
        with pg.su() as su:
            su.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(fresh))
            )
