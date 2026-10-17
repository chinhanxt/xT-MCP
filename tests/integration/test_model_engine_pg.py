# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Model engine trên Postgres 17 thật: 3 gói nghiệp vụ vào 2 tenant khác phiên bản, cô lập RLS +
schema, G-M1 quyền cột, G-M2 nhãn DLCN + HMAC audit, rollout 5 tenant có lỗi tiêm vào, phát hiện
sửa audit. Các test chạy theo thứ tự trong module (dùng chung một DB)."""

from __future__ import annotations

import copy
import json
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import psycopg
import pytest
from psycopg import sql
from pydantic import Field, TypeAdapter
from pydantic_core import SchemaError

from tests.integration import pg as pgmod
from tests.unit.model.support import PACKS, pack_models
from xt.adapters.db.audit import AuditReader, verify_hmac
from xt.adapters.db.catalog import PgCatalogReader, PgProposalStore
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import bind_bi_login, bootstrap, drop_tenant, provision_tenant
from xt.application.model_engine.service import ModelService, RecordService
from xt.contracts.model import ProposeModelChange
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import RiskClass
from xt.domain.model.spec import Catalog

pytestmark = pytest.mark.integration
SAFE_RISKY = frozenset({RiskClass.SAFE, RiskClass.RISKY})


@dataclass
class Env:
    pg: pgmod.Pg
    app: Database
    mig_db: Database
    migrator: PgMigrator
    records: RecordService
    models: ModelService
    audit: AuditReader
    tenants: dict[str, TenantRef]


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    pg = pgmod.start()
    try:
        with pg.su() as su:
            assert bootstrap(
                su,
                migrator_password=pg.migrator_password,
                app_password=pg.app_password,
                pepper=pg.pepper,
            )
            assert not bootstrap(su, migrator_password="x", app_password="y", pepper=pg.pepper)
        key = AuditKey(1, pg.pepper)
        app = Database(pg.app_dsn, max_size=4, audit_key=key)
        mig_db = Database(pg.migrator_dsn, max_size=2)
        migrator = PgMigrator(pg.migrator, lock_timeout="1s", post_lock_timeout="5s", audit_key=key)
        cats = PgCatalogReader(app)
        e = Env(
            pg,
            app,
            mig_db,
            migrator,
            RecordService(cats, PgRecordRepository(app)),
            ModelService(cats, migrator, PgProposalStore(app, mig_db)),
            AuditReader(app),
            {},
        )
        with pg.migrator() as m:
            e.tenants["A"] = provision_tenant(m, uuid.uuid4(), "Công ty A")
            e.tenants["B"] = provision_tenant(m, uuid.uuid4(), "Công ty B")
        yield e
        app.close()
        mig_db.close()
    finally:
        pgmod.stop()


def actor(e: Env, t: str, *roles: str, depts: tuple[uuid.UUID, ...] = ()) -> ModelActor:
    return ModelActor(e.tenants[t], uuid.UUID(int=hash(t) & 0xFFFF), roles, department_ids=depts)


def cat_of(
    version: int, *packs: str, edit: Callable[[list[dict[str, Any]]], None] | None = None
) -> Catalog:
    ms = copy.deepcopy(pack_models(*packs))
    if edit:
        edit(ms)
    return Catalog.model_validate({"version": version, "models": ms})


def apply(e: Env, t: TenantRef, new: Catalog, confirm: set[str] | None = None) -> None:
    plan = e.migrator.plan(t, new, rehearse=True)
    assert not plan.ids(RiskClass.BLOCKED), plan.render_vi()
    e.migrator.apply(
        t,
        new,
        plan.hash,
        confirm or set(plan.ids(RiskClass.DESTRUCTIVE)),
        proposed_by="agent:it",
        approved_by="user:it",
    )
    stats = e.migrator.run_jobs(t.seq)
    assert stats.failed == stats.dead == 0 and not stats.pending_versions


def managed_proposal(ms: list[dict[str, Any]]) -> None:
    p = next(m for m in ms if m["name"] == "proposal")
    for f in p["fields"]:
        if f["name"] in ("state", "final_approver", "approved_at", "current_step"):
            f["managed_by"] = "workflow"


def task_extra(ms: list[dict[str, Any]]) -> None:
    t = next(m for m in ms if m["name"] == "task")
    t["fields"].append(
        {"id": "f99", "name": "customer_ref", "type": "text", "label": "Mã khách hàng"}
    )


def set_ctx(conn: psycopg.Connection[Any], t: TenantRef, *roles: str) -> None:
    conn.execute(
        "SELECT xt.set_context(%s, NULL, %s::text[], '{}', '{}', NULL, NULL, NULL)",
        (t.id, list(roles)),
    )


def q1(conn: psycopg.Connection[Any], query: str, *args: Any) -> Any:
    row = conn.execute(query, args or None).fetchone()
    return row[0] if row else None


# --------------------------------------------------------------------------- tests ----
def test_three_packs_into_two_tenants_with_different_versions(env: Env) -> None:
    a, b = env.tenants["A"], env.tenants["B"]
    apply(env, a, cat_of(1, PACKS[0]))
    v2 = cat_of(2, *PACKS, edit=managed_proposal)
    plan = env.migrator.plan(a, v2, rehearse=True)
    kinds = {c.kind for c in plan.changes}
    assert sum(c.kind == "add_model" for c in plan.changes) == 13 and kinds == {
        "add_model",
        "grants",
    }
    apply(env, a, v2)
    apply(env, b, cat_of(1, PACKS[1], PACKS[2], edit=task_extra))
    with env.pg.su() as su:

        def tables(seq: int) -> int:
            return int(q1(su, "SELECT count(*) FROM pg_tables WHERE schemaname = %s", f"xt_t{seq}"))

        assert (tables(a.seq), tables(b.seq)) == (19, 13)
        forced = su.execute(
            "SELECT count(*) FILTER (WHERE relrowsecurity AND relforcerowsecurity), count(*) FROM"
            " pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'r' AND n.nspname"
            " LIKE 'xt\\_t%'"
        ).fetchone()
        assert forced == (32, 32)
        views = su.execute(
            "SELECT count(*), bool_and(c.relowner = 'xt_api_owner'::regrole),"
            " bool_and(coalesce(c.reloptions, '{}') @> ARRAY['security_invoker=false'])"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind = 'v'"
            " AND n.nspname IN (%s, %s)",
            (f"api_t{a.seq}", f"api_t{b.seq}"),
        ).fetchone()
        assert views == (32, True, True)
        cols = {
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s"
                " AND table_name = 'employee_pii'",
                (f"api_t{a.seq}",),
            )
        }
        assert "national_id" in cols and not {"base_salary", "bank_account"} & cols
        b_task = {
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND"
                " table_name = 'task'",
                (f"xt_t{b.seq}",),
            )
        }
        a_task = {
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND"
                " table_name = 'task'",
                (f"xt_t{a.seq}",),
            )
        }
        assert "customer_ref" in b_task and "customer_ref" not in a_task
        assert int(q1(su, "SELECT count(*) FROM pg_policies WHERE schemaname LIKE 'xt\\_t%'")) > 100
    assert env.migrator.version_distribution() == {2: [a.seq], 1: [b.seq]}


def _dept(env: Env, t: str, code: str) -> dict[str, Any]:
    return env.records.create(
        actor(env, t, "hr"), "department", {"code": code, "name": f"Phòng {code}", "active": True}
    )


def test_rls_and_schema_isolation(env: Env) -> None:
    a, b = env.tenants["A"], env.tenants["B"]
    ra = _dept(env, "A", "KT")["record"]
    rb = _dept(env, "B", "KT")["record"]  # cùng mã ở tenant khác: không đụng unique
    with pytest.raises(ModelError) as ex:
        _dept(env, "A", "KT")
    assert ex.value.code == "rule_violation" and "đã tồn tại" in ex.value.message
    # B không thấy bản ghi của A theo id
    with pytest.raises(ModelError) as ex:
        env.records.get(actor(env, "B", "hr"), "department", uuid.UUID(ra["id"]))
    assert ex.value.code == "not_found"
    assert env.records.search(actor(env, "B", "hr"), "department")["total"] == 1
    # FK chéo tenant bị chặn
    with pytest.raises(ModelError) as ex:
        env.records.create(
            actor(env, "A", "hr"),
            "employee",
            {
                "emp_no": "NV-0001",
                "full_name": "An",
                "job_title": "KT",
                "department": rb["id"],
                "state": "permanent",
                "synthetic": True,
            },
        )
    assert ex.value.details.get("reason") == "bad_reference", (ex.value.message, ex.value.details)
    with env.pg.app() as c:
        # quên đặt tenant → 0 dòng (fail-closed)
        assert q1(c, f"SELECT count(*) FROM xt_t{a.seq}.department") == 0
        # GUC tự đặt KHÔNG có tác dụng: RBAC chỉ đọc ngữ cảnh do xt.set_context ghi (AUD-13)
        with c.transaction():
            c.execute(
                "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.roles', 'hr', true)",
                (str(a.id),),
            )
            assert q1(c, f"SELECT count(*) FROM xt_t{a.seq}.department") == 0
        with c.transaction():
            set_ctx(c, a, "hr")
            assert q1(c, f"SELECT count(*) FROM xt_t{a.seq}.department") == 1
            # lỗi route sang bảng của tenant B trong ngữ cảnh A → 0 dòng (RLS vẫn chặn)
            assert q1(c, f"SELECT count(*) FROM xt_t{b.seq}.department") == 0
        # ngữ cảnh chỉ sống trong transaction đã đặt nó
        assert q1(c, f"SELECT count(*) FROM xt_t{a.seq}.department") == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            set_ctx(c, a, "hr")
            c.execute(
                f"INSERT INTO xt_t{a.seq}.department (tenant_id, code, name, active) VALUES (%s,"
                " 'XX', 'x', true)",
                (str(b.id),),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(f"ALTER TABLE xt_t{a.seq}.department DISABLE ROW LEVEL SECURITY")
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(
                "INSERT INTO xt_meta.session_ctx (pid, xid, tenant_id, roles, team_ids,"
                " department_ids) VALUES (pg_backend_pid(), pg_current_xact_id(), %s,"
                " '{hr}', '{}', '{}')",
                (a.id,),
            )
    with env.pg.su() as su:
        su.execute("CREATE ROLE bi_a LOGIN PASSWORD 'bi-a-pass'")
        su.execute("CREATE ROLE bi_noroles LOGIN PASSWORD 'bi-n-pass'")
        su.execute("ALTER ROLE xt_web LOGIN PASSWORD 'web-pass'")
    try:
        # xt_web (role JWT của PostgREST) không leo được sang xt_app/xt_workflow (SMT §5.2)
        with psycopg.connect(f"{pgmod.DSN} user=xt_web password=web-pass", autocommit=True) as web:
            for target in ("xt_app", "xt_workflow", "xt_api_owner"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    web.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(target)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                web.execute(f"SELECT count(*) FROM xt_t{a.seq}.department")
            with pytest.raises(psycopg.errors.InsufficientPrivilege), web.transaction():
                set_ctx(web, a, "hr")  # chỉ xt_app được đặt ngữ cảnh
            with web.transaction():
                web.execute(
                    "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.roles', 'hr',"
                    " true)",
                    (str(a.id),),
                )
                assert q1(web, f"SELECT count(*) FROM api_t{a.seq}.department") == 0
    finally:
        with env.pg.su() as su:
            su.execute("ALTER ROLE xt_web NOLOGIN")
    with env.pg.migrator() as m:
        bind_bi_login(m, "bi_a", a, roles=("hr",))
        bind_bi_login(m, "bi_noroles", a)
        with pytest.raises(ValueError):
            bind_bi_login(m, "xt_app", a, roles=("hr",))
    with psycopg.connect(f"{pgmod.DSN} user=bi_a password=bi-a-pass", autocommit=True) as bi:
        with bi.transaction():
            bi.execute(
                "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.roles', 'admin',"
                " true)",
                (str(b.id),),
            )
            assert q1(bi, f"SELECT count(*) FROM api_t{a.seq}.department") == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            bi.execute(f"SELECT count(*) FROM api_t{b.seq}.department")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            bi.execute(f"SELECT count(*) FROM xt_t{a.seq}.department")
    # BI không có role cố định: tự đặt xt.roles/xt.actor_id cũng không thấy gì (AUD-13)
    dsn = f"{pgmod.DSN} user=bi_noroles password=bi-n-pass"
    with psycopg.connect(dsn, autocommit=True) as bi, bi.transaction():
        bi.execute(
            "SELECT set_config('xt.roles', 'hr', true), set_config('xt.actor_id', %s, true)",
            (str(uuid.uuid4()),),
        )
        assert q1(bi, f"SELECT count(*) FROM api_t{a.seq}.department") == 0


def test_gm1_workflow_fields_column_privileges(env: Env) -> None:
    a = env.tenants["A"]
    wf = actor(env, "A", "xt_workflow")
    types = env.records.create(
        actor(env, "A", "admin"),
        "proposal_type",
        {"code": "advance", "name": "Tạm ứng", "process_id": "proposal_approval", "active": True},
    )["record"]
    values = {
        "proposal_no": "TU-2026-0042",
        "kind": types["id"],
        "requester": str(uuid.uuid4()),
        "department": "KT",
        "amount": "8000000",
        "reason": "Công tác Đà Nẵng",
    }
    with pytest.raises(ModelError) as ex:
        env.records.create(wf, "proposal", {**values, "state": "approved"})
    assert ex.value.code == "precondition_failed" and "Trạng thái" in ex.value.message
    rec = env.records.create(wf, "proposal", values)["record"]
    assert rec["state"] == "draft"
    rid = uuid.UUID(rec["id"])
    for patch in ({"state": "approved"}, {"final_approver": str(uuid.uuid4())}):
        with pytest.raises(ModelError) as ex:
            env.records.update(wf, "proposal", rid, 1, patch)
        assert ex.value.code == "precondition_failed"
    assert (
        env.records.update(wf, "proposal", rid, 1, {"amount": 9000000})["record"]["revision"] == 2
    )
    desc = env.models.describe_model(wf, "proposal", include_values_schema=True)["model"]
    assert (
        "state" in desc["workflow_fields"]
        and "state" not in desc["update_values_schema"]["properties"]
    )
    # Lớp DB: SQL thô của xt_app không ghi được cột workflow
    with env.pg.app() as c:
        ctx = "SELECT xt.set_context(%s::uuid, NULL, '{xt_workflow}', '{}', '{}', NULL, NULL, NULL)"
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(ctx, (str(a.id),))
            c.execute(f"UPDATE xt_t{a.seq}.proposal SET state = 'approved' WHERE id = %s", (rid,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute(ctx, (str(a.id),))
            c.execute(
                f"INSERT INTO xt_t{a.seq}.proposal (proposal_no, kind, requester, department,"
                " amount, reason, state)"
                " VALUES ('TU-2026-0043', %s, %s, 'KT', 1, 'hello', 'approved')",
                (types["id"], str(uuid.uuid4())),
            )
        with c.transaction():
            c.execute(ctx, (str(a.id),))
            assert (
                c.execute(
                    f"UPDATE xt_t{a.seq}.proposal SET reason = 'sửa SQL thô' WHERE id = %s", (rid,)
                ).rowcount
                == 1
            )
    # Đường transition (SET LOCAL ROLE xt_workflow) ghi được; vẫn chịu RLS
    out = env.records.workflow_write(
        wf,
        "proposal",
        rid,
        3,
        {
            "state": "approved",
            "final_approver": str(uuid.uuid4()),
            "approved_at": "2026-10-15T10:00:00+07:00",
        },
    )["record"]
    assert (out["state"], out["revision"]) == ("approved", 4)
    with pytest.raises(ModelError) as ex:
        env.records.workflow_write(
            actor(env, "B", "xt_workflow"), "proposal", rid, 4, {"state": "paid"}
        )
    assert ex.value.code == "not_found"  # B không có model proposal


def test_gm2_pii_labels_and_audit_hmac(env: Env) -> None:
    a = env.tenants["A"]
    hr = actor(env, "A", "hr", "hr_pii")
    desc = env.models.describe_model(hr, "employee_pii")["model"]
    f = {x["id"]: x for x in desc["fields"]}
    assert (f["base_salary"]["pii"], f["base_salary"]["sensitive"]) == (True, True)
    assert (f["national_id"]["pii"], f["national_id"]["sensitive"]) == (True, False)
    assert f["bank_account"]["sensitive"] is True
    dept = env.records.search(hr, "department")["items"][0]
    emp = env.records.create(
        hr,
        "employee",
        {
            "emp_no": "NV-0007",
            "full_name": "Nguyễn Văn A",
            "job_title": "KT",
            "department": dept["id"],
            "state": "permanent",
            "synthetic": True,
        },
    )["record"]
    rec = env.records.create(
        hr,
        "employee_pii",
        {"employee": emp["id"], "national_id": "001203004567", "base_salary": 15000000},
    )["record"]
    env.records.update(hr, "employee_pii", uuid.UUID(rec["id"]), 1, {"base_salary": 17500000})
    events = env.audit.events(hr, uuid.UUID(rec["id"]))
    ins, upd = events[0]["diff"], events[1]["diff"]
    assert ins["new"]["national_id"] == "001203004567"  # basic: nguyên văn (quyết định v0.3)
    stored = ins["new"]["base_salary"]
    assert stored.startswith("hmac:1:") and "15000000" not in str(ins)
    assert verify_hmac(env.pg.pepper, 1, 15000000, stored)
    assert not verify_hmac(env.pg.pepper, 1, 15000001, stored)
    assert verify_hmac(env.pg.pepper, 1, 17500000, upd["new"]["base_salary"])
    assert verify_hmac(env.pg.pepper, 1, 15000000, upd["old"]["base_salary"])
    assert env.audit.verify(hr).intact
    # Hạ nhãn DLCN là RISKY và tạo lại trigger audit
    cur = env.migrator.current(a)
    assert cur is not None
    d = cur.dump()
    d["version"] += 1
    m = next(x for x in d["models"] if x["name"] == "employee_pii")
    next(x for x in m["fields"] if x["name"] == "base_salary")["pii"] = "basic"
    plan = env.migrator.plan(a, Catalog.model_validate(d), rehearse=True)
    assert [(c.kind, c.cls) for c in plan.changes] == [("pii", RiskClass.RISKY)]


def test_external_checkpoint_detects_tail_deletion_and_recompute(env: Env, tmp_path: Path) -> None:
    """Quyết định 16/10 (3): đầu chuỗi được ký Ed25519 và lưu NGOÀI Postgres; superuser xóa đuôi
    hoặc tính lại chuỗi thì `verify_against_checkpoint` phát hiện (verify trong DB thì không)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from xt.adapters.db.audit import (
        CheckpointError,
        load_checkpoint,
        sign_checkpoint,
        take_checkpoint,
        verify_against_checkpoint,
        write_checkpoint,
    )
    from xt.entrypoints import audit_cli

    sk = Ed25519PrivateKey.generate()
    (tmp_path / "sk.pem").write_bytes(
        sk.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (tmp_path / "pk.pem").write_bytes(
        sk.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    cp_dir = tmp_path / "anchors"
    cli_env = {
        "XT_DB_APP_URL": env.pg.app_dsn,
        "XT_AUDIT_CHECKPOINT_DIR": str(cp_dir),
        "XT_AUDIT_SIGNING_KEY_FILE": str(tmp_path / "sk.pem"),
        "XT_AUDIT_SIGNING_KEY_ID": "anchor-2026-10",
        "XT_AUDIT_VERIFY_KEY_FILE": str(tmp_path / "pk.pem"),
    }
    assert audit_cli.run(["checkpoint"], cli_env) == 0
    assert audit_cli.run(["verify", "--against-checkpoint", "latest"], cli_env) == 0
    a = env.tenants["B"]  # xóa đuôi chuỗi của B (A/B đều toàn vẹn lúc này)
    with env.pg.app() as c:
        doc = load_checkpoint(sorted(cp_dir.glob("*.json"))[-1], sk.public_key())
        assert {h["tenant_id"] for h in doc["heads"]} >= {str(a.id)}
        assert all(r.ok for r in verify_against_checkpoint(c, doc))
    # chữ ký sai / tệp bị sửa
    bad = dict(doc, heads=[dict(h, seq=h["seq"] + 1) for h in doc["heads"]])
    forged = cp_dir / "forged.json"
    forged.write_text(json.dumps(sign_checkpoint(bad, Ed25519PrivateKey.generate(), "x")))
    with pytest.raises(CheckpointError):
        load_checkpoint(forged, sk.public_key())
    forged.unlink()
    # superuser xóa đuôi chuỗi của A rồi sửa head: verify trong DB vẫn "toàn vẹn"
    with env.pg.su() as su:
        last = q1(su, "SELECT max(seq) FROM xt_audit.event WHERE tenant_id = %s", a.id)
        su.execute("ALTER TABLE xt_audit.event DISABLE TRIGGER no_update_delete")
        su.execute("DELETE FROM xt_audit.event WHERE seq = %s", (last,))
        su.execute("ALTER TABLE xt_audit.event ENABLE TRIGGER no_update_delete")
        su.execute(
            "UPDATE xt_audit.head h SET seq = e.seq, hash = e.hash FROM (SELECT seq, hash"
            " FROM xt_audit.event WHERE tenant_id = %s ORDER BY seq DESC LIMIT 1) e"
            " WHERE h.tenant_id = %s",
            (a.id, a.id),
        )
    with env.pg.app() as c:
        res = {r.tenant_id: r for r in verify_against_checkpoint(c, doc)}
    assert res[str(a.id)].status == "missing"
    assert audit_cli.run(["verify", "--against-checkpoint", "latest"], cli_env) == 1
    # checkpoint mới sau khi đã bị xóa đuôi không "hợp thức hóa" được checkpoint cũ
    with env.pg.app() as c:
        doc2 = sign_checkpoint(take_checkpoint(c), sk, "anchor-2026-10")
    path2 = write_checkpoint(doc2, cp_dir)
    assert path2.exists() and len(list(cp_dir.glob("*.json"))) == 2
    with env.pg.app() as c:
        assert not all(r.ok for r in verify_against_checkpoint(c, doc))


def test_audit_chain_detects_tampering(env: Env) -> None:
    a, _b = env.tenants["A"], env.tenants["B"]
    hr_a, hr_b = actor(env, "A", "hr"), actor(env, "B", "hr")
    before = env.audit.verify(hr_a)
    assert before.intact and before.events >= 5
    assert env.audit.verify(hr_b).intact
    with env.pg.app() as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("UPDATE xt_audit.event SET diff = '{}'")
    with env.pg.migrator() as m, pytest.raises(psycopg.errors.InsufficientPrivilege):
        m.execute("DELETE FROM xt_audit.event")
    with env.pg.su() as su:
        with pytest.raises(psycopg.errors.RaiseException):
            su.execute("UPDATE xt_audit.event SET diff = '{}' WHERE tenant_id = %s", (a.id,))
        seq = q1(
            su,
            "SELECT seq FROM xt_audit.event WHERE tenant_id = %s AND diff ? 'new'"
            " ORDER BY seq OFFSET 1 LIMIT 1",
            a.id,
        )
        su.execute("ALTER TABLE xt_audit.event DISABLE TRIGGER no_update_delete")
        su.execute(
            "UPDATE xt_audit.event SET diff = jsonb_set(diff, '{new,name}', '\"giả mạo\"')"
            " WHERE seq = %s",
            (seq,),
        )
        su.execute("ALTER TABLE xt_audit.event ENABLE TRIGGER no_update_delete")
    after = env.audit.verify(hr_a)
    assert after.first_bad_seq == seq and after.events == before.events
    assert env.audit.verify(hr_b).intact


TICKET = {
    "id": "tk",
    "name": "ticket",
    "label": "Phiếu hỗ trợ",
    "fields": [
        {
            "id": "f1",
            "name": "title",
            "type": "text",
            "label": "Tiêu đề",
            "required": True,
            "max": 200,
        },
        {"id": "f2", "name": "amount", "type": "money", "label": "Chi phí", "min": 0},
        {"id": "f3", "name": "contact", "type": "text", "label": "Liên hệ"},
    ],
    "permissions": {"agent": {"actions": ["create", "read", "update"], "scope": "all"}},
}


def ticket_v2(cur: Catalog | None) -> Catalog:
    assert cur is not None
    d = cur.dump()
    d["version"] += 1
    m = d["models"][0]
    m["fields"].append(
        {
            "id": "f4",
            "name": "priority",
            "type": "enum",
            "label": "Ưu tiên",
            "required": True,
            "default": "normal",
            "options": [{"value": "normal", "label": "Thường"}, {"value": "high", "label": "Cao"}],
        }
    )
    next(f for f in m["fields"] if f["name"] == "amount")["max"] = (
        300_000_000  # siết: cần kiểm dữ liệu
    )
    m["indexes"] = [
        {"id": "i1", "fields": ["priority", "title"]}
    ]  # CREATE INDEX CONCURRENTLY ở pha 2
    return Catalog.model_validate(d)


def test_rollout_five_tenants_one_injected_failure_isolated(env: Env) -> None:
    ts: list[TenantRef] = []
    with env.pg.migrator() as m:
        for i in range(5):
            ts.append(provision_tenant(m, uuid.uuid4(), f"Rollout {i}", seq=11 + i))
    for t in ts:
        apply(env, t, Catalog.model_validate({"version": 1, "models": [TICKET]}))
        a = ModelActor(t, uuid.uuid4(), ("agent",))
        for j in range(20):
            env.records.create(a, "ticket", {"title": f"Phiếu {j}", "amount": 1000 * j})
    # tenant 14 có dữ liệu vi phạm ràng buộc mới → BLOCKED, không áp
    env.records.create(
        ModelActor(ts[3], uuid.uuid4(), ("agent",)),
        "ticket",
        {"title": "lớn", "amount": 400_000_000},
    )
    # tiêm lỗi: tenant 13 có giao dịch khác giữ khóa bảng → lock_timeout ở pha 1
    hold = env.pg.su()
    hold.autocommit = False
    hold.execute(f"LOCK TABLE xt_t{ts[2].seq}.ticket IN ACCESS SHARE MODE")
    stop = threading.Event()
    errors: list[str] = []

    def writer() -> None:  # ghi liên tục vào tenant 11 trong lúc rollout
        a = ModelActor(ts[0], uuid.uuid4(), ("agent",))
        n = 0
        while not stop.is_set():
            try:
                env.records.create(a, "ticket", {"title": f"live {n}"})
            except ModelError as e:
                errors.append(e.message)
            n += 1

    th = threading.Thread(target=writer)
    th.start()
    try:
        report = env.models.rollout(
            ts, ticket_v2, allow=SAFE_RISKY, proposed_by="agent:it", approved_by="user:admin"
        )
    finally:
        stop.set()
        th.join()
        hold.rollback()
        hold.close()
    assert not errors
    assert report.by_status("applied") == [11, 12, 15]
    assert report.by_status("failed") == [13] and "khóa" in (report.results[2].reason or "")
    assert report.by_status("blocked") == [14] and "vi phạm" in (report.results[3].reason or "")
    dist = report.distribution
    assert {11, 12, 15} <= set(dist[2]) and {13, 14} <= set(dist[1])
    # tenant lỗi giữ nguyên v1 và vẫn phục vụ được; retry sau khi khóa nhả
    assert env.migrator.current(ts[2]).version == 1  # type: ignore[union-attr]
    retry = env.models.rollout(
        [ts[2]], ticket_v2, allow=SAFE_RISKY, proposed_by="agent:it", approved_by="user:admin"
    )
    assert retry.by_status("applied") == [13]
    with env.pg.su() as su:
        assert (
            q1(
                su,
                "SELECT count(*) FROM pg_constraint WHERE NOT convalidated AND"
                " connamespace::regnamespace::text LIKE 'xt\\_t1_'",
            )
            == 0
        )
        assert q1(su, "SELECT count(*) FROM pg_index WHERE NOT indisvalid") == 0
        assert (
            q1(su, "SELECT count(*) FROM xt_meta.catalog_version WHERE post_state <> 'validated'")
            == 0
        )
        assert q1(su, "SELECT count(*) FROM pg_indexes WHERE indexname = 'ix_tk_i1'") == 4
    rec = env.records.create(
        ModelActor(ts[0], uuid.uuid4(), ("agent",)), "ticket", {"title": "sau v2"}
    )["record"]
    assert rec["priority"] == "normal"
    with pytest.raises(ModelError) as ex:
        env.records.create(
            ModelActor(ts[0], uuid.uuid4(), ("agent",)),
            "ticket",
            {"title": "x", "amount": 400_000_000},
        )
    assert ex.value.code == "invalid_request"  # Poka-yoke chặn ở app trước DB


def test_rename_by_stable_id_keeps_data_and_gates(env: Env) -> None:
    t = TenantRef(11, _tenant_id(env, 11))
    cur = env.migrator.current(t)
    assert cur is not None
    d = cur.dump()
    d["version"] += 1
    next(f for f in d["models"][0]["fields"] if f["name"] == "contact")["name"] = "contact_phone"
    new = Catalog.model_validate(d)
    plan = env.migrator.plan(t, new, rehearse=True)
    assert [(c.id, c.cls) for c in plan.changes] == [("rename_field:tk:f3", RiskClass.DESTRUCTIVE)]
    with pytest.raises(ModelError) as ex:
        env.models.apply_catalog(t, new, plan.hash, set(), "agent:it", "user:it")
    assert ex.value.details["reason"] == "confirmation_missing"
    with pytest.raises(ModelError) as ex:
        env.models.apply_catalog(
            t, new, "sha256:" + "0" * 64, {"rename_field:tk:f3"}, "agent:it", "user:it"
        )
    assert ex.value.code == "revision_conflict"
    a = ModelActor(t, uuid.uuid4(), ("agent",))
    rid = uuid.UUID(
        env.records.create(a, "ticket", {"title": "giữ", "contact": "0901234567"})["record"]["id"]
    )
    env.models.apply_catalog(t, new, plan.hash, {"rename_field:tk:f3"}, "agent:it", "user:it")
    assert env.records.get(a, "ticket", rid)["record"]["contact_phone"] == "0901234567"


def _tenant_id(env: Env, seq: int) -> uuid.UUID:
    with env.pg.migrator() as m:
        return uuid.UUID(str(q1(m, "SELECT id FROM xt_meta.tenant WHERE seq = %s", seq)))


def test_propose_model_change_end_to_end(env: Env) -> None:
    t = TenantRef(12, _tenant_id(env, 12))
    a = ModelActor(t, uuid.uuid4(), ("agent",))
    body = {
        "model_id": "device_loan",
        "rationale": "Quản lý mượn trả laptop",
        "idempotency_key": "it-device-0001",
        "operations": [
            {
                "op": "create_model",
                "model": {"label": "Phiếu mượn thiết bị", "primitive": "record"},
            },
            {
                "op": "add_field",
                "field": {"id": "device", "label": "Thiết bị", "type": "string", "required": True},
            },
            {
                "op": "add_field",
                "field": {"id": "due_date", "label": "Hạn trả", "type": "date", "required": True},
            },
            {
                "op": "add_rule",
                "rule": {
                    "id": "r1",
                    "expr": "due_date >= '2026-01-01'",
                    "message": "Hạn trả không hợp lệ",
                },
            },
        ],
    }
    dry = env.models.propose(a, ProposeModelChange.model_validate(body), proposed_by="agent:x")
    assert dry["dry_run"] and dry["classification"] == "additive" and dry["base_revision"] == 0
    with pytest.raises(ModelError) as ex:
        env.models.propose(
            a,
            ProposeModelChange.model_validate({**body, "base_revision": 5}),
            proposed_by="agent:x",
        )
    assert ex.value.code == "revision_conflict"
    sub = env.models.propose(
        a, ProposeModelChange.model_validate({**body, "dry_run": False}), proposed_by="agent:x"
    )
    pid = uuid.UUID(sub["proposal"].split(":", 1)[1])
    with pytest.raises(ModelError) as ex:
        env.models.approve_and_apply(
            t, pid, approved_hash=sub["plan_hash"], confirm=[], approved_by="agent:x"
        )
    assert ex.value.details["reason"] == "sod"
    out = env.models.approve_and_apply(
        t, pid, approved_hash=sub["plan_hash"], confirm=[], approved_by="user:mai"
    )
    assert out.version == 3
    env.migrator.run_jobs(t.seq)
    cur = env.migrator.current(t)
    assert cur is not None and cur.model("device_loan").rules[0].id == "r1"


def test_regex_semantics_match_postgres(env: Env) -> None:
    patterns = sorted(
        {
            f.regex
            for m in Catalog.model_validate({"version": 1, "models": pack_models(*PACKS)}).models
            for f in m.fields
            if f.regex
        }
    )
    samples = [
        "TU-2026-0042",
        "HU-2026-12",
        "NV-0001",
        "KT",
        "kt",
        "001203004567",
        "0901234567",
        "a@b.vn",
        "a b@c",
        "advance",
        "Advance",
        "",
        "x" * 41,
        "12345678901234567890",
        "tu_2026",
    ]
    with env.pg.su() as su:
        for p in patterns:
            ta = TypeAdapter(Annotated[str, Field(pattern=p)])
            for s in samples:
                rust = True
                try:
                    ta.validate_python(s)
                except Exception:
                    rust = False
                assert rust == bool(q1(su, "SELECT %s ~ %s", s, p)), (p, s)
    with pytest.raises(SchemaError):
        TypeAdapter(Annotated[str, Field(pattern="(a)\\1")])


def _bump(cur: Catalog | None, edit: Callable[[dict[str, Any]], None]) -> Catalog:
    assert cur is not None
    d = cur.dump()
    d["version"] += 1
    edit(d)
    return Catalog.model_validate(d)


def _audit_last_seq(env: Env) -> int:
    with env.pg.su() as su:
        return int(q1(su, "SELECT coalesce(max(seq), 0) FROM xt_audit.event"))


def test_backfill_runs_batched_in_phase2_not_in_dry_run(env: Env) -> None:
    t = TenantRef(13, _tenant_id(env, 13))
    a = ModelActor(t, uuid.uuid4(), ("agent",))
    for j in range(5):
        env.records.create(a, "ticket", {"title": f"bf {j}"})
    with env.pg.su() as su:
        nulls = int(q1(su, "SELECT count(*) FROM xt_t13.ticket WHERE contact IS NULL"))
    assert nulls >= 5

    def req(d: dict[str, Any]) -> None:
        f = next(x for x in d["models"][0]["fields"] if x["name"] == "contact")
        f.update(required=True, default="n/a")

    new = _bump(env.migrator.current(t), req)
    before = _audit_last_seq(env)
    plan = env.migrator.plan(t, new, rehearse=True)
    assert _audit_last_seq(env) == before  # dry-run không đụng dữ liệu
    ch = next(c for c in plan.changes if c.kind == "require_field")
    assert ch.cls is RiskClass.RISKY and ch.affected == nulls
    out = env.models.apply_catalog(t, new, plan.hash, set(), "agent:it", "user:it")
    assert out.queued_jobs >= 5
    with env.pg.su() as su:  # pha 1 chưa backfill
        assert int(q1(su, "SELECT count(*) FROM xt_t13.ticket WHERE contact IS NULL")) == nulls
    env.migrator.run_jobs(t.seq)
    with env.pg.su() as su:
        assert int(q1(su, "SELECT count(*) FROM xt_t13.ticket WHERE contact IS NULL")) == 0
        assert (
            q1(
                su,
                "SELECT progress FROM xt_meta.apply_job WHERE tenant_seq = 13"
                " AND phase = 'backfill' ORDER BY id DESC LIMIT 1",
            )
            == nulls
        )
        assert (
            q1(
                su,
                "SELECT attnotnull FROM pg_attribute WHERE attrelid = 'xt_t13.ticket'::regclass"
                " AND attname = 'contact'",
            )
            is True
        )
    assert env.migrator.post_state(t) == (out.version, "validated")


def test_dead_phase2_job_blocks_until_repaired_and_reconciles(env: Env) -> None:
    t = TenantRef(13, _tenant_id(env, 13))
    a = ModelActor(t, uuid.uuid4(), ("agent",))

    def add_badge(d: dict[str, Any]) -> None:
        d["models"][0]["fields"].append(
            {"id": "f9", "name": "badge", "type": "text", "label": "Thẻ", "unique": True}
        )

    new = _bump(env.migrator.current(t), add_badge)
    plan = env.migrator.plan(t, new, rehearse=True)
    env.models.apply_catalog(t, new, plan.hash, set(), "agent:it", "user:it")
    dup = [
        env.records.create(a, "ticket", {"title": f"d{i}", "badge": "SAME"})["record"]
        for i in range(2)
    ]
    for _ in range(3):
        env.migrator.run_jobs(t.seq)
    assert env.migrator.post_state(t) == (new.version, "failed")
    with env.pg.su() as su:  # index INVALID không bị bỏ lại
        assert (
            q1(
                su,
                "SELECT count(*) FROM pg_indexes WHERE schemaname = 'xt_t13'"
                " AND indexname = 'ux_tk_f9'",
            )
            == 0
        )
    nxt = _bump(env.migrator.current(t), lambda d: d["models"][0].update(label="Phiếu HT"))
    p2 = env.migrator.plan(t, nxt, rehearse=True)
    with pytest.raises(ModelError) as ex:
        env.models.apply_catalog(t, nxt, p2.hash, set(), "agent:it", "user:it")
    assert ex.value.details["reason"] == "post_failed"
    assert env.migrator.reconcile()[13] == "failed"
    env.records.update(a, "ticket", uuid.UUID(dup[1]["id"]), 1, {"badge": "OTHER"})
    stats = env.migrator.retry_failed(t)
    assert stats.dead == 0 and env.migrator.post_state(t) == (new.version, "validated")
    assert env.migrator.reconcile()[13] == "validated"
    env.models.apply_catalog(t, nxt, p2.hash, set(), "agent:it", "user:it")


def test_drop_tenant(env: Env) -> None:
    t = TenantRef(15, _tenant_id(env, 15))
    with env.pg.migrator() as m:
        drop_tenant(m, t)
    with env.pg.su() as su:
        assert (
            q1(su, "SELECT count(*) FROM pg_namespace WHERE nspname IN ('xt_t15', 'api_t15')") == 0
        )
    assert 15 not in {s for v in env.migrator.version_distribution().values() for s in v}
