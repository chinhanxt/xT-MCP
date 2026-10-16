# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit Model Engine vòng 3 (docs/audit-model-engine.md "Vòng 3") trên Postgres 17 thật.

Một container `postgres:17-alpine` (tên ngẫu nhiên, cổng ngẫu nhiên, xóa bằng `docker rm -f -v`),
hai CSDL:
  * `xt`    — cài mới: bootstrap + migrate 0001…mới nhất bằng mã hiện tại.
  * `xt_up` — nâng cấp: bootstrap + migrate + tenant + catalog bằng mã commit 98e1a1b (0001-0004,
              trước I-AUD-META), rồi `install_meta_audit` + migrate bằng mã hiện tại.

Test thường = phòng thủ đứng vững. `xfail(strict=True)` = lỗi ĐÃ tái hiện (gỡ xfail khi sửa).
"""

from __future__ import annotations

import json
import random
import secrets
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from pydantic import Field, TypeAdapter

from tests.unit.model.support import bump, catalog
from tests.unit.model.support import field as fld
from tests.unit.model.support import model as mdl
from xt.adapters.db.audit import (
    AuditReader,
    sign_checkpoint,
    take_checkpoint,
    verify_against_checkpoint,
)
from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.migrations import migrate
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bind_bi_login, bootstrap, install_meta_audit, provision_tenant
from xt.application.model_engine.service import RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import RiskClass
from xt.domain.model.spec import Catalog, check_regex

pytestmark = pytest.mark.integration

IMAGE = "postgres:17-alpine"
OLD_COMMIT = "98e1a1b"
ROOT = Path(__file__).resolve().parents[3]


def emp_model() -> dict[str, Any]:
    return {
        "id": "m2",
        "name": "emp",
        "label": "Nhân viên",
        "fields": [
            {"id": "e1", "name": "code", "type": "text", "label": "Mã", "required": True,
             "pii": "none"},
            {"id": "e2", "name": "salary", "type": "money", "label": "Lương", "pii": "sensitive"},
            {"id": "e4", "name": "note", "type": "text", "label": "Ghi chú", "pii": "none"},
        ],
        "permissions": {"hr": {"actions": ["create", "read", "update", "delete"], "scope": "all"}},
    }  # fmt: skip


# ----------------------------------------------------------------------- hạ tầng ----
@dataclass
class Cluster:
    name: str
    port: int
    su_pw: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    mig_pw: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    app_pw: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    pepper: bytes = field(default_factory=lambda: secrets.token_bytes(32))

    def dsn(self, db: str, user: str, pw: str) -> str:
        return f"host=127.0.0.1 port={self.port} dbname={db} user={user} password={pw}"

    def su(self, db: str) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn(db, "xt_owner", self.su_pw), autocommit=True)

    def migrator(self, db: str) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn(db, "xt_migrator", self.mig_pw), autocommit=True)

    def app(self, db: str) -> psycopg.Connection[Any]:
        return psycopg.connect(self.dsn(db, "xt_app", self.app_pw), autocommit=True)


@dataclass
class Env:
    db: str
    c: Cluster
    app: Database
    migrator: PgMigrator
    records: RecordService
    audit: AuditReader
    old_tenant: TenantRef | None = None

    def su(self) -> psycopg.Connection[Any]:
        return self.c.su(self.db)

    def mig(self) -> psycopg.Connection[Any]:
        return self.c.migrator(self.db)


_OLD_SETUP = r"""
import json, sys, uuid, psycopg
from xt.adapters.db.tenancy import bootstrap, provision_tenant
from xt.adapters.db.migrations import migrate
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.connection import AuditKey
from xt.domain.model.spec import Catalog
import xt.adapters.db.tenancy as T
assert T.__file__.startswith(sys.argv[7]), T.__file__
port, su_pw, mig_pw, app_pw, pepper, spec = sys.argv[1:7]
dsn = f"host=127.0.0.1 port={port} dbname=xt_up"
with psycopg.connect(dsn + f" user=xt_owner password={su_pw}", autocommit=True) as su:
    assert bootstrap(su, migrator_password=mig_pw, app_password=app_pw,
                     pepper=bytes.fromhex(pepper))
mc = lambda: psycopg.connect(dsn + f" user=xt_migrator password={mig_pw}", autocommit=True)
with mc() as m:
    ran = migrate(m)
    t = provision_tenant(m, uuid.uuid4(), "OLD")
