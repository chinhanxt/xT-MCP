# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""I-AUD-META: audit cho bảng metadata xt_meta.* dưới xt_audit_owner, trên Postgres 17 thật.

Một container (`xt-audit-meta-pg`, cổng 20323), hai CSDL: `xt` (cài mới: bootstrap + migrate) và
`xt_old` (mô phỏng CSDL bootstrap trước khi có audit_meta.sql, nâng cấp bằng install_meta_audit).
"""

from __future__ import annotations

import secrets
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import psycopg.sql
import pytest
from psycopg.types.json import Jsonb

from xt.adapters.db.migrations import migrate
from xt.adapters.db.tenancy import (
    bootstrap,
    bootstrap_sql,
    install_meta_audit,
    provision_tenant,
)

pytestmark = pytest.mark.integration

IMAGE = "postgres:17-alpine"
CONTAINER = "xt-audit-meta-pg"
PORT = 20323
META_TABLES = (
    "iam_policy",
    "process_definition",
    "pack_install",
    "model_binding",
    "process_event",
    "pii_egress",
    "model_change_request",
    "tool_operation",
    "file_object",  # 0009_files: metadata tệp (tên redact) cũng vào hash-chain
    "file_event",
    "decision",  # 0010_decision_sod
    "notification",  # 0014_notify: tiêu đề redact
    "telegram_link",  # 0014_notify: telegram_user_id/chat_id redact
)


@dataclass(frozen=True)
class Pg:
    su_pw: str
    mig_pw: str
    app_pw: str
    pepper: bytes

    def su(self, db: str = "xt") -> psycopg.Connection[Any]:
        return psycopg.connect(
            f"host=localhost port={PORT} dbname={db} user=xt_owner password={self.su_pw}",
            autocommit=True,
        )

    def migrator(self, db: str = "xt") -> psycopg.Connection[Any]:
        return psycopg.connect(
            f"host=localhost port={PORT} dbname={db} user=xt_migrator password={self.mig_pw}",
            autocommit=True,
        )


@pytest.fixture(scope="module")
def pg() -> Iterator[Pg]:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        pytest.fail(f"thiếu image {IMAGE}")
    p = Pg(*(secrets.token_urlsafe(16) for _ in range(3)), secrets.token_bytes(32))
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER, "-p", f"{PORT}:5432",
         "-e", "POSTGRES_USER=xt_owner", "-e", f"POSTGRES_PASSWORD={p.su_pw}",
         "-e", "POSTGRES_DB=xt", IMAGE],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                with p.su() as c:
                    c.execute("SELECT 1")
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        with p.su() as su:
            assert bootstrap(su, migrator_password=p.mig_pw, app_password=p.app_pw, pepper=p.pepper)
        with p.migrator() as m:
            migrate(m)
        yield p
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)


def q1(c: psycopg.Connection[Any], q: str, *a: Any) -> Any:
    row = c.execute(q, a or None).fetchone()
    return row[0] if row else None


def _tenant(pg: Pg, db: str = "xt") -> uuid.UUID:
    with pg.migrator(db) as m:
        return provision_tenant(m, uuid.uuid4(), f"T {secrets.token_hex(3)}").id


def _events(pg: Pg, tenant: uuid.UUID, db: str = "xt") -> list[tuple[Any, ...]]:
    with pg.su(db) as su:
        return su.execute(
            "SELECT seq, model, op, diff FROM xt_audit.event WHERE tenant_id = %s ORDER BY seq",
            (tenant,),
        ).fetchall()


def test_fresh_install_attaches_meta_audit_to_all_tables(pg: Pg) -> None:
    with pg.su() as su:
        rows = su.execute(
            "SELECT c.relname, g.tgname, g.tgenabled::text FROM pg_trigger g"
            " JOIN pg_class c ON c.oid = g.tgrelid WHERE c.relnamespace = 'xt_meta'::regnamespace"
            " AND g.tgname LIKE 'xt_audit_meta%' ORDER BY 1, 2"
        ).fetchall()
    present = {r[0] for r in rows}
    with pg.su() as su:
        existing = {t for t in META_TABLES if q1(su, "SELECT to_regclass(%s)", f"xt_meta.{t}")}
    assert existing and present == existing
    assert all(r[2] == "O" for r in rows) and len(rows) == 2 * len(existing)


def test_meta_writes_enter_tenant_chain_with_redaction(pg: Pg) -> None:
    t = _tenant(pg)
    with pg.migrator() as m:
        m.execute(
            "INSERT INTO xt_meta.iam_policy (tenant_id, revision, document, digest, proposed_by,"
            " approved_by) VALUES (%s, 1, %s, 'd1', 'agent:a', 'user:b')",
            (t, Jsonb({"statements": []})),
        )
        m.execute("UPDATE xt_meta.iam_policy SET digest = 'd2' WHERE tenant_id = %s", (t,))
        m.execute("UPDATE xt_meta.iam_policy SET digest = 'd2' WHERE tenant_id = %s", (t,))
        if q1(m, "SELECT to_regclass('xt_meta.tool_operation')"):
            cols = {r[0] for r in m.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'xt_meta'"
                " AND table_name = 'tool_operation' AND is_nullable = 'NO'"
                " AND column_default IS NULL"
            )}  # fmt: skip
            assert {"tenant_id", "actor_key", "tool", "idempotency_key", "fingerprint"} <= cols
            m.execute(
                "INSERT INTO xt_meta.tool_operation (tenant_id, actor_key, tool, idempotency_key,"
                " fingerprint, state, receipt) VALUES (%s, 'u:1', 'create_record', 'k-0001',"
                " 'fp', 'done', %s)",
                (t, Jsonb({"salary": 31000000, "name": "Nguyễn Văn A"})),
            )
    ev = _events(pg, t)
    models = [(e[1], e[2]) for e in ev]
    assert ("xt_meta.iam_policy", "INSERT") in models and ("xt_meta.iam_policy", "UPDATE") in models
    assert models.count(("xt_meta.iam_policy", "UPDATE")) == 1  # UPDATE không đổi gì: không event
    upd = next(e[3] for e in ev if e[1] == "xt_meta.iam_policy" and e[2] == "UPDATE")
    assert upd == {"old": {"digest": "d1"}, "new": {"digest": "d2"}}
    tool = [e[3] for e in ev if e[1] == "xt_meta.tool_operation"]
    if tool:
        assert tool[0]["new"]["receipt"].startswith("redacted:sha256:")
        assert "31000000" not in str(tool) and "Nguyễn" not in str(tool)
    with pg.su() as su:
        assert su.execute("SELECT * FROM xt_audit.verify(%s)", (t,)).fetchone() == (len(ev), None)


def test_migrator_cannot_disable_drop_or_bypass_meta_audit(pg: Pg) -> None:
    t = _tenant(pg)
    with pg.migrator() as m:
        for q in (
            "ALTER TABLE xt_meta.iam_policy DISABLE TRIGGER xt_audit_meta",
            "ALTER TABLE xt_meta.iam_policy DISABLE TRIGGER ALL",
            "DROP TRIGGER xt_audit_meta ON xt_meta.iam_policy",
            "DROP TRIGGER xt_audit_meta_truncate ON xt_meta.iam_policy",
            "CREATE RULE r AS ON INSERT TO xt_meta.iam_policy DO INSTEAD NOTHING",
            "CREATE TRIGGER x AFTER INSERT ON xt_meta.platform FOR EACH ROW"
            " EXECUTE FUNCTION xt_audit.log_meta('id')",
            "SELECT xt_audit.install_meta('tenant')",  # tenant không có cột tenant_id
        ):
            with pytest.raises(psycopg.Error) as ex, m.transaction():
                m.execute(q)
            assert isinstance(
                ex.value, psycopg.errors.InsufficientPrivilege | psycopg.errors.RaiseException
            ), (q, ex.value)
        # TRUNCATE để lại dấu vết ở chuỗi nền tảng
        m.execute("TRUNCATE xt_meta.pii_egress")
    zero = uuid.UUID(int=0)
    assert any(e[2] == "TRUNCATE" and e[1] == "xt_meta.pii_egress" for e in _events(pg, zero))
    with pg.migrator() as m:  # vẫn ghi được, và vẫn vào chuỗi
        m.execute(
            "INSERT INTO xt_meta.pack_install (tenant_id, pack, version, digest, proposed_by,"
            " approved_by) VALUES (%s, 'p', '1', 'd', 'a', 'b')",
            (t,),
        )
    assert [e[1] for e in _events(pg, t)] == ["xt_meta.pack_install"]


def test_checkpoint_anchor_covers_meta_chain(pg: Pg) -> None:
    t = _tenant(pg)
    with pg.migrator() as m:
        for i in range(3):
            m.execute(
                "INSERT INTO xt_meta.pii_egress (tenant_id, trace_id, tool, client_class, mode,"
                " fields) VALUES (%s, %s, 'get_record', 'cloud', 'masked', '{phone}')",
                (t, f"tr{i}"),
            )
    with pg.su() as su:
        seq, h = su.execute(
            "SELECT seq, hash FROM xt_audit.head WHERE tenant_id = %s", (t,)
        ).fetchone()
        assert q1(su, "SELECT status FROM xt_audit.verify_anchor(%s, %s, %s)", t, seq, h) == "ok"
        su.execute("ALTER TABLE xt_audit.event DISABLE TRIGGER no_update_delete")
        su.execute("DELETE FROM xt_audit.event WHERE seq = %s", (seq,))
        su.execute("ALTER TABLE xt_audit.event ENABLE TRIGGER no_update_delete")
        assert su.execute("SELECT * FROM xt_audit.verify(%s)", (t,)).fetchone()[1] is None
        assert (
            q1(su, "SELECT status FROM xt_audit.verify_anchor(%s, %s, %s)", t, seq, h) == "missing"
        )


def test_drop_tenant_cascade_still_audited(pg: Pg) -> None:
    from xt.adapters.db.tenancy import drop_tenant
    from xt.domain.model.context import TenantRef

    with pg.migrator() as m:
        ref = provision_tenant(m, uuid.uuid4(), "Xóa")
        m.execute(
            "INSERT INTO xt_meta.pack_install (tenant_id, pack, version, digest, proposed_by,"
            " approved_by) VALUES (%s, 'p', '1', 'd', 'a', 'b')",
            (ref.id,),
        )
        drop_tenant(m, TenantRef(ref.seq, ref.id))
    ops = [(e[1], e[2]) for e in _events(pg, ref.id)]
    assert ops == [("xt_meta.pack_install", "INSERT"), ("xt_meta.pack_install", "DELETE")]


def test_existing_db_upgrade_path(pg: Pg) -> None:
    """CSDL bootstrap trước khi có audit_meta.sql: migration 0006 báo rõ cần install_meta_audit;
    sau khi superuser chạy install_meta_audit thì migrate thành công và trigger được gắn."""
    with pg.su() as su:
        su.execute("DROP DATABASE IF EXISTS xt_old")
        su.execute("CREATE DATABASE xt_old")
    with pg.su("xt_old") as su, su.transaction():
        su.execute(bootstrap_sql().encode())  # bản cũ: không có audit_meta.sql
        su.execute(
            psycopg.sql.SQL("ALTER ROLE xt_migrator PASSWORD {}").format(
                psycopg.sql.Literal(pg.mig_pw)
            )
        )
    with pg.migrator("xt_old") as m:
        with pytest.raises(psycopg.errors.RaiseException, match="install_meta_audit"):
            migrate(m)
        assert q1(m, "SELECT count(*) FROM xt_meta.schema_migration WHERE name LIKE '0006%'") == 0
    with pg.su("xt_old") as su:
        install_meta_audit(su)
        install_meta_audit(su)  # idempotent
    with pg.migrator("xt_old") as m:
        assert "0006_audit_meta.sql" in migrate(m)
        n = q1(
            m,
            "SELECT count(*) FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid"
            " WHERE c.relnamespace = 'xt_meta'::regnamespace AND g.tgname = 'xt_audit_meta'",
        )
        assert n >= 6
    t = _tenant(pg, "xt_old")
    with pg.migrator("xt_old") as m:
        m.execute(
            "INSERT INTO xt_meta.pack_install (tenant_id, pack, version, digest, proposed_by,"
            " approved_by) VALUES (%s, 'p', '1', 'd', 'a', 'b')",
            (t,),
        )
    assert [e[1] for e in _events(pg, t, "xt_old")] == ["xt_meta.pack_install"]
