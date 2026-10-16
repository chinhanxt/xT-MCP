# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Regression của audit model engine trên Postgres 17 thật (docs/audit-model-engine.md).

Test `xfail(strict=True)` = lỗi ĐÃ tái hiện, gỡ xfail khi sửa. Test thường = phòng thủ đã đứng
vững khi tấn công (giữ để không thoái lui). Container riêng `xt-audit-model-pg`, cổng 20303.
"""

from __future__ import annotations

import secrets
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest

from tests.unit.model.support import bump, catalog, customer, field, model
from xt.adapters.db.audit import AuditReader
from xt.adapters.db.catalog import PgCatalogReader, PgProposalStore
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bootstrap, provision_tenant
from xt.application.model_engine.service import ModelService, RecordService
from xt.contracts.model import ProposeModelChange
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import RiskClass
from xt.domain.model.spec import Catalog

pytestmark = pytest.mark.integration

IMAGE = "postgres:17-alpine"
CONTAINER = "xt-audit-model-pg"
PORT = 20303
DSN = f"host=localhost port={PORT} dbname=xt"


@dataclass(frozen=True)
class Pg:
    su_pw: str
    mig_pw: str
    app_pw: str
    pepper: bytes

    def su(self) -> psycopg.Connection[Any]:
        return psycopg.connect(f"{DSN} user=xt_owner password={self.su_pw}", autocommit=True)

    def migrator(self) -> psycopg.Connection[Any]:
        return psycopg.connect(f"{DSN} user=xt_migrator password={self.mig_pw}", autocommit=True)

    def app(self) -> psycopg.Connection[Any]:
        return psycopg.connect(f"{DSN} user=xt_app password={self.app_pw}", autocommit=True)


@dataclass
class Env:
    pg: Pg
    app: Database
    mig_db: Database
    migrator: PgMigrator
    records: RecordService
    models: ModelService
    audit: AuditReader
    a: TenantRef
    b: TenantRef


def emp_model() -> dict[str, Any]:
    return {
        "id": "m2",
        "name": "emp",
        "label": "Nhân viên",
        "fields": [
            {"id": "e1", "name": "code", "type": "text", "label": "Mã", "required": True,
             "pii": "none"},
            {"id": "e2", "name": "salary", "type": "money", "label": "Lương", "pii": "sensitive"},
            {"id": "e3", "name": "state", "type": "text", "label": "Trạng thái", "pii": "none",
             "managed_by": "workflow", "default": "draft", "required": True},
            {"id": "e4", "name": "note", "type": "text", "label": "Ghi chú", "pii": "none"},
        ],
        "permissions": {
            "hr": {"actions": ["create", "read", "update", "delete"], "scope": "all"},
        },
    }  # fmt: skip


def _start() -> Pg:
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        pytest.fail(f"thiếu image {IMAGE} (kiểm `df -h /` trước khi pull)")
    pg = Pg(*(secrets.token_urlsafe(16) for _ in range(3)), secrets.token_bytes(32))
    subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", CONTAINER, "-p", f"{PORT}:5432",
         "-e", "POSTGRES_USER=xt_owner", "-e", f"POSTGRES_PASSWORD={pg.su_pw}",
         "-e", "POSTGRES_DB=xt", IMAGE, "-c", "deadlock_timeout=1s"],
        check=True, capture_output=True,
    )  # fmt: skip
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with pg.su() as c:
                c.execute("SELECT 1")
            return pg
        except psycopg.OperationalError:
            time.sleep(0.5)
    raise RuntimeError("Postgres không lên trong 60 s")


def do_apply(e: Env, t: TenantRef, cat: Catalog) -> None:
    p = e.migrator.plan(t, cat, rehearse=True)
    assert not p.ids(RiskClass.BLOCKED), p.render_vi()
    e.migrator.apply(t, cat, p.hash, set(p.ids(RiskClass.DESTRUCTIVE)),
                     proposed_by="agent:audit", approved_by="user:audit")  # fmt: skip


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    pg = _start()
    try:
        with pg.su() as su:
            bootstrap(su, migrator_password=pg.mig_pw, app_password=pg.app_pw, pepper=pg.pepper)
        key = AuditKey(1, pg.pepper)
        app = Database(f"{DSN} user=xt_app password={pg.app_pw}", max_size=4, audit_key=key)
        mig_db = Database(f"{DSN} user=xt_migrator password={pg.mig_pw}", max_size=2)
        migrator = PgMigrator(pg.migrator, lock_timeout="1s", post_lock_timeout="2s", audit_key=key)
        cats = PgCatalogReader(app)
        with pg.migrator() as m:
            a = provision_tenant(m, uuid.uuid4(), "A")
            b = provision_tenant(m, uuid.uuid4(), "B")
        e = Env(pg, app, mig_db, migrator, RecordService(cats, PgRecordRepository(app)),
                ModelService(cats, migrator, PgProposalStore(app, mig_db)), AuditReader(app),
                a, b)  # fmt: skip
        for t in (a, b):
            do_apply(e, t, catalog(customer(), emp_model()))
            assert migrator.run_jobs(t.seq).dead == 0
        yield e
        app.close()
        mig_db.close()
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True)


def hr(t: TenantRef, uid: int = 7) -> ModelActor:
    return ModelActor(t, uuid.UUID(int=uid), ("hr",))


def ctx(c: psycopg.Connection[Any], t: TenantRef, roles: str = "hr") -> None:
    # Ngữ cảnh RBAC chỉ đặt được qua xt.set_context (quyết định sau AUD-13); GUC không còn tác dụng.
    c.execute(
        "SELECT xt.set_context(%s, %s, %s::text[], '{}', '{}', NULL, NULL, NULL)",
        (t.id, uuid.UUID(int=7), roles.split(",")),
    )


def audit_seq(e: Env) -> int:
    with e.pg.su() as su:
        row = su.execute(
            "SELECT coalesce(last_value, 0) FROM pg_sequences WHERE schemaname = 'xt_audit'"
            " AND sequencename = 'event_seq_seq'"
        ).fetchone()
    assert row is not None
    return int(row[0])


# ------------------------------------------------------------ phòng thủ đứng vững ----
@pytest.mark.parametrize(
    "q",
    [
        "INSERT INTO {t}.emp (code) VALUES ('U1') ON CONFLICT (id) DO UPDATE SET state='x'",
        "INSERT INTO {t}.emp (code, state) SELECT 'U3', 'approved'",
        "UPDATE {t}.emp e SET state = o.s FROM (SELECT 'approved'::text s) o",
        "MERGE INTO {t}.emp t USING (SELECT 1) s ON true WHEN MATCHED THEN UPDATE SET state='x'",
        "UPDATE {t}.emp SET revision = 1",
        "UPDATE {t}.emp SET tenant_id = gen_random_uuid()",
        "UPDATE {api}.emp SET note = 'x'",
        "ALTER TABLE {t}.emp DISABLE TRIGGER xt_audit",
        "SET LOCAL ROLE xt_workflow; INSERT INTO {t}.emp (code, state) VALUES ('W', 'x')",
        "CREATE TEMP TABLE t (x int)",
        "SET session_replication_role = replica",
        "SELECT * FROM xt_audit.pepper",
        "UPDATE xt_meta.catalog_version SET spec = '{{}}'",
        "INSERT INTO xt_meta.apply_job (tenant_seq, version, ord, phase, change_id, sql)"
        " VALUES (1, 1, 99, 'post', 'x', 'SELECT 1')",
    ],
)
def test_xt_app_cannot_bypass_gm1_or_escalate(env: Env, q: str) -> None:
    s = env.a.seq
    with env.pg.app() as c, c.transaction(force_rollback=True):
        ctx(c, env.a)  # ngoài pytest.raises: mất quyền set_context thì test phải đỏ
        assert c.execute("SELECT xt.tenant() = %s", (env.a.id,)).fetchone() == (True,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(q.format(t=f"xt_t{s}", api=f"api_t{s}"))


def test_copy_obeys_rls_across_tenant_schemas(env: Env) -> None:
    env.records.create(hr(env.b), "emp", {"code": "B-ONLY"})
    with env.pg.app() as c, c.transaction():
        ctx(c, env.a)
        with c.cursor().copy(f"COPY (SELECT * FROM xt_t{env.b.seq}.emp) TO STDOUT") as cp:
            assert sum(1 for _ in cp.rows()) == 0


def test_lock_timeout_rolls_back_whole_tenant(env: Env) -> None:
    new = bump(
        env.migrator.current(env.a),
        lambda d: model(d, "customer")["fields"].append(
            {"id": "f9", "name": "zalo", "type": "text", "label": "Zalo", "pii": "basic"}
        ),
    )
    p = env.migrator.plan(env.a, new, rehearse=True)
    hold = env.pg.app()
    hold.execute("BEGIN")
    hold.execute(f"SELECT count(*) FROM xt_t{env.a.seq}.customer")
    try:
        with pytest.raises(ModelError) as ex:
            env.models.apply_catalog(env.a, new, p.hash, set(), "agent:x", "user:y")
        assert ex.value.code == "timeout"
    finally:
        hold.execute("ROLLBACK")
        hold.close()
    cur = env.migrator.current(env.a)
    assert cur is not None and cur.version == new.version - 1
    env.models.apply_catalog(env.a, new, p.hash, set(), "agent:x", "user:y")


# ------------------------------------------------------------------- lỗi đã thấy ----
def test_renamed_sensitive_field_still_hmac_in_audit(env: Env) -> None:
    t = env.b
    rec = env.records.create(hr(t), "emp", {"code": "R1", "salary": 12345678})["record"]
    new = bump(env.migrator.current(t), lambda d: field(d, "emp", "salary").update(name="luong"))
    do_apply(env, t, new)
    env.records.update(hr(t), "emp", uuid.UUID(rec["id"]), 1, {"luong": 99999999})
    ev = env.audit.events(hr(t), uuid.UUID(rec["id"]))[-1]
    assert "99999999" not in str(ev["diff"]) and "12345678" not in str(ev["diff"])


def test_rehearsal_does_not_execute_backfill(env: Env) -> None:
    t = env.a
    with env.pg.migrator() as m:
        # emp có field sensitive: ghi bắt buộc kèm khóa HMAC (AUD-07, khóa không nằm trong DB)
        m.execute(
            f"INSERT INTO xt_t{t.seq}.emp (tenant_id, code)"
            " SELECT %s, 'BULK' || g FROM generate_series(1, 500) g",
            (t.id,),
        )
    before = audit_seq(env)
    new = bump(
        env.migrator.current(t),
        lambda d: field(d, "emp", "note").update(required=True, default="n/a"),
    )
    env.migrator.plan(t, new, rehearse=True)
    # sequence không rollback: số event audit sinh ra trong transaction diễn tập đã hủy
    assert audit_seq(env) - before < 10


@pytest.mark.parametrize("op", ["contains", "startswith"])
def test_search_contains_and_startswith(env: Env, op: str) -> None:
    env.records.create(hr(env.a), "emp", {"code": "L1", "note": "100% done_ok"})
    out = env.records.search(hr(env.a), "emp", where={"field": "note", "op": op, "value": "100%"})
    assert out["total"] >= 1


@pytest.mark.parametrize("fname", ["created_at", "updated_at", "owner_id"])
def test_search_filter_on_system_fields(env: Env, fname: str) -> None:
    value = str(uuid.UUID(int=7)) if fname == "owner_id" else "2020-01-01T00:00:00+00:00"
    op = "=" if fname == "owner_id" else ">"
    out = env.records.search(hr(env.a), "emp", where={"field": fname, "op": op, "value": value})
    assert out["total"] >= 0


def test_audit_chain_survives_repeatable_read_writers(env: Env) -> None:
    t = env.b
    barrier = threading.Barrier(2)
    outcome: list[str] = []
    before = env.audit.verify(hr(t)).events

    def writer(i: int) -> None:
        with env.pg.app() as c:
            c.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
            ctx(c, t)
            c.execute(f"SELECT count(*) FROM xt_t{t.seq}.emp")
            barrier.wait()
            try:
                c.execute(f"INSERT INTO xt_t{t.seq}.emp (code) VALUES (%s)", (f"RR{i}",))
                c.execute("COMMIT")
                outcome.append("ok")
            except psycopg.errors.SerializationFailure:
                c.execute("ROLLBACK")
                outcome.append("serialization")

    ths = [threading.Thread(target=writer, args=(i,)) for i in range(2)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    # chuỗi không rẽ nhánh: hoặc cả hai nối tuần tự, hoặc transaction sau phải thử lại
    assert "ok" in outcome and len(outcome) == 2
    st = env.audit.verify(hr(t))
    assert st.intact and st.events == before + outcome.count("ok")


def test_dead_post_job_blocks_or_repairs_next_version(env: Env) -> None:
    t = env.b
    new = bump(
        env.migrator.current(t),
        lambda d: model(d, "emp")["fields"].append(
            {"id": "e9", "name": "badge", "type": "text", "label": "Thẻ", "unique": True,
             "pii": "none"}
        ),
    )  # fmt: skip
    p = env.migrator.plan(t, new, rehearse=True)
    env.migrator.apply(t, new, p.hash, set(), proposed_by="a", approved_by="b")
    env.records.create(hr(t), "emp", {"code": "D1", "badge": "SAME"})
    env.records.create(hr(t), "emp", {"code": "D2", "badge": "SAME"})
    for _ in range(3):
        env.migrator.run_jobs(t.seq)
    nxt = bump(env.migrator.current(t), lambda d: field(d, "emp", "note").update(label="GC 2"))
    p2 = env.migrator.plan(t, nxt, rehearse=True)
    try:
        env.migrator.apply(t, nxt, p2.hash, set(), proposed_by="a", approved_by="b")
    except ModelError:
        return  # chấp nhận: chặn version mới khi version trước post_state=failed
    with env.pg.su() as su:
        ok = su.execute(
            "SELECT coalesce(bool_and(indisvalid), false) FROM pg_index"
            " WHERE indexrelid::regclass::text = %s",
            (f"xt_t{t.seq}.ux_m2_e9",),
        ).fetchone()
    assert ok is not None and ok[0], "catalog unique=true nhưng không có unique index hợp lệ"


def test_migrator_cannot_install_pepper(env: Env) -> None:
    """Mục tiêu gốc AUD-07: xt_migrator không có được năng lực dò ngược HMAC (không đọc/cài được
    khóa). Sau quyết định 16/10: khóa không bao giờ vào Postgres, kể cả trong transaction ghi."""
    with env.pg.migrator() as m:
        for q in (
            "INSERT INTO xt_audit.pepper (kid, fingerprint, active) VALUES (77, %s, true)",
            "UPDATE xt_audit.pepper SET fingerprint = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(q, (b"K" * 32,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("SELECT * FROM xt_audit.pepper")
        assert m.execute(
            "SELECT to_regprocedure('xt_audit.set_pepper(int, bytea)')"
        ).fetchone() == (None,)
        # Migrator kiểm soát bảng: gài policy "gián điệp" chạy trong transaction ghi của xt_app.
        m.execute("CREATE TABLE xt_meta.spy (k text, h text)")
        m.execute("GRANT INSERT ON xt_meta.spy TO xt_app")
        m.execute(
            "CREATE FUNCTION xt.spy() RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER AS $$"
            " BEGIN INSERT INTO xt_meta.spy VALUES (current_setting('xt.audit_key', true),"
            " current_setting('xt.audit_hmac', true)); RETURN true; END $$"
        )
        m.execute("GRANT EXECUTE ON FUNCTION xt.spy() TO xt_app")
        m.execute(
            f"CREATE POLICY spy ON xt_t{env.a.seq}.emp AS RESTRICTIVE FOR INSERT TO xt_app"
            " WITH CHECK (xt.spy())"
        )
    try:
        env.records.create(hr(env.a), "emp", {"code": "SPY1", "salary": 31000000})
        with env.pg.migrator() as m:
            rows = m.execute("SELECT k, h FROM xt_meta.spy").fetchall()
        assert rows, "policy gián điệp phải đã chạy"
        for k, h in rows:
            assert not k  # không có khóa trong phiên
            assert h and "hmac:1:" in h and env.pg.pepper.hex() not in h and "31000000" not in h
    finally:
        with env.pg.migrator() as m:
            m.execute(f"DROP POLICY spy ON xt_t{env.a.seq}.emp")
            m.execute("DROP FUNCTION xt.spy()")
            m.execute("DROP TABLE xt_meta.spy")
    # thiếu HMAC hoặc HMAC của kid lạ → trigger từ chối (fail-closed)
    with env.pg.app() as c, c.transaction(force_rollback=True):
        ctx(c, env.a)
        for hm in (None, '{"new": {"salary": "hmac:77:' + "0" * 32 + '"}}'):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                if hm:
                    c.execute("SELECT set_config('xt.audit_hmac', %s, true)", (hm,))
                c.execute(f"INSERT INTO xt_t{env.a.seq}.emp (code, salary) VALUES ('K1', 31000000)")


def test_stale_proposal_is_revision_conflict(env: Env) -> None:
    t = env.a
    req = ProposeModelChange.model_validate(
        {
            "model_id": "customer",
            "operations": [
                {"op": "add_field", "field": {"id": "zalo2", "label": "Zalo 2", "type": "string"}}
            ],
            "rationale": "audit",
            "dry_run": False,
            "idempotency_key": "audit-stale-0001",
        }
    )
    out = env.models.propose(hr(t), req, proposed_by="agent:x")
    pid = uuid.UUID(str(out["proposal"]).split(":", 1)[1])
    other = bump(env.migrator.current(t), lambda d: field(d, "customer", "note").update(label="N"))
    p = env.migrator.plan(t, other, rehearse=False)
    env.models.apply_catalog(t, other, p.hash, set(), "agent:y", "user:z")
    with pytest.raises(ModelError) as ex:
        env.models.approve_and_apply(
            t, pid, approved_hash=str(out.get("plan_hash")), confirm=[], approved_by="user:z"
        )
    assert ex.value.code == "revision_conflict"


def test_workflow_role_cannot_forge_created_by(env: Env) -> None:
    env.records.create(hr(env.a), "emp", {"code": "CB1"})
    with env.pg.app() as c, c.transaction(force_rollback=True):
        ctx(c, env.a)
        c.execute("SET LOCAL ROLE xt_workflow")
        with pytest.raises(psycopg.errors.CheckViolation) as ex, c.transaction():
            c.execute(
                f"UPDATE xt_t{env.a.seq}.emp SET created_by = gen_random_uuid() WHERE code = 'CB1'"
            )
        assert ex.value.diag.constraint_name == "xt_immutable_system_columns"


# ----------------------------------------------------------------- vòng 2 (R2-*) ----
def test_r2_04_migrator_cannot_tamper_or_forge_audit(env: Env) -> None:
    s = env.a.seq
    with env.pg.migrator() as m:
        attacks = [
            f"ALTER TABLE xt_t{s}.emp DISABLE TRIGGER xt_audit",
            f"ALTER TABLE xt_t{s}.emp DISABLE TRIGGER ALL",
            f"DROP TRIGGER xt_audit ON xt_t{s}.emp",
            f"CREATE TRIGGER xt_audit2 AFTER INSERT ON xt_t{s}.emp FOR EACH ROW"
            " EXECUTE FUNCTION xt_audit.log()",
            f"CREATE RULE r AS ON INSERT TO xt_t{s}.emp DO INSTEAD NOTHING",
            "CREATE TABLE xt_meta.fake (tenant_id uuid, id uuid);"
            " CREATE TRIGGER xt_audit AFTER INSERT ON xt_meta.fake FOR EACH ROW"
            " EXECUTE FUNCTION xt_audit.log()",
            "SELECT xt_audit.install('xt_meta', 'tenant', '{}')",
            "SELECT xt_audit.append(gen_random_uuid(), 'x', gen_random_uuid(), 'INSERT', '{}', 0)",
            "CREATE FUNCTION xt.noop() RETURNS trigger LANGUAGE plpgsql AS"
            " $$ BEGIN RETURN NULL; END $$;"
            f" CREATE TRIGGER spy AFTER INSERT ON xt_t{s}.emp FOR EACH ROW"
            " EXECUTE FUNCTION xt.noop()",
        ]
        for q in attacks:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), m.transaction():
                m.execute(q)
        trig = m.execute(
            "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = %s::regclass"
            " AND NOT tgisinternal ORDER BY 1",
            (f"xt_t{s}.emp",),
        ).fetchall()
        assert trig == [("xt_audit", "O"), ("xt_audit_truncate", "O"), ("xt_touch", "O")]
    # TRUNCATE của chủ bảng vẫn để lại dấu vết trong chuỗi
    before = env.audit.verify(hr(env.a)).events
    with env.pg.migrator() as m:
        m.execute(f"CREATE TABLE xt_t{s}.scratch (id uuid, tenant_id uuid)")
        m.execute(f"GRANT TRIGGER ON xt_t{s}.scratch TO xt_audit_owner")
        m.execute("SELECT xt_audit.install(%s, 'scratch', '{}')", (f"xt_t{s}",))
        m.execute(f"TRUNCATE xt_t{s}.scratch")
        # event giả bằng bảng tự tạo: tenant_id phải đúng tenant của schema
        with pytest.raises(psycopg.errors.InsufficientPrivilege), m.transaction():
            m.execute(f"INSERT INTO xt_t{s}.scratch VALUES (gen_random_uuid(), %s)", (env.b.id,))
        m.execute(f"DROP TABLE xt_t{s}.scratch")
    st = env.audit.verify(hr(env.a))
    assert st.intact and st.events == before + 1
    ev = env.audit.events(hr(env.a), uuid.UUID(int=0))
    assert ev and ev[-1]["op"] == "TRUNCATE"


def test_r2_02_pending_jobs_of_earlier_version_block_next_apply(env: Env) -> None:
    t = env.a
    v1 = bump(
        env.migrator.current(t),
        lambda d: model(d, "emp")["fields"].append(
            {"id": "e7", "name": "badge2", "type": "text", "label": "Thẻ 2", "unique": True,
             "pii": "none"}
        ),
    )  # fmt: skip
    p1 = env.migrator.plan(t, v1, rehearse=True)
    env.migrator.apply(t, v1, p1.hash, set(), proposed_by="a", approved_by="b")
    # vN+1 không có job pha 2 nhưng job của vN còn chờ → chặn (catalog phải bằng CSDL)
    v2 = bump(env.migrator.current(t), lambda d: field(d, "emp", "note").update(label="GC r2"))
    p2 = env.migrator.plan(t, v2, rehearse=True)
    with pytest.raises(ModelError) as ex:
        env.migrator.apply(t, v2, p2.hash, set(), proposed_by="a", approved_by="b")
    assert ex.value.details["reason"] == "post_pending"
    env.migrator.run_jobs(t.seq)
    env.migrator.apply(t, v2, p2.hash, set(), proposed_by="a", approved_by="b")
    assert env.migrator.reconcile()[t.seq] == "validated"


def test_r2_03_parallel_workers_claim_jobs_once(env: Env) -> None:
    t = env.a
    new = bump(
        env.migrator.current(t),
        lambda d: model(d, "emp")["indexes"].append({"id": "i9", "fields": ["code", "note"]}),
    )
    p = env.migrator.plan(t, new, rehearse=True)
    env.migrator.apply(t, new, p.hash, set(), proposed_by="a", approved_by="b")
    results: list[Any] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(env.migrator.run_jobs(t.seq))

    ths = [threading.Thread(target=worker) for _ in range(4)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert sum(r.done for r in results) >= 1
    assert sum(r.dead + r.failed for r in results) == 0
    env.migrator.run_jobs(t.seq)
    with env.pg.su() as su:
        ok = su.execute(
            "SELECT count(*), bool_and(i.indisvalid) FROM pg_index i"
            " JOIN pg_class c ON c.oid = i.indexrelid WHERE c.relname = 'ix_m2_i9'"
            " AND c.relnamespace = %s::regnamespace",
            (f"xt_t{t.seq}",),
        ).fetchone()
        assert ok == (1, True)
        assert su.execute(
            "SELECT count(*) FROM xt_meta.apply_job WHERE tenant_seq = %s AND status <> 'done'",
            (t.seq,),
        ).fetchone() == (0,)