mig = PgMigrator(mc, audit_key=AuditKey(1, bytes.fromhex(pepper)))
cat = Catalog.model_validate(json.loads(spec))
p = mig.plan(t, cat, rehearse=True)
mig.apply(t, cat, p.hash, set(), proposed_by="agent:old", approved_by="user:old")
mig.run_jobs(t.seq)
print(json.dumps({"seq": t.seq, "id": str(t.id), "ran": ran}))
"""


def _wait(c: Cluster, db: str = "postgres") -> None:
    deadline = time.monotonic() + 60
    while True:
        try:
            with psycopg.connect(c.dsn(db, "xt_owner", c.su_pw), autocommit=True) as x:
                x.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


def _apply(m: PgMigrator, t: TenantRef, cat: Catalog) -> None:
    p = m.plan(t, cat, rehearse=True)
    assert not p.ids(RiskClass.BLOCKED), p.render_vi()
    m.apply(t, cat, p.hash, set(p.ids(RiskClass.DESTRUCTIVE)), proposed_by="agent:r3",
            approved_by="user:r3")  # fmt: skip


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Cluster, dict[str, Any]]]:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        pytest.fail(f"thiếu image {IMAGE} (kiểm `df -h /` trước khi pull)")
    name = f"xt-audit-r3-model-{secrets.token_hex(4)}"
    c = Cluster(name, 0)
    subprocess.run(
        ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::5432",
         "-e", "POSTGRES_USER=xt_owner", "-e", f"POSTGRES_PASSWORD={c.su_pw}",
         "-e", "POSTGRES_DB=xt", IMAGE, "-c", "deadlock_timeout=1s"],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        out = subprocess.run(
            ["docker", "port", name, "5432/tcp"], check=True, capture_output=True, text=True
        ).stdout
        c.port = int(out.strip().splitlines()[0].rsplit(":", 1)[1])
        _wait(c)
        # 1) CSDL nâng cấp: mã cũ (98e1a1b) trước, khi cụm còn chưa có role mới (xt_job…)
        old = tmp_path_factory.mktemp("old_src")
        arch = subprocess.run(
            ["git", "-C", str(ROOT), "archive", OLD_COMMIT, "src"], check=True, capture_output=True
        ).stdout
        subprocess.run(["tar", "-x", "-C", str(old)], input=arch, check=True)
        with c.su("xt") as su:
            su.execute("CREATE DATABASE xt_up")
        spec = json.dumps(catalog(emp_model()).dump())
        r = subprocess.run(
            [sys.executable, "-c", _OLD_SETUP, str(c.port), c.su_pw, c.mig_pw, c.app_pw,
             c.pepper.hex(), spec, str(old / "src")],
            capture_output=True, text=True,
            env={"PYTHONPATH": str(old / "src"), "PATH": "/usr/bin"},
        )  # fmt: skip
        assert r.returncode == 0, r.stderr[-3000:]
        old_info = json.loads(r.stdout.strip().splitlines()[-1])
        # 2) CSDL cài mới bằng mã hiện tại
        with c.su("xt") as su:
            assert bootstrap(su, migrator_password=c.mig_pw, app_password=c.app_pw, pepper=c.pepper)
        with c.migrator("xt") as m:
            migrate(m)
        # 3) nâng cấp xt_up như deploy/scripts/init_db.py: install_meta_audit + xt_job + migrate
        with c.su("xt_up") as su:
            install_meta_audit(su)
            if not su.execute("SELECT 1 FROM pg_roles WHERE rolname = 'xt_job'").fetchone():
                su.execute("CREATE ROLE xt_job NOLOGIN NOBYPASSRLS")
        with c.migrator("xt_up") as m:
            old_info["upgrade_ran"] = migrate(m)
        yield c, old_info
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)


@pytest.fixture(scope="module")
def envs(cluster: tuple[Cluster, dict[str, Any]]) -> Iterator[dict[str, Env]]:
    c, old = cluster
    key = AuditKey(1, c.pepper)
    out: dict[str, Env] = {}
    for db in ("xt", "xt_up"):
        app = Database(c.dsn(db, "xt_app", c.app_pw), max_size=4, audit_key=key)
        mig = PgMigrator(
            lambda db=db: c.migrator(db), lock_timeout="2s", post_lock_timeout="3s", audit_key=key
        )
        out[db] = Env(db, c, app, mig, RecordService(PgCatalogReader(app), PgRecordRepository(app)),
                      AuditReader(app))  # fmt: skip
    out["xt_up"].old_tenant = TenantRef(int(old["seq"]), uuid.UUID(old["id"]))
    yield out
    for e in out.values():
        e.app.close()


def new_tenant(e: Env) -> TenantRef:
    with e.mig() as m:
        t = provision_tenant(m, uuid.uuid4(), f"R3 {secrets.token_hex(3)}")
    _apply(e.migrator, t, catalog(emp_model()))
    assert e.migrator.run_jobs(t.seq).dead == 0
    return t


def hr(t: TenantRef, uid: int = 7) -> ModelActor:
    return ModelActor(t, uuid.UUID(int=uid), ("hr",))


def q1(c: psycopg.Connection[Any], q: str, *a: Any) -> Any:
    row = c.execute(q, a or None).fetchone()
    return row[0] if row else None


def events_for(e: Env, t: TenantRef, rid: Any) -> list[tuple[Any, ...]]:
    with e.su() as su:
        return su.execute(
            "SELECT op, actor_id, model, diff FROM xt_audit.event WHERE tenant_id = %s"
            " AND record_id = %s ORDER BY seq",
            (t.id, rid),
        ).fetchall()


TARGETS = ["xt:new", "xt_up:new", "xt_up:old"]


def target(envs: dict[str, Env], spec: str) -> tuple[Env, TenantRef]:
    db, kind = spec.split(":")
    e = envs[db]
    if kind == "old":
        assert e.old_tenant is not None
        return e, e.old_tenant
    return e, new_tenant(e)


# ------------------------------------------------- 0. nâng cấp + quyền của migrator ----
def test_upgrade_path_applied_every_migration(
    cluster: tuple[Cluster, dict[str, Any]], envs: dict[str, Env]
) -> None:
    _, old = cluster
    assert old["ran"] == [f for f in old["ran"] if f < "0005"]  # mã cũ chỉ có 0001-0004
    with envs["xt"].su() as a, envs["xt_up"].su() as b:
        fresh = {r[0] for r in a.execute("SELECT name FROM xt_meta.schema_migration")}
        up = {r[0] for r in b.execute("SELECT name FROM xt_meta.schema_migration")}
    assert fresh == up and max(fresh) >= "0013"


@pytest.mark.parametrize("db", ["xt", "xt_up"])
def test_migrator_has_no_audit_write_privilege(envs: dict[str, Env], db: str) -> None:
    with envs[db].su() as su:
        for fn in ("xt_audit.log()", "xt_audit.append(uuid,text,uuid,text,jsonb)",
                   "xt_audit.log_truncate()", "xt_audit.log_meta()",
                   "xt_audit.log_meta_truncate()"):  # fmt: skip
            for role in ("xt_migrator", "xt_app", "xt_job"):
                assert not q1(su, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", role, fn), (
                    role,
                    fn,
                )
        assert not q1(su, "SELECT has_table_privilege('xt_migrator', 'xt_audit.pepper', 'SELECT')")
        owners = su.execute(
            "SELECT DISTINCT pg_get_userbyid(c.relowner) FROM pg_class c"
            " WHERE c.relnamespace = 'xt_audit'::regnamespace AND c.relkind IN ('r', 'S')"
        ).fetchall()
        assert owners == [("xt_audit_owner",)]
        ev = su.execute(
            "SELECT evtname, evtenabled FROM pg_event_trigger WHERE evtname LIKE 'xt_audit%'"
            " ORDER BY 1"
        ).fetchall()
        assert ev == [("xt_audit_ddl_guard", "O"), ("xt_audit_drop_guard", "O")]


# --------------------------------------- 1. tấn công trực tiếp của migrator: bị chặn ----
DIRECT = [
    "ALTER TABLE {s}.emp DISABLE TRIGGER xt_audit",
    "ALTER TABLE {s}.emp DISABLE TRIGGER ALL",
    "ALTER TABLE {s}.emp DISABLE TRIGGER USER",
    "ALTER TABLE {s}.emp ENABLE REPLICA TRIGGER xt_audit",
    "ALTER TABLE {s}.emp ENABLE ALWAYS TRIGGER xt_audit",
    "ALTER TRIGGER xt_audit ON {s}.emp RENAME TO zz",
    "DROP TRIGGER xt_audit ON {s}.emp",
    "DROP TRIGGER xt_audit_truncate ON {s}.emp",
    "CREATE RULE r AS ON INSERT TO {s}.emp DO INSTEAD NOTHING",
    "CREATE RULE r AS ON UPDATE TO {s}.emp DO ALSO NOTHING",
    "CREATE TRIGGER z BEFORE INSERT ON {s}.emp FOR EACH ROW EXECUTE FUNCTION xt.row_touch()",
    "CREATE CONSTRAINT TRIGGER z AFTER INSERT ON {s}.emp FOR EACH ROW"
    " EXECUTE FUNCTION xt.row_touch()",
    "SELECT xt_audit.append(gen_random_uuid(), 'emp', gen_random_uuid(), 'INSERT', '{{}}')",
    "SELECT xt_audit.log()",
    "CREATE TABLE {s}.fake (tenant_id uuid, id uuid);"
    " CREATE TRIGGER xt_audit AFTER INSERT ON {s}.fake FOR EACH ROW"
    " EXECUTE FUNCTION xt_audit.log()",
    "SELECT * FROM xt_audit.pepper",
    "SELECT * FROM xt_audit.head",
    "UPDATE xt_audit.event SET diff = '{{}}'",
    "DELETE FROM xt_audit.event",
    "CREATE OR REPLACE FUNCTION xt_audit.verify_anchor(t uuid, s bigint, h bytea) RETURNS TABLE"
    " (status text, events bigint, first_bad_seq bigint) LANGUAGE sql AS $$ SELECT 'ok', 0::bigint,"
    " NULL::bigint $$",
    "ALTER EVENT TRIGGER xt_audit_ddl_guard DISABLE",
    "DROP EVENT TRIGGER xt_audit_drop_guard",
    "SET session_replication_role = replica",
    "ALTER TABLE xt_meta.decision DISABLE TRIGGER xt_audit_meta",
    "DROP TRIGGER xt_audit_meta ON xt_meta.file_object",
    "CREATE RULE r AS ON INSERT TO xt_meta.pii_egress DO INSTEAD NOTHING",
]


@pytest.mark.parametrize("tgt", TARGETS)
def test_migrator_direct_tampering_denied(envs: dict[str, Env], tgt: str) -> None:
    e, t = target(envs, tgt)
    s = f"xt_t{t.seq}"
    with e.mig() as m:
        for q in DIRECT:
            with pytest.raises(psycopg.Error) as ex, m.transaction():
                m.execute(q.format(s=s))
            assert ex.value.sqlstate in ("42501", "0A000", "P0001"), (
                q,
                ex.value.sqlstate,
                ex.value,
            )
        trig = m.execute(
            "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = %s::regclass"
            " AND NOT tgisinternal ORDER BY 1",
            (f"{s}.emp",),
        ).fetchall()
    assert trig == [("xt_audit", "O"), ("xt_audit_truncate", "O"), ("xt_touch", "O")]


@pytest.mark.parametrize("tgt", TARGETS)
def test_spy_policy_sees_only_hmac_never_key(envs: dict[str, Env], tgt: str) -> None:
    e, t = target(envs, tgt)
    s = f"xt_t{t.seq}"
    with e.mig() as m:
        m.execute("CREATE TABLE IF NOT EXISTS xt_meta.r3_spy (k text, h text)")
        m.execute("GRANT INSERT ON xt_meta.r3_spy TO xt_app")
        m.execute(
            "CREATE OR REPLACE FUNCTION xt.r3_spy() RETURNS boolean LANGUAGE plpgsql"
            " SECURITY DEFINER AS $$ BEGIN INSERT INTO xt_meta.r3_spy VALUES"
            " (current_setting('xt.audit_key', true), current_setting('xt.audit_hmac', true));"
            " RETURN true; END $$"
        )
        m.execute("GRANT EXECUTE ON FUNCTION xt.r3_spy() TO xt_app")
        m.execute(
            f"CREATE POLICY r3_spy ON {s}.emp AS RESTRICTIVE FOR INSERT TO xt_app"
            " WITH CHECK (xt.r3_spy())"
        )
    try:
        e.records.create(hr(t), "emp", {"code": "SPY", "salary": 31000000})
        with e.mig() as m:
            rows = m.execute("SELECT k, h FROM xt_meta.r3_spy").fetchall()
        assert rows
        for k, h in rows:
            assert not k and h and e.c.pepper.hex() not in h and "31000000" not in h
    finally:
        with e.mig() as m:
            m.execute(f"DROP POLICY r3_spy ON {s}.emp")
            m.execute("DROP FUNCTION xt.r3_spy()")
            m.execute("DROP TABLE xt_meta.r3_spy")


# ----------------------- 2. né audit bằng DDL ngoài tầm event trigger (lỗi vòng 3) ----
def _bypass_set_schema(m: psycopg.Connection[Any], s: str, t: TenantRef) -> Any:
    # ddl_guard chỉ xét bảng nằm trong xt_t<seq> lúc lệnh kết thúc: dời bảng ra xt_meta rồi tắt
    with m.transaction():
        m.execute(f"ALTER TABLE {s}.emp SET SCHEMA xt_meta")
        m.execute("ALTER TABLE xt_meta.emp DISABLE TRIGGER xt_audit")
        rid = q1(m, "INSERT INTO xt_meta.emp (tenant_id, code) VALUES (%s, 'BYP1') RETURNING id",
                 t.id)  # fmt: skip
        m.execute("ALTER TABLE xt_meta.emp ENABLE TRIGGER xt_audit")
        m.execute(f"ALTER TABLE xt_meta.emp SET SCHEMA {s}")
    return rid


def _bypass_rename_schema(m: psycopg.Connection[Any], s: str, t: TenantRef) -> Any:
    # ALTER SCHEMA … RENAME không bị kiểm; trigger tắt được giữ nguyên khi đổi tên lại
    with m.transaction():
        m.execute(f"ALTER SCHEMA {s} RENAME TO r3_hidden")
        m.execute("ALTER TABLE r3_hidden.emp DISABLE TRIGGER xt_audit")
        m.execute(f"ALTER SCHEMA r3_hidden RENAME TO {s}")
    state = q1(m, "SELECT tgenabled::text FROM pg_trigger WHERE tgrelid = %s::regclass"
               " AND tgname = 'xt_audit'", f"{s}.emp")  # fmt: skip
    rid = q1(m, f"INSERT INTO {s}.emp (tenant_id, code) VALUES (%s, 'BYP2') RETURNING id", t.id)
    if state == "D":
        m.execute(f"ALTER TABLE {s}.emp ENABLE TRIGGER xt_audit")
    return rid


def _bypass_swap_table(m: psycopg.Connection[Any], s: str, t: TenantRef) -> Any:
    # bảng thay thế không có trigger nào: ddl_guard chỉ cấm trigger lạ, không đòi phải có xt_audit
    with m.transaction():
        m.execute(f"CREATE TABLE {s}.emp_r3 (LIKE {s}.emp INCLUDING ALL)")
        m.execute(f"GRANT SELECT, INSERT, UPDATE ON {s}.emp_r3 TO xt_app")
        m.execute(f"ALTER TABLE {s}.emp RENAME TO emp_r3_orig")
        m.execute(f"ALTER TABLE {s}.emp_r3 RENAME TO emp")
        return q1(m, f"INSERT INTO {s}.emp (tenant_id, code) VALUES (%s, 'BYP3') RETURNING id",
                  t.id)  # fmt: skip


def _bypass_inherit(m: psycopg.Connection[Any], s: str, t: TenantRef) -> Any:
    # bảng con (INHERITS) ngoài schema tenant: dòng hiện qua emp nhưng không kích trigger của emp
    m.execute(f"CREATE TABLE xt_meta.emp_r3_kid () INHERITS ({s}.emp)")
    return q1(m, "INSERT INTO xt_meta.emp_r3_kid (tenant_id, code) VALUES (%s, 'BYP4')"
              " RETURNING id", t.id)  # fmt: skip


BYPASS: dict[str, Callable[[psycopg.Connection[Any], str, TenantRef], Any]] = {
    "set_schema": _bypass_set_schema,
    "rename_schema": _bypass_rename_schema,
    "swap_table": _bypass_swap_table,
    "inherit_child": _bypass_inherit,
}


@pytest.mark.parametrize("how", list(BYPASS))
@pytest.mark.parametrize("tgt", ["xt:new", "xt_up:new"])
def test_r3_01_migrator_write_always_audited(envs: dict[str, Env], tgt: str, how: str) -> None:
    e, t = target(envs, tgt)
    s = f"xt_t{t.seq}"
    rid: Any = None
    try:
        with e.mig() as m:
            try:
                rid = BYPASS[how](m, s, t)
            except psycopg.Error:
                return  # bị chặn: đúng
        # dòng nằm trong model (app đọc được) …
        found = e.records.search(hr(t), "emp", where={"field": "code", "op": "startswith",
                                                       "value": "BYP"})  # fmt: skip
        assert any(r["id"] == str(rid) for r in found["items"]), "dòng phải thấy được qua model"
        # … thì phải có event INSERT
        assert [ev[0] for ev in events_for(e, t, rid)] == ["INSERT"], how
    finally:
        with e.mig() as m:
            m.execute("DROP TABLE IF EXISTS xt_meta.emp_r3_kid")
            if q1(m, "SELECT to_regclass(%s)", f"{s}.emp_r3_orig"):
                m.execute(f"DROP TABLE {s}.emp")
                m.execute(f"ALTER TABLE {s}.emp_r3_orig RENAME TO emp")


@pytest.mark.parametrize("how", ["session_ctx", "replace_actor_fn"])
def test_r3_02_migrator_cannot_forge_audit_actor(envs: dict[str, Env], how: str) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    victim = uuid.UUID(int=0xBAD)
    if how == "session_ctx":
        # migrator là chủ xt_meta.session_ctx: tự ghi ngữ cảnh cho phiên của mình
        with e.mig() as m, m.transaction():
            m.execute(
                "INSERT INTO xt_meta.session_ctx VALUES (pg_backend_pid(), pg_current_xact_id(),"
                " %s, %s, '{hr}', '{}', '{}', %s, 'xt-desk', 'trace-forged')"
                " ON CONFLICT (pid) DO UPDATE SET xid = EXCLUDED.xid,"
                " tenant_id = EXCLUDED.tenant_id,"
                " actor_id = EXCLUDED.actor_id, effective_actor = EXCLUDED.effective_actor,"
                " client_azp = EXCLUDED.client_azp, trace_id = EXCLUDED.trace_id",
                (t.id, victim, victim),
            )
            rid = q1(m, f"INSERT INTO {s}.emp (tenant_id, code) VALUES (%s, 'F1') RETURNING id",
                     t.id)  # fmt: skip
        ev = events_for(e, t, rid)
        assert ev and ev[0][1] is None, f"event của migrator mang actor giả {ev[0][1]}"
        return
    # migrator là chủ xt.actor()/xt.cur(): thay thân hàm → mọi event (cả của xt_app) mang actor giả
    with e.mig() as m:
        orig = q1(m, "SELECT pg_get_functiondef('xt.actor()'::regprocedure)")
        try:
            m.execute(
                "CREATE OR REPLACE FUNCTION xt.actor() RETURNS uuid LANGUAGE sql STABLE AS"
                f" $$ SELECT '{victim}'::uuid $$"
            )
        except psycopg.Error:
            return  # bị chặn: đúng
    try:
        rec = e.records.create(hr(t), "emp", {"code": "F2"})["record"]
        ev = events_for(e, t, uuid.UUID(rec["id"]))
        assert ev and ev[0][1] == uuid.UUID(int=7), f"event của xt_app mang actor {ev[0][1]}"
    finally:
        with e.mig() as m:
            m.execute(orig)


def test_r3_03_install_cannot_drop_sensitive_columns(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    with e.mig() as m:
        try:
            m.execute("SELECT xt_audit.install(%s, 'emp', '{}')", (s,))
        except psycopg.Error:
            return  # bị chặn: đúng
    try:
        rec = e.records.create(hr(t), "emp", {"code": "P1", "salary": 31000000})["record"]
        assert "31000000" not in str(events_for(e, t, uuid.UUID(rec["id"])))
    finally:
        with e.mig() as m:
            m.execute("SELECT xt_audit.install(%s, 'emp', '{salary}')", (s,))


def test_r3_04_migrator_cannot_forge_event_for_real_model(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    real = e.records.create(hr(t), "emp", {"code": "REAL", "salary": 10})["record"]
    rid = uuid.UUID(real["id"])
    with e.mig() as m:
        try:
            with m.transaction():
                m.execute(f"ALTER TABLE {s}.emp RENAME TO emp_r3_real")
                m.execute(f"CREATE TABLE {s}.emp (tenant_id uuid, id uuid, code text)")
                m.execute(f"GRANT TRIGGER ON {s}.emp TO xt_audit_owner")
                m.execute("SELECT xt_audit.install(%s, 'emp', '{}')", (s,))
                m.execute(f"INSERT INTO {s}.emp VALUES (%s, %s, 'FORGED')", (t.id, rid))
                m.execute(f"DELETE FROM {s}.emp")
                m.execute(f"DROP TABLE {s}.emp")
                m.execute(f"ALTER TABLE {s}.emp_r3_real RENAME TO emp")
        except psycopg.Error:
            return  # bị chặn: đúng
    ops = [(ev[0], ev[2]) for ev in events_for(e, t, rid)]
    assert ops == [("INSERT", "emp")], f"lịch sử bản ghi thật bị chèn event giả: {ops}"


# ------------------------------------------------------- 3. neo checkpoint ngoài DB ----
def _tail_delete(su: psycopg.Connection[Any], t: TenantRef) -> None:
    with su.transaction():
        su.execute("ALTER TABLE xt_audit.event DISABLE TRIGGER no_update_delete")
        last = q1(su, "SELECT max(seq) FROM xt_audit.event WHERE tenant_id = %s", t.id)
        su.execute("DELETE FROM xt_audit.event WHERE seq = %s", (last,))
        su.execute(
            "UPDATE xt_audit.head h SET seq = e.seq, hash = e.hash FROM (SELECT seq, hash FROM"
            " xt_audit.event WHERE tenant_id = %s ORDER BY seq DESC LIMIT 1) e"
            " WHERE h.tenant_id = %s",
            (t.id, t.id),
        )
        su.execute("ALTER TABLE xt_audit.event ENABLE TRIGGER no_update_delete")


def _checkpoint(e: Env) -> dict[str, Any]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with e.c.app(e.db) as c:
        return sign_checkpoint(take_checkpoint(c), Ed25519PrivateKey.generate(), "r3")


def _status(e: Env, doc: dict[str, Any], t: TenantRef) -> str:
    with e.c.app(e.db) as c:
        res = verify_against_checkpoint(c, doc)
    return next(r.status for r in res if r.tenant_id == str(t.id))


def test_checkpoint_detects_superuser_tail_deletion(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    for i in range(3):
        e.records.create(hr(t), "emp", {"code": f"CP{i}"})
    doc = _checkpoint(e)
    assert _status(e, doc, t) == "ok"
    with e.su() as su:
        _tail_delete(su, t)
        assert su.execute("SELECT * FROM xt_audit.verify(%s)", (t.id,)).fetchone()[1] is None
    assert _status(e, doc, t) == "missing"
    e.records.create(hr(t), "emp", {"code": "CP-after"})  # ghi tiếp không che được
    assert _status(e, doc, t) == "missing"


def test_r3_05_checkpoint_verification_not_trusting_db_functions(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    for i in range(2):
        e.records.create(hr(t), "emp", {"code": f"CQ{i}"})
    doc = _checkpoint(e)
    with e.su() as su:
        orig = q1(su, "SELECT pg_get_functiondef('xt_audit.verify_anchor(uuid,bigint,bytea)'"
                  "::regprocedure)")  # fmt: skip
        _tail_delete(su, t)
        su.execute(
            "CREATE OR REPLACE FUNCTION xt_audit.verify_anchor(t uuid, s bigint, h bytea)"
            " RETURNS TABLE (status text, events bigint, first_bad_seq bigint) LANGUAGE sql"
            " STABLE SECURITY DEFINER AS $$ SELECT 'ok'::text, 0::bigint, NULL::bigint $$"
        )
    try:
        assert _status(e, doc, t) != "ok", "superuser xóa đuôi + thay hàm kiểm: checkpoint vẫn ok"
    finally:
        with e.su() as su:
            su.execute(orig)


# ------------------------------------------------------------------- 4. job pha 2 ----
def test_phase2_backend_killed_mid_concurrent_build_then_reconcile(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    e.records.create(hr(t), "emp", {"code": "K0"})
    new = bump(
        e.migrator.current(t),
        lambda d: mdl(d, "emp")["fields"].append(
            {"id": "e8", "name": "badge", "type": "text", "label": "Thẻ", "unique": True,
             "pii": "none"}),
    )  # fmt: skip
    _apply(e.migrator, t, new)
    # giữ RowExclusiveLock để CREATE INDEX CONCURRENTLY phải chờ, rồi giết backend của job
    hold = e.c.app(e.db)
    hold.execute("BEGIN")
    hold.execute("SELECT xt.set_context(%s, %s, '{hr}', '{}', '{}', NULL, NULL, NULL)",
                 (t.id, uuid.UUID(int=7)))  # fmt: skip
    hold.execute(f"INSERT INTO {s}.emp (code) VALUES ('HOLD')")
    err: list[BaseException] = []

    def run() -> None:
        try:
            e.migrator.run_jobs(t.seq)
        except BaseException as x:
            err.append(x)

    th = threading.Thread(target=run)
    th.start()
    killed = False
    with e.su() as su:
        for _ in range(100):
            pid = q1(su, "SELECT pid FROM pg_stat_activity WHERE query ILIKE"
                     " 'CREATE UNIQUE INDEX CONCURRENTLY%%' AND state = 'active'")  # fmt: skip
            if pid:
                su.execute("SELECT pg_terminate_backend(%s)", (pid,))
                killed = True
                break
            time.sleep(0.1)
    th.join(30)
    hold.execute("ROLLBACK")
    hold.close()
    assert killed and err, "job phải bị giết giữa chừng"
    with e.su() as su:
        st = su.execute("SELECT status FROM xt_meta.apply_job WHERE tenant_seq = %s AND"
                        " phase = 'concurrent'", (t.seq,)).fetchall()  # fmt: skip
        inv = q1(su, "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
                 " WHERE c.relnamespace = %s::regnamespace AND NOT i.indisvalid", s)  # fmt: skip
    assert st == [("pending",)] and inv == 1  # job vẫn chờ, index INVALID còn đó
    nxt = bump(e.migrator.current(t), lambda d: fld(d, "emp", "note").update(label="GC k"))
    p = e.migrator.plan(t, nxt, rehearse=True)
    with pytest.raises(ModelError) as ex:
        e.migrator.apply(t, nxt, p.hash, set(), proposed_by="a", approved_by="b")
    assert ex.value.details["reason"] == "post_pending"
    assert e.migrator.reconcile()[t.seq] == "pending"
    stats = e.migrator.run_jobs(t.seq)
    assert stats.done == 1 and stats.dead == stats.failed == 0
    assert e.migrator.reconcile()[t.seq] == "validated"
    with e.su() as su:
        assert q1(su, "SELECT bool_and(i.indisvalid) FROM pg_index i JOIN pg_class c ON"
                  " c.oid = i.indexrelid WHERE c.relnamespace = %s::regnamespace", s)  # fmt: skip
    e.migrator.apply(t, nxt, p.hash, set(), proposed_by="a", approved_by="b")


def test_phase2_dead_job_blocks_until_data_fixed(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    new = bump(
        e.migrator.current(t),
        lambda d: mdl(d, "emp")["fields"].append(
            {"id": "e7", "name": "badge", "type": "text", "label": "Thẻ", "unique": True,
             "pii": "none"}),
    )  # fmt: skip
    _apply(e.migrator, t, new)
    # trùng dữ liệu chen vào trước khi job CONCURRENTLY chạy → job chết sau max_attempts
    e.records.create(hr(t), "emp", {"code": "D1", "badge": "DUP"})
    e.records.create(hr(t), "emp", {"code": "D2", "badge": "DUP"})
    for _ in range(3):
        e.migrator.run_jobs(t.seq)
    assert e.migrator.reconcile()[t.seq] == "failed"
    nxt = bump(e.migrator.current(t), lambda d: fld(d, "emp", "note").update(label="GC d"))
    p = e.migrator.plan(t, nxt, rehearse=True)
    with pytest.raises(ModelError):
        e.migrator.apply(t, nxt, p.hash, set(), proposed_by="a", approved_by="b")
    with e.su() as su:
        assert not q1(su, "SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid ="
                      " i.indexrelid WHERE c.relnamespace = %s::regnamespace AND NOT"
                      " i.indisvalid", f"xt_t{t.seq}")  # fmt: skip
    found = e.records.search(hr(t), "emp", where={"field": "badge", "op": "=", "value": "DUP"})
    one = found["items"][0]
    e.records.update(hr(t), "emp", uuid.UUID(one["id"]), int(one["revision"]), {"badge": "DUP2"})
    assert e.migrator.retry_failed(t).dead == 0
    assert e.migrator.reconcile()[t.seq] == "validated"
    e.migrator.apply(t, nxt, p.hash, set(), proposed_by="a", approved_by="b")


def test_phase2_four_parallel_workers_run_each_job_exactly_once(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    ts = [new_tenant(e) for _ in range(2)]
    for t in ts:
        for i in range(50):
            e.records.create(hr(t), "emp", {"code": f"P{i}", "note": f"n{i}"})
        new = bump(
            e.migrator.current(t),
            lambda d: (
                mdl(d, "emp")
                .setdefault("indexes", [])
                .extend(
                    [{"id": "i8", "fields": ["code", "note"]}, {"id": "i9", "fields": ["note"]}]
                )
            ),
        )
        _apply(e.migrator, t, new)
    workers = [
        PgMigrator(lambda: e.mig(), post_lock_timeout="5s", audit_key=AuditKey(1, e.c.pepper))
        for _ in range(4)
    ]
    barrier = threading.Barrier(4)
    results: list[Any] = []
    errs: list[BaseException] = []

    def go(w: PgMigrator) -> None:
        barrier.wait()
        try:
            results.append(w.run_jobs(None))
        except BaseException as x:
            errs.append(x)

    ths = [threading.Thread(target=go, args=(w,)) for w in workers]
    for th in ths:
        th.start()
    for th in ths:
        th.join(60)
    assert not errs, errs
    with e.su() as su:
        rows = su.execute(
            "SELECT tenant_seq, status, attempts FROM xt_meta.apply_job WHERE tenant_seq = ANY(%s)",
            ([t.seq for t in ts],),
        ).fetchall()
        valid = su.execute(
            "SELECT count(*), bool_and(i.indisvalid) FROM pg_index i JOIN pg_class c ON"
            " c.oid = i.indexrelid WHERE c.relname IN ('ix_m2_i8', 'ix_m2_i9')"
            " AND c.relnamespace::regnamespace::text = ANY(%s)",
            ([f"xt_t{t.seq}" for t in ts],),
        ).fetchone()
    mine = [r for r in rows if r[1] != "done" or r[2] != 1]
    assert not mine, f"job chạy != 1 lần hoặc chưa xong: {mine}"
    assert sum(r.dead + r.failed for r in results) == 0
    assert valid == (4, True)


# ------------------------------------------------------- 5. tập con regex Rust ↔ PG ----
_TOK = ["a", "b", "A", "0", "9", "-", "^", "$", "|", "(", ")", "(?:", "[", "]", "[^", "*", "+",
        "?", "{2}", "{1,3}", "{0,}", "{", "}", ",", "\\.", "\\-", "\\n", "\\t", "\\[", "\\]",
        "\\^", "\\$", "\\/", "\\{", "\\}", "\\|", " ", "é", "/", "#", "_", "z-a", "a-z",
        "\\\\"]  # fmt: skip
_SAMPLES = ["", "a", "b", "ab", "aa", "ba", "A", "0", "9", "-", "^", "$", "a\n", "\n", "\t", "[",
            "]", "é", "a-b", "{2}", "/", "#", "_", "\\", " ", "aaa", "ab0", "a|b", "{", "}", ",",
            "a{2}", "0-9", "Z", "ä", "\r"]  # fmt: skip


def test_regex_subset_agrees_with_postgres(envs: dict[str, Env]) -> None:
    rnd = random.Random(20261016)
    seen: set[str] = set()
    bad: list[tuple[str, str, Any, Any]] = []
    accepted = 0
    with envs["xt"].su() as su:
        for _ in range(30000):
            p = "".join(rnd.choice(_TOK) for _ in range(rnd.randint(1, 7)))
            if p in seen:
                continue
            seen.add(p)
            try:
                check_regex(p)
            except ValueError:
                continue
            accepted += 1
            ta = TypeAdapter(Annotated[str, Field(pattern=p)])
            rust = []
            for s in _SAMPLES:
                try:
                    ta.validate_python(s)
                    rust.append(True)
                except Exception:
                    rust.append(False)
            try:
                with su.transaction():
                    pg = su.execute(
                        "SELECT array_agg(s ~ %s ORDER BY i) FROM unnest(%s::text[])"
                        " WITH ORDINALITY u(s, i)",
                        (p, _SAMPLES),
                    ).fetchone()[0]
            except psycopg.Error as x:
                bad.append((p, "pg_error", str(x).splitlines()[0], None))
                continue
            for s, r_, g in zip(_SAMPLES, rust, pg, strict=True):
                if r_ != g:
                    bad.append((p, repr(s), r_, g))
    assert accepted > 500
    sem = [b for b in bad if b[1] != "pg_error"]
    print(f"\nREGEX accepted={accepted} pg_error={len(bad) - len(sem)} semantic={len(sem)}")
    for b in sem[:40]:
        print("SEM", b)
    for b in [b for b in bad if b[1] == "pg_error"][:15]:
        print("ERR", b)
    assert not sem, f"{len(sem)} pattern khớp khác nhau Rust ↔ PG, vd {sem[:8]}"


PG_REJECTS = ["^[A-Z]{2}{2}$", "^a$+", "^{2}a", "#*{0,}", "a+{1,3}"]


def test_r3_07_accepted_regex_compiles_in_postgres(envs: dict[str, Env]) -> None:
    ok = [p for p in PG_REJECTS if _accepts(p)]
    with envs["xt"].su() as su:
        bad = []
        for p in ok:
            try:
                with su.transaction():
                    su.execute("SELECT 'x' ~ %s", (p,))
            except psycopg.Error:
                bad.append(p)
    assert not bad, f"check_regex nhận nhưng PG từ chối: {bad}"


def _accepts(p: str) -> bool:
    try:
        check_regex(p)
        return True
    except ValueError:
        return False


def test_pg_rejected_regex_is_blocked_at_rehearsal_not_500(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    try:
        # sau sửa R3-07: bị chặn ngay ở validate spec (check_regex), trước cả diễn tập
        new = bump(
            e.migrator.current(t), lambda d: fld(d, "emp", "code").update(regex="^[A-Z]{2}{2}$")
        )
    except ValueError as x:
        assert "lượng từ" in str(x)
        return
    try:
        p = e.migrator.plan(t, new, rehearse=True)
    except ModelError as x:
        assert x.code in ("invalid_request", "validation_error", "precondition_failed"), x.code
        return
    assert p.ids(RiskClass.BLOCKED), "regex PG không biên dịch được phải bị chặn ở diễn tập"


# -------------------------------------------- 6. audit metadata: bảng của 0007-0015 ----
# Bảng xt_meta KHÔNG cần vào hash-chain (dữ liệu tạm, phái sinh, hoặc đã có bản ghi audit khác).
EXCLUDED_OK = {
    "platform", "schema_migration", "session_ctx", "pgrst_client",
    "apply_job",              # hàng đợi pha 2 (catalog_version là nguồn)
    "model_proposal",         # đề xuất bất biến; duyệt/áp nằm ở model_change_request (audit)
    "pii_token", "pii_token_source", "pii_token_seen",  # vault/ánh xạ token; lộ ra = pii_egress
    "pii_reveal_ticket",      # vé dùng một lần; lần xem ghi pii_egress (audit)
    "workflow_timer_outbox", "workflow_timer_fire",     # sổ idempotency hẹn giờ
    "notification_request", "notification_delivery", "notification_source",  # sổ gửi/idempotency
    "telegram_link_code", "telegram_update",            # mã liên kết dùng một lần / chống lặp
    "confirmation_binding", "confirmation_session", "single_use_key",  # nonce xác nhận, hết hạn
    "workflow_instance", "workflow_request",  # trạng thái engine; chuyển trạng thái: process_event
}  # fmt: skip


@pytest.mark.parametrize("db", ["xt", "xt_up"])
def test_meta_audit_inventory(envs: dict[str, Env], db: str) -> None:
    with envs[db].su() as su:
        rows = su.execute(
            "SELECT c.relname, EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = c.oid"
            " AND g.tgname = 'xt_audit_meta' AND g.tgenabled = 'O'),"
            " EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid"
            " AND a.attname = 'tenant_id' AND NOT a.attisdropped)"
            " FROM pg_class c WHERE c.relnamespace = 'xt_meta'::regnamespace AND c.relkind = 'r'"
            " ORDER BY 1"
        ).fetchall()
    audited = {r[0] for r in rows if r[1]}
    gaps = sorted(r[0] for r in rows if not r[1] and r[0] not in EXCLUDED_OK)
    for must in ("decision", "file_object", "file_event", "notification", "telegram_link",
                 "iam_policy", "pack_install", "pii_egress", "model_change_request"):  # fmt: skip
        assert must in audited, (db, must)
    # Vòng 3: các bảng dưới đây mang quyền hoặc trạng thái nghiệp vụ nhưng không vào hash-chain
    print(
        "\nINVENTORY",
        db,
        "audited=",
        sorted(audited),
        "gaps=",
        gaps,
        "notenant=",
        sorted(r[0] for r in rows if not r[2]),
    )
    # Sau sửa R3-06: không còn bảng mang quyền/trạng thái nào ngoài hash-chain
    assert gaps == [], (db, gaps)


# Ghi nhận ở báo cáo vòng 3 (R3-06): mang quyền/chính sách, trước đây không vào hash-chain. Test
# bên dưới khẳng định nay đã có audit.
KNOWN_GAPS = {"agent_grant", "catalog_version", "login_tenant", "org_unit", "person", "tenant",
              "tenant_profile"}  # fmt: skip


@pytest.mark.parametrize("db", ["xt", "xt_up"])
def test_r3_06_permission_and_catalog_tables_audited(envs: dict[str, Env], db: str) -> None:
    with envs[db].su() as su:
        missing = [
            t for t in sorted(KNOWN_GAPS)
            if not q1(su, "SELECT count(*) FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid"
                      " WHERE c.relnamespace = 'xt_meta'::regnamespace AND c.relname = %s"
                      " AND g.tgname LIKE 'xt_audit%%'", t)
        ]  # fmt: skip
    assert not missing, missing


# ---------------------------------------------------- 7. login BI + set_context ----
@pytest.fixture(scope="module")
def bi(envs: dict[str, Env]) -> Iterator[tuple[str, str, TenantRef, TenantRef]]:
    e = envs["xt"]
    a, b = new_tenant(e), new_tenant(e)
    e.records.create(hr(a), "emp", {"code": "BI-A", "salary": 5})
    e.records.create(hr(b), "emp", {"code": "BI-B", "salary": 6})
    login, pw = f"bi_r3_{secrets.token_hex(3)}", secrets.token_urlsafe(16)
    with e.su() as su:
        su.execute(f"CREATE ROLE {login} LOGIN PASSWORD '{pw}'")
    with e.mig() as m:
        bind_bi_login(m, login, a, roles=("hr",))
    yield login, pw, a, b
    with e.mig() as m:  # quyền CONNECT do migrator cấp (grantor) → migrator thu hồi
        m.execute(f"REVOKE ALL ON DATABASE xt FROM {login}")
    with e.su() as su:
        su.execute(f"DROP OWNED BY {login}")
        su.execute(f"DELETE FROM xt_meta.login_tenant WHERE login = '{login}'")
        su.execute(f"DROP ROLE {login}")


def test_bi_login_cannot_escape_bound_context(
    envs: dict[str, Env], bi: tuple[str, str, TenantRef, TenantRef]
) -> None:
    e = envs["xt"]
    login, pw, a, b = bi
    with psycopg.connect(e.c.dsn("xt", login, pw), autocommit=True) as c:
        assert q1(c, f"SELECT count(*) FROM api_t{a.seq}.emp") == 1
        for q in (
            "SELECT xt.set_context({}, NULL, '{{admin}}', '{{}}', '{{}}', NULL, NULL, NULL)".format(
                f"'{b.id}'"
            ),
            f'SELECT xt.set_context(\'{{"t": "{b.id}", "r": ["admin"]}}\'::jsonb)',
            "INSERT INTO xt_meta.session_ctx VALUES (pg_backend_pid(), pg_current_xact_id(),"
            f" '{b.id}', NULL, '{{admin}}', '{{}}', '{{}}', NULL, NULL, NULL)",
            "SELECT * FROM xt.backend_cur()",
            "SELECT * FROM xt_meta.login_tenant",
            f"SELECT count(*) FROM api_t{b.seq}.emp",
            f"SELECT count(*) FROM xt_t{a.seq}.emp",
            "SELECT count(*) FROM xt_audit.event",
            "SET ROLE xt_app",
            "SET ROLE xt_authenticator",
            "SET SESSION AUTHORIZATION xt_app",
            "CREATE TEMP TABLE z (x int)",
            "CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        ):
            with pytest.raises(psycopg.Error) as ex, c.transaction():
                c.execute(q)
            assert ex.value.sqlstate in ("42501", "42704", "3F000", "42P01"), (q, ex.value)
        for guc in ("xt.tenant_id", "xt.pgrst_ctx", "xt.roles", "xt.actor_id"):
            with c.transaction(force_rollback=True):
                val = (json.dumps({"t": str(b.id), "r": ["admin"]}) if guc == "xt.pgrst_ctx"
                       else str(b.id))  # fmt: skip
                c.execute("SELECT set_config(%s, %s, true)", (guc, val))
                assert q1(c, "SELECT xt.tenant()") == a.id
                assert q1(c, "SELECT xt.has_role('admin')") is False
                assert q1(c, f"SELECT count(*) FROM api_t{a.seq}.emp") == 1
    with pytest.raises(psycopg.OperationalError):  # CSDL khác: không có CONNECT
        psycopg.connect(e.c.dsn("xt_up", login, pw)).close()


def test_app_context_does_not_leak_across_transactions_or_roles(
    envs: dict[str, Env], bi: tuple[str, str, TenantRef, TenantRef]
) -> None:
    e = envs["xt"]
    _, _, a, b = bi
    with e.c.app("xt") as c:
        with c.transaction():
            c.execute("SELECT xt.set_context(%s, %s, '{hr}', '{}', '{}', NULL, NULL, NULL)",
                      (a.id, uuid.UUID(int=7)))  # fmt: skip
            assert q1(c, f"SELECT count(*) FROM xt_t{a.seq}.emp") == 1
            assert q1(c, f"SELECT count(*) FROM xt_t{b.seq}.emp") == 0
        assert q1(c, "SELECT xt.tenant()") is None  # tx kế: hết ngữ cảnh
        with c.transaction():
            c.execute("SELECT xt.set_context(%s, %s, '{hr}', '{}', '{}', NULL, NULL, NULL)",
                      (a.id, uuid.UUID(int=7)))  # fmt: skip
            c.execute("SET LOCAL ROLE xt_workflow")
            assert q1(c, f"SELECT count(*) FROM xt_t{b.seq}.emp") == 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                c.execute("SELECT xt.set_context(%s, NULL, '{hr}', '{}', '{}', NULL, NULL, NULL)",
                          (b.id,))  # fmt: skip
