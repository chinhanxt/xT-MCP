# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tầng 1 /data: PostgREST thật trên Postgres 17 thật, dựng bằng adapter thật (bootstrap +
postgrest.sql, migration nền tảng, register_tenant/import_org, PgMigrator, RecordService).

Kiểm: đọc đúng tenant + đúng phạm vi vai trò (lấy từ bảng tổ chức xT, không từ token), chặn chéo
tenant theo token/profile, ghi trường managed_by=workflow (và mọi ghi) qua /data bị từ chối, field
sensitive không bao giờ lộ (audit chỉ giữ HMAC), aud sai, leo role, claim tenant dị dạng, cô lập
ngữ cảnh giữa các transaction trên CÙNG một kết nối (pool = 1), tạo/xóa tenant + model khi server
đang chạy (db-pre-config + NOTIFY). Các test chạy theo thứ tự trong module, dùng chung một stack.

Bằng chứng cho docs: `XT_IT_HEADLESS_EVIDENCE=/tmp/xt-headless-evidence.md` ghi lệnh curl tương
đương + status/body THẬT (token chỉ hiện là `$TOKEN_…`).
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.integration import headless_stack as hs
from xt.adapters.db import data_setup
from xt.adapters.db.audit import AuditReader, verify_hmac
from xt.adapters.db.catalog import PgCatalogReader
from xt.adapters.db.connection import AuditKey, Database
from xt.adapters.db.data_setup import install_data_prerequisites
from xt.adapters.db.integration import import_org, person_id, register_tenant, unit_id
from xt.adapters.db.migrator import PgMigrator
from xt.adapters.db.records import PgRecordRepository
from xt.adapters.db.tenancy import (
    bind_bi_login,
    bootstrap,
    drop_tenant,
    install_postgrest_role,
    provision_tenant,
)
from xt.application.model_engine.service import RecordService
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import RiskClass
from xt.domain.model.spec import Catalog

pytestmark = pytest.mark.integration

DENIED = (401, 403)
# Ghi qua /data: chặn ở pre-request/quyền view (401/403), hoặc PostgREST từ chối trước khi chạy
# (400 PUT không PK, 404 schema mặc định xt_api_none không có bảng, 405).
WRITE_DENIED = (400, 401, 403, 404, 405)
NATIONAL_ID_A = "001203004567"
NATIONAL_ID_B = "079088001234"

TICKET: dict[str, Any] = {
    "id": "it_ticket",
    "name": "ticket",
    "label": "Phiếu yêu cầu",
    "title_field": "title",
    "fields": [
        {"id": "f1", "name": "title", "type": "text", "label": "Tiêu đề", "required": True,
         "pii": "none"},
        {"id": "f2", "name": "state", "type": "enum", "label": "Trạng thái", "required": True,
         "default": "open", "managed_by": "workflow", "pii": "none",
         "options": [{"value": "open", "label": "Mở"}, {"value": "closed", "label": "Đóng"}]},
        {"id": "f3", "name": "national_id", "type": "text", "label": "Số CCCD",
         "pii": "sensitive"},
        {"id": "f4", "name": "phone", "type": "text", "label": "Số điện thoại", "pii": "basic"},
    ],
    "permissions": {
        "manager": {"actions": ["create", "read", "update"], "scope": "all"},
        "staff": {"actions": ["create", "read"], "scope": "own"},
        "xt_workflow": {"actions": ["read", "update"], "scope": "all"},
    },
}  # fmt: skip


def simple_model(mid: str, name: str, label: str) -> dict[str, Any]:
    return {
        "id": mid,
        "name": name,
        "label": label,
        "title_field": "body",
        "fields": [
            {"id": "f1", "name": "body", "type": "text", "label": "Nội dung", "required": True,
             "pii": "none"},
        ],
        "permissions": {"manager": {"actions": ["create", "read"], "scope": "all"}},
    }  # fmt: skip


MEMO = simple_model("it_memo", "memo", "Ghi nhớ")
ASSET = simple_model("it_asset", "asset", "Tài sản")
ASSET_NOTE = simple_model("it_asset_note", "asset_note", "Ghi chú tài sản")


def catalog(version: int, *models: dict[str, Any]) -> Catalog:
    return Catalog.model_validate({"version": version, "models": copy.deepcopy(list(models))})


ORG_A = {
    "units": [{"key": "kt", "name": "Kế toán"}],
    "people": [
        {"key": "alice", "name": "Alice", "subject": "kc-alice", "roles": ["manager"],
         "unit": "kt"},
        {"key": "bob", "name": "Bob", "subject": "kc-bob", "roles": ["staff"], "unit": "kt"},
        {"key": "carol", "name": "Carol", "subject": "kc-carol", "roles": []},
        {"key": "frank", "name": "Frank", "subject": "kc-frank", "roles": ["manager"],
         "active": False},
    ],
}  # fmt: skip
ORG_B = {"people": [{"key": "dave", "name": "Dave", "subject": "kc-dave", "roles": ["manager"]}]}
ORG_C = {"people": [{"key": "erin", "name": "Erin", "subject": "kc-erin", "roles": ["manager"]}]}


@dataclass
class Env:
    st: hs.Stack
    rest: hs.Rest
    app: Database
    migrator: PgMigrator
    records: RecordService
    audit: AuditReader
    tenants: dict[str, TenantRef] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)
    setup_probes: list[tuple[str, str, bool]] = field(default_factory=list)

    def profile(self, alias: str) -> str:
        return f"api_t{self.tenants[alias].seq}"

    def actor(self, alias: str, key: str, *roles: str, unit: str | None = None) -> ModelActor:
        t = self.tenants[alias]
        depts = (unit_id(t.id, unit),) if unit else ()
        return ModelActor(t, person_id(t.id, key), roles, department_ids=depts)

    def token(self, org: Any, sub: str | None, **over: Any) -> str:
        claims = hs.data_claims(org, sub, **over)
        return self.st.signer.sign(claims)

    def get(self, alias_token: tuple[Any, str] | str | None, path: str, **kw: Any) -> hs.Resp:
        tok = self.token(*alias_token) if isinstance(alias_token, tuple) else alias_token
        return self.rest.get(path, token=tok, **kw)


# GUC ngữ cảnh mà xt.pgrst_gate đặt cho transaction PostgREST (đọc bởi nhánh xt_authenticator
# của xt.cur()). Phiên khác tự đặt nó không được có tác dụng.
PGRST_CTX_GUC = "xt.pgrst_ctx"


def forged_ctx(e: Env, alias: str) -> str:
    t = e.tenants[alias]
    key = "alice" if alias == "acme" else "dave"
    return json.dumps(
        {"t": str(t.id), "a": str(person_id(t.id, key)), "r": ["manager"], "tm": [], "d": []}
    )


def titles(resp: hs.Resp) -> set[str]:
    return {r["title"] for r in resp.rows}


def assert_denied(
    resp: hs.Resp, hint: str | tuple[str, ...] | None = None, codes: tuple[int, ...] = DENIED
) -> None:
    """Bị từ chối, không có dòng nào; `hint` = mã lý do của xt.pgrst_gate (HINT của lỗi 42501)."""
    assert resp.status in codes, (resp.status, resp.body)
    assert not isinstance(resp.body, list) or resp.body == [], resp.body
    if hint is not None:
        want = (hint,) if isinstance(hint, str) else hint
        assert isinstance(resp.body, dict) and resp.body.get("hint") in want, resp.body


def apply(e: Env, t: TenantRef, new: Catalog) -> None:
    plan = e.migrator.plan(t, new, rehearse=True)
    assert not plan.ids(RiskClass.BLOCKED), plan.render_vi()
    e.migrator.apply(
        t,
        new,
        plan.hash,
        set(plan.ids(RiskClass.DESTRUCTIVE)),
        proposed_by="agent:it-headless",
        approved_by="user:it-headless",
    )
    stats = e.migrator.run_jobs(t.seq)
    assert stats.failed == stats.dead == 0 and not stats.pending_versions


# Hàm ghi audit: xt_migrator không bao giờ được EXECUTE (R2-04, docs/decisions.md).
AUDIT_WRITE = ("xt_audit.log()", "xt_audit.append(uuid, text, uuid, text, jsonb)")


def audit_write_acl(conn: psycopg.Connection[Any]) -> list[Any]:
    return [
        conn.execute("SELECT proacl FROM pg_proc WHERE oid = %s::regprocedure", (f,)).fetchone()
        for f in AUDIT_WRITE
    ]


def migrator_audit_privs(conn: psycopg.Connection[Any], phase: str) -> list[tuple[str, str, bool]]:
    """Quyền hiệu lực (gồm role thành viên, quyền chưa commit trong transaction đang chạy)."""
    out = []
    for f in AUDIT_WRITE:
        row = conn.execute(
            "SELECT has_function_privilege('xt_migrator', %s, 'EXECUTE')", (f,)
        ).fetchone()
        assert row is not None
        out.append((phase, f, bool(row[0])))
    return out


def probed_data_setup(st: hs.Stack) -> list[tuple[str, str, bool]]:
    """Chạy install_data_prerequisites thật; probe quyền audit của xt_migrator trước, TRONG
    transaction quản trị (ngay trước và sau migration, khi current_user = xt_migrator, kèm gọi
    thử `append`) và sau commit. ACL hàm ghi audit phải giữ nguyên."""
    probes: list[tuple[str, str, bool]] = []
    real = data_setup.migrate

    def probe(conn: psycopg.Connection[Any]) -> list[str]:
        assert conn.execute("SELECT current_user").fetchone() == ("xt_migrator",)
        probes.extend(migrator_audit_privs(conn, "during:before-migrate"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "SELECT xt_audit.append(gen_random_uuid(), 'probe', gen_random_uuid(),"
                " 'INSERT', '{}'::jsonb)"
            )
        ran = real(conn)
        probes.extend(migrator_audit_privs(conn, "during:after-migrate"))
        return ran

    with st.su() as su:
        acl = audit_write_acl(su)
        probes.extend(migrator_audit_privs(su, "before"))
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(data_setup, "migrate", probe)
            install_data_prerequisites(su)
        probes.extend(migrator_audit_privs(su, "after"))
        assert audit_write_acl(su) == acl
    return probes


def install_postgrest(st: hs.Stack) -> None:
    """Đường vận hành thật: mật khẩu xt_authenticator (secret), issuer realm, allowlist `azp`."""
    with st.su() as su:
        install_postgrest_role(
            su,
            authenticator_password=st.authenticator_password,
            issuer=hs.ISSUER,
            clients=[hs.DATA_CLIENT],
        )


@pytest.fixture(scope="module")
def env() -> Iterator[Env]:
    if shutil.which("docker") is None:
        pytest.fail("test tích hợp cần docker (postgres:17-alpine + postgrest/postgrest local)")
    ev_path = os.environ.get("XT_IT_HEADLESS_EVIDENCE")
    evidence = hs.Evidence(Path(ev_path) if ev_path else None)
    st = hs.new_stack()
    rest = hs.Rest(f"http://127.0.0.1:{st.rest_port}", evidence)
    app = None
    try:
        with st.su() as su:
            assert bootstrap(
                su,
                migrator_password=st.migrator_password,
                app_password=st.app_password,
                pepper=st.pepper,
            )
        setup_probes = probed_data_setup(st)
        install_postgrest(st)
        key = AuditKey(1, st.pepper)
        app = Database(st.app_dsn, max_size=4, audit_key=key)
        migrator = PgMigrator(st.migrator, lock_timeout="2s", post_lock_timeout="5s", audit_key=key)
        cats = PgCatalogReader(app)
        e = Env(
            st,
            rest,
            app,
            migrator,
            RecordService(cats, PgRecordRepository(app)),
            AuditReader(app),
            setup_probes=setup_probes,
        )
        with st.migrator() as m:
            e.tenants["acme"] = register_tenant(
                m,
                alias="acme",
                name="Công ty Acme",
                timezone="Asia/Ho_Chi_Minh",
                llm_egress="masked",
            )
            e.tenants["globex"] = register_tenant(
                m,
                alias="globex",
                name="Công ty Globex",
                timezone="Asia/Ho_Chi_Minh",
                llm_egress="local_only",
            )
            import_org(m, e.tenants["acme"], ORG_A)
            import_org(m, e.tenants["globex"], ORG_B)
        apply(e, e.tenants["acme"], catalog(1, TICKET))
        apply(e, e.tenants["globex"], catalog(1, TICKET, MEMO))
        alice = e.actor("acme", "alice", "manager", unit="kt")
        bob = e.actor("acme", "bob", "staff", unit="kt")
        dave = e.actor("globex", "dave", "manager")
        recs = {
            "a_alice": e.records.create(
                alice, "ticket",
                {"title": "A-alice-1", "national_id": NATIONAL_ID_A, "phone": "0901234567"},
            ),
            "a_bob": e.records.create(bob, "ticket", {"title": "A-bob-1", "phone": "0907654321"}),
            "b_dave1": e.records.create(
                dave, "ticket", {"title": "B-dave-1", "national_id": NATIONAL_ID_B}
            ),
            "b_dave2": e.records.create(dave, "ticket", {"title": "B-dave-2"}),
            "b_memo": e.records.create(dave, "memo", {"body": "B-memo-1"}),
        }  # fmt: skip
        e.ids = {k: uuid.UUID(v["record"]["id"]) for k, v in recs.items()}
        st.start_rest()
        evidence.write(
            f"# Bằng chứng /data — tests/integration/test_headless_data.py\n\n"
            f"Chạy lúc {time.strftime('%Y-%m-%d %H:%M:%S %z')}; image: "
            + ", ".join(f"`{k}` ({v[:19]})" for k, v in st.image_ids.items())
            + f"; acme = `{e.profile('acme')}`, globex = `{e.profile('globex')}`.\n\n"
        )
        yield e
    finally:
        if st.rest_started and os.environ.get("XT_IT_HEADLESS_LOGS"):
            print(st.rest_logs())
        rest.close()
        if app is not None:
            app.close()
        st.stop()


# --------------------------------------------------------------------------- tests ----
def test_roles_and_functions_are_locked_down(env: Env) -> None:
    with env.st.su() as su:
        version = su.execute("SHOW server_version_num").fetchone()
        assert version is not None and int(version[0]) // 10000 == 17, version
        auth = su.execute(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb"
            " FROM pg_roles WHERE rolname = %s",
            (hs.AUTHENTICATOR,),
        ).fetchone()
        assert auth == (True, False, False, False, False, False)

        def member_of(role: str) -> set[str]:
            return {
                str(r[0])
                for r in su.execute(
                    "SELECT g.rolname FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid"
                    " JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s",
                    (role,),
                )
            }

        assert member_of(hs.AUTHENTICATOR) == {hs.WEB_ROLE}
        assert member_of(hs.WEB_ROLE) == set()
        web = su.execute(
            "SELECT rolcanlogin, rolbypassrls FROM pg_roles WHERE rolname = 'xt_web'"
        ).fetchone()
        assert web == (False, False)
        fns = su.execute(
            "SELECT p.proname, p.prosecdef, p.proowner::regrole::text, p.proconfig,"
            " coalesce(bool_or(a.grantee = 0), false)"
            " FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace"
            " LEFT JOIN LATERAL aclexplode(p.proacl) a ON true"
            " WHERE n.nspname = 'xt' AND p.proname IN ('pgrst_pre', 'pgrst_gate', 'pgrst_config')"
            " GROUP BY 1, 2, 3, 4"
        ).fetchall()
        by = {r[0]: r for r in fns}
        assert set(by) == {"pgrst_pre", "pgrst_gate", "pgrst_config"}
        for name in ("pgrst_gate", "pgrst_config"):
            _, secdef, owner, cfg, public = by[name]
            assert secdef and owner == "xt_migrator" and not public
            assert any(c.startswith("search_path=") for c in cfg or [])
        assert by["pgrst_pre"][1] and not by["pgrst_pre"][4]
        priv = su.execute(
            "SELECT has_function_privilege('xt_web', 'xt.set_context(uuid, uuid, text[], uuid[],"
            " uuid[], uuid, text, text)', 'EXECUTE'),"
            " has_function_privilege(%s, 'xt.pgrst_gate(text, text)', 'EXECUTE'),"
            " has_function_privilege('xt_app', 'xt.pgrst_gate(text, text)', 'EXECUTE')",
            (hs.AUTHENTICATOR,),
        ).fetchone()
        assert priv == (False, False, False)


def test_reinstall_removes_privileged_role_drift(env: Env) -> None:
    """Nâng cấp DB phải thu quyền role bị cấu hình lệch, kể cả ADMIN/INHERIT của membership."""
    with env.st.su() as su, su.transaction(force_rollback=True):
        grantor = sql.Identifier("it_data_grant_" + uuid.uuid4().hex[:8])
        su.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(grantor))
        su.execute(sql.SQL("GRANT xt_app TO {} WITH ADMIN TRUE").format(grantor))
        su.execute(
            "ALTER ROLE xt_web LOGIN INHERIT SUPERUSER CREATEROLE CREATEDB REPLICATION BYPASSRLS"
        )
        su.execute("GRANT xt_app TO xt_authenticator WITH ADMIN TRUE, INHERIT TRUE, SET TRUE")
        # PostgreSQL 17 lưu nhiều grantor cho cùng cặp role/member: thu quyền chỉ dưới một
        # grantor sẽ bỏ sót đường leo quyền còn lại.
        su.execute(sql.SQL("GRANT xt_app TO xt_authenticator GRANTED BY {}").format(grantor))
        su.execute("GRANT xt_workflow TO xt_web WITH ADMIN TRUE, INHERIT TRUE, SET TRUE")
        su.execute("GRANT xt_web TO xt_authenticator WITH ADMIN TRUE, INHERIT TRUE, SET TRUE")
        install_postgrest_role(
            su,
            authenticator_password=env.st.authenticator_password,
            issuer=hs.ISSUER,
            clients=[hs.DATA_CLIENT],
        )
        assert su.execute(
            "SELECT rolcanlogin, rolinherit, rolsuper, rolcreaterole, rolcreatedb,"
            " rolreplication, rolbypassrls FROM pg_roles WHERE rolname = 'xt_web'"
        ).fetchone() == (False, False, False, False, False, False, False)
        memberships = su.execute(
            "SELECT member.rolname, parent.rolname, m.admin_option, m.inherit_option, m.set_option"
            " FROM pg_auth_members m"
            " JOIN pg_roles member ON member.oid = m.member"
            " JOIN pg_roles parent ON parent.oid = m.roleid"
            " WHERE member.rolname IN ('xt_web', 'xt_authenticator')"
        ).fetchall()
        assert memberships == [(hs.AUTHENTICATOR, hs.WEB_ROLE, False, False, True)], memberships


def test_authenticator_and_web_cannot_escalate_in_sql(env: Env) -> None:
    a = env.tenants["acme"]
    with env.st.connect(hs.AUTHENTICATOR, env.st.authenticator_password) as c:
        for target in ("xt_app", "xt_workflow", "xt_api_owner", "xt_migrator", "xt_owner"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(target)))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "SELECT xt.set_context(%s, NULL, '{manager}', '{}', '{}', NULL, NULL, NULL)",
                (a.id,),
            )
        c.execute("SET ROLE xt_web")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "SELECT xt.set_context(%s, NULL, '{manager}', '{}', '{}', NULL, NULL, NULL)",
                (a.id,),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(
                "INSERT INTO xt_meta.session_ctx VALUES (pg_backend_pid(), pg_current_xact_id(),"
                " %s, NULL, '{manager}', '{}', '{}', NULL, NULL, NULL)",
                (a.id,),
            )
        for q in (f"SELECT * FROM xt_t{a.seq}.ticket", "SELECT * FROM xt_audit.event"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(q)
        for target in ("xt_app", "xt_workflow", "xt_api_owner"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(target)))
        # xt_web không có ngữ cảnh => view trả 0 dòng (fail-closed), kể cả khi tự đặt GUC
        c.execute("SELECT set_config('xt.tenant_id', %s, false)", (str(a.id),))
        row = c.execute(f"SELECT count(*) FROM api_t{a.seq}.ticket").fetchone()
        assert row is not None and row[0] == 0
        # gọi pre-request ngoài PostgREST (không claim) => từ chối
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT xt.pgrst_pre()")

    # Login BI gắn tenant không mượn được đường PostgREST (không là thành viên xt_web)
    pw = "bi-" + uuid.uuid4().hex
    with env.st.su() as su:
        su.execute(sql.SQL("CREATE ROLE it_bi_acme LOGIN PASSWORD {}").format(sql.Literal(pw)))
    with env.st.migrator() as m:
        bind_bi_login(m, "it_bi_acme", a, roles=("manager",))
    with env.st.connect("it_bi_acme", pw) as bi:
        forged = hs.data_claims(["globex"], "kc-dave")
        bi.execute("SELECT set_config('request.jwt.claims', %s, false)", (json.dumps(forged),))
        for q in (
            "SELECT xt.pgrst_pre()",
            "SELECT xt.pgrst_gate('api_t1', 'xt_web')",
            "SET ROLE xt_web",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                bi.execute(q)
        b = env.tenants["globex"]
        bi.execute("SELECT set_config(%s, %s, false)", (PGRST_CTX_GUC, forged_ctx(env, "globex")))
        row = bi.execute(f"SELECT count(*) FROM api_t{a.seq}.ticket").fetchone()
        assert row is not None and row[0] == 2  # gắn cố định tenant acme, role manager
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            bi.execute(f"SELECT count(*) FROM api_t{b.seq}.ticket")
    # Runtime xt_app tự đặt GUC ngữ cảnh của nhánh PostgREST: xt.cur() bỏ qua (khác session_user)
    with env.st.connect("xt_app", env.st.app_password) as app, app.transaction():
        app.execute("SELECT set_config(%s, %s, true)", (PGRST_CTX_GUC, forged_ctx(env, "acme")))
        assert app.execute("SELECT (xt.cur()).tenant_id").fetchone() == (None,)
        row = app.execute(f"SELECT count(*) FROM xt_t{a.seq}.ticket").fetchone()
        assert row == (0,)


def test_own_tenant_reads_follow_xt_roles(env: Env) -> None:
    pa = env.profile("acme")
    r = env.get(
        ("acme", "kc-alice"),
        "/ticket?select=title,state,phone&order=title",
        profile=pa,
        token_label="TOKEN_ACME_ALICE",
        record=True,
    )
    assert r.rows == [
        {"title": "A-alice-1", "state": "open", "phone": "0901234567"},
        {"title": "A-bob-1", "state": "open", "phone": "0907654321"},
    ]
    # staff: phạm vi own (owner_id = person.id của sub)
    r = env.get(
        ("acme", "kc-bob"),
        "/ticket?select=title",
        profile=pa,
        token_label="TOKEN_ACME_BOB",
        record=True,
    )
    assert titles(r) == {"A-bob-1"}
    # người không có vai trò: không thấy gì (RLS), không lỗi
    r = env.get(("acme", "kc-carol"), "/ticket?select=title", profile=pa)
    assert r.rows == []
    pb = env.profile("globex")
    r = env.get(
        ("globex", "kc-dave"),
        "/ticket?select=title&order=title",
        profile=pb,
        token_label="TOKEN_GLOBEX_DAVE",
        record=True,
    )
    assert [x["title"] for x in r.rows] == ["B-dave-1", "B-dave-2"]
    assert env.get(("globex", "kc-dave"), "/memo?select=body", profile=pb).rows == [
        {"body": "B-memo-1"}
    ]
    # tenant khác model: acme không có memo
    r = env.get(("acme", "kc-alice"), "/memo", profile=pa)
    assert r.status == 404, r.body
    # dạng claim Keycloak khác: aud mảng, organization chuỗi / object {alias: {...}}
    for org, aud in (("acme", ["xt-data", "account"]), ({"acme": {"id": "kc-org-1"}}, "xt-data")):
        r = env.get(env.token(org, "kc-alice", aud=aud), "/ticket?select=title", profile=pa)
        assert titles(r) == {"A-alice-1", "A-bob-1"}


def test_cross_tenant_token_and_profile_rejected(env: Env) -> None:
    pa, pb = env.profile("acme"), env.profile("globex")
    r = env.get(
        ("acme", "kc-alice"), "/ticket", profile=pb, token_label="TOKEN_ACME_ALICE", record=True
    )
    assert_denied(r, "profile_mismatch", (403,))
    assert_denied(env.get(("globex", "kc-dave"), "/ticket", profile=pa), "profile_mismatch")
    # thiếu Accept-Profile: rơi vào xt_api_none (schema đầu danh sách), không vào tenant nào
    for who in (("acme", "kc-alice"), ("globex", "kc-dave")):
        r = env.get(who, "/ticket", record=who[0] == "acme", token_label="TOKEN_ACME_ALICE")
        assert r.status in (*DENIED, 404), r.body
        assert not isinstance(r.body, list), r.body
    # xt_api_none không có bảng nào: 404 trước pre-request, hoặc pre-request chặn
    assert_denied(
        env.get(("acme", "kc-alice"), "/ticket", profile=hs.DEFAULT_SCHEMA), codes=(*DENIED, 404)
    )
    # sub của acme nhưng đổi organization sang globex: không phải người của globex
    assert_denied(env.get(("globex", "kc-alice"), "/ticket", profile=pb), "invalid_behalf")
    # schema ngoài danh sách db-schemas: bảng gốc, lõi, audit, catalog hệ thống
    seq = env.tenants["acme"].seq
    for prof in (
        f"xt_t{seq}",
        "xt",
        "xt_meta",
        "xt_audit",
        "public",
        "pg_catalog",
        "information_schema",
        f"api_t{seq + 1000}",
    ):
        r = env.get(
            ("acme", "kc-alice"),
            "/ticket",
            profile=prof,
            record=prof == "xt_meta",
            token_label="TOKEN_ACME_ALICE",
        )
        assert r.status == 406 and r.code == "PGRST106", (prof, r.status, r.body)


def test_head_preserves_tenant_scope_and_sensitive_column_boundary(env: Env) -> None:
    """HEAD chạy READ ONLY như GET; count cũng không được trở thành oracle chéo tenant."""
    pa, pb = env.profile("acme"), env.profile("globex")
    alice, bob = env.token("acme", "kc-alice"), env.token("acme", "kc-bob")
    for token, total in ((alice, 2), (bob, 1)):
        resp = env.rest.call(
            "HEAD",
            "/ticket?select=title",
            token=token,
            profile=pa,
            headers={"Prefer": "count=exact"},
        )
        assert resp.status == 200 and resp.body is None, (resp.status, resp.body)
        assert resp.headers["content-range"].endswith(f"/{total}"), resp.headers
    for token, profile in ((alice, pb), (env.token("globex", "kc-dave"), pa), (None, pa)):
        resp = env.rest.call(
            "HEAD",
            "/ticket",
            token=token,
            profile=profile,
            headers={"Prefer": "count=exact"},
        )
        assert resp.status in DENIED, (resp.status, resp.headers)
        assert resp.body is None and "content-range" not in resp.headers, resp.headers
    resp = env.rest.call("HEAD", "/ticket?select=national_id", token=alice, profile=pa)
    assert resp.status == 400 and resp.body is None, (resp.status, resp.headers)


def test_audience_and_signature_enforced(env: Env) -> None:
    pa = env.profile("acme")
    for aud in ("xt-mcp", "https://xt-mcp.freepro.online/mcp", ["account"], None):
        r = env.get(
            env.token("acme", "kc-alice", aud=aud),
            "/ticket",
            profile=pa,
            record=aud == "xt-mcp",
            token_label="TOKEN_AUD_XT_MCP",
        )
        assert_denied(r)
        if aud is not None:
            assert r.status == 401, (aud, r.body)
    rogue = hs.Signer.new(env.st.signer.kid)  # cùng kid, khóa khác
    bad = [
        rogue.sign(hs.data_claims("acme", "kc-alice")),
        hs.Signer.new().sign(hs.data_claims("acme", "kc-alice")),
        env.token("acme", "kc-alice", exp=int(time.time()) - 60),
        "not.a.jwt",
    ]
    for tok in bad:
        r = env.get(tok, "/ticket", profile=pa)
        assert r.status == 401, r.body

    # alg=none và HS256 ký bằng khóa công khai (alg confusion)
    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    claims = b64(json.dumps(hs.data_claims("acme", "kc-alice")).encode())
    none = f"{b64(json.dumps({'alg': 'none', 'typ': 'JWT'}).encode())}.{claims}."
    hs_head = b64(json.dumps({"alg": "HS256", "kid": env.st.signer.kid}).encode())
    pub = json.dumps(env.st.signer.jwk()).encode()
    sig = b64(hmac.new(pub, f"{hs_head}.{claims}".encode(), hashlib.sha256).digest())
    for tok in (none, f"{hs_head}.{claims}.{sig}"):
        assert env.get(tok, "/ticket", profile=pa).status == 401
    # không token: không có anon role (401); nếu cấu hình anon thì pre-request vẫn chặn
    r = env.rest.get("/ticket", profile=pa, record=True)
    assert_denied(r)
    # thiếu claim role (`data_role` do mapper scope xt-data phát hành) => không có role => 401
    no_role = hs.data_claims("acme", "kc-alice")
    no_role.pop(hs.ROLE_CLAIM_KEY.lstrip("."), None)
    assert_denied(env.get(env.st.signer.sign(no_role), "/ticket", profile=pa))
    # PostgREST không kiểm iss/azp: pre-request so với issuer + allowlist client đã cài
    r = env.get(
        env.token("acme", "kc-alice", iss="https://evil.example/realms/xt"),
        "/ticket",
        profile=pa,
        record=True,
        token_label="TOKEN_OTHER_ISSUER",
    )
    assert_denied(r, "issuer", (403,))
    for azp in ("claude-connector", "xt-desk-agent", None):
        r = env.get(env.token("acme", "kc-alice", azp=azp), "/ticket", profile=pa)
        assert_denied(r, "client_not_allowed", (403,))
    # refresh/ID token, thiếu sub
    assert_denied(
        env.get(env.token("acme", "kc-alice", typ="Refresh"), "/ticket", profile=pa), "token_type"
    )
    assert_denied(env.get(env.token("acme", None), "/ticket", profile=pa), "missing_claim:sub")


@pytest.mark.parametrize(
    ("org", "hint"),
    [
        (None, "missing_tenant"),
        ([], "missing_tenant"),
        ({}, "missing_tenant"),
        (["acme", "globex"], "tenant_ambiguous"),
        ({"acme": {}, "globex": {}}, "tenant_ambiguous"),
        ([1], "bad_claim:organization"),
        ([["acme"]], "bad_claim:organization"),
        (42, "bad_claim:organization"),
        (True, "bad_claim:organization"),
        ("ACME", ("bad_claim:organization", "unknown_tenant")),
        ("acme' OR '1'='1", ("bad_claim:organization", "unknown_tenant")),
        (["acme;globex"], ("bad_claim:organization", "unknown_tenant")),
        (["nope"], "unknown_tenant"),
    ],
)
def test_malformed_tenant_claims_rejected(env: Env, org: Any, hint: str | tuple[str, ...]) -> None:
    r = env.get(env.token(org, "kc-alice"), "/ticket", profile=env.profile("acme"))
    assert_denied(r, hint, (403,))


def test_unknown_or_inactive_person_rejected(env: Env) -> None:
    pa = env.profile("acme")
    assert_denied(env.get(("acme", "kc-ghost"), "/ticket", profile=pa), "invalid_behalf")
    assert_denied(env.get(("acme", "kc-frank"), "/ticket", profile=pa), "invalid_behalf")


def test_role_escalation_via_token_claims_blocked(env: Env) -> None:
    """Token tự khai role DB / vai trò nghiệp vụ: không bao giờ thấy nhiều hơn phạm vi của bob."""
    pa = env.profile("acme")
    evil: dict[str, Any] = {
        "role": "xt_migrator",
        "data_role": "xt_app",
        "realm_access": {"roles": ["manager", "admin", "xt_workflow"]},
        "resource_access": {"xt-data": {"roles": ["xt_api_owner"]}},
        "roles": ["manager"],
        "tenant_id": str(env.tenants["globex"].id),
        "tenant_seq": env.tenants["globex"].seq,
    }
    claims = hs.data_claims("acme", "kc-bob", **evil)
    # Giữ role transport hợp lệ để request thật sự vào pre-request/RLS: mọi vai trò nghiệp vụ
    # tự khai trong JWT vẫn bị bỏ qua; bob chỉ đọc bản ghi của mình trong bảng tổ chức xT.
    legitimate_transport = copy.deepcopy(claims)
    hs.put_claim(legitimate_transport, hs.ROLE_CLAIM_KEY, hs.WEB_ROLE)
    r = env.get(env.st.signer.sign(legitimate_transport), "/ticket?select=title", profile=pa)
    assert titles(r) == {"A-bob-1"}, r.body
    for role in ("xt_app", "xt_workflow", "xt_api_owner", "xt_migrator", "xt_owner", "postgres"):
        c = copy.deepcopy(claims)
        hs.put_claim(c, hs.ROLE_CLAIM_KEY, role)
        r = env.get(
            env.st.signer.sign(c),
            "/ticket?select=title",
            profile=pa,
            record=role == "xt_migrator",
            token_label="TOKEN_ACME_BOB_EVIL_ROLE",
        )
        # SET ROLE thất bại: 42501 (403) hoặc role không tồn tại 22023 (400)
        if r.status == 200:
            assert titles(r) == {"A-bob-1"}, (role, r.body)
        else:
            assert_denied(r, codes=(400, *DENIED))
    # cũng không đọc được tenant mà claim tenant_seq trỏ tới
    assert_denied(env.get(env.st.signer.sign(claims), "/ticket", profile=env.profile("globex")))


def test_writes_and_workflow_managed_fields_denied_over_data(env: Env) -> None:
    pa = env.profile("acme")
    rid = env.ids["a_bob"]
    alice = env.token("acme", "kc-alice")  # manager: model cho phép update nhưng /data chỉ đọc
    attempts = [
        ("PATCH", f"/ticket?id=eq.{rid}", {"state": "closed"}),
        ("PATCH", f"/ticket?id=eq.{rid}", {"title": "hacked"}),
        ("POST", "/ticket", {"title": "injected", "state": "closed"}),
        ("PUT", f"/ticket?id=eq.{rid}", {"id": str(rid), "title": "x", "state": "closed"}),
        ("DELETE", f"/ticket?id=eq.{rid}", None),
    ]
    for method, path, body in attempts:
        r = env.rest.call(
            method,
            path,
            token=alice,
            content_profile=pa,
            json_body=body,
            record=method == "PATCH" and body == {"state": "closed"},
            token_label="TOKEN_ACME_ALICE",
        )
        if method == "PUT":  # PostgREST có thể từ chối PUT (view không PK) trước pre-request
            assert r.status in WRITE_DENIED, (method, r.status, r.body)
        else:
            assert_denied(r, "read_only_api", (403,))
        # thiếu Content-Profile => schema mặc định xt_api_none / pre-request chặn
        r = env.rest.call(method, path, token=alice, json_body=body)
        assert r.status in WRITE_DENIED, (method, r.status, r.body)
    # RPC không lộ (hàm nội bộ không nằm trong api_t<seq>; pre-request chặn /rpc)
    for fn in ("pgrst_pre", "pgrst_gate", "set_context", "pgrst_config"):
        r = env.rest.call("POST", f"/rpc/{fn}", token=alice, content_profile=pa, json_body={})
        assert r.status in (403, 404), (fn, r.status, r.body)
        r = env.get(alice, f"/rpc/{fn}", profile=pa)
        assert r.status in (403, 404), (fn, r.status, r.body)
    with env.st.su() as su:
        row = su.execute(
            sql.SQL("SELECT title, state, revision FROM {} WHERE id = %s").format(
                sql.Identifier(f"xt_t{env.tenants['acme'].seq}", "ticket")
            ),
            (rid,),
        ).fetchone()
        n = su.execute(
            sql.SQL("SELECT count(*) FROM {}").format(
                sql.Identifier(f"xt_t{env.tenants['acme'].seq}", "ticket")
            )
        ).fetchone()
    assert row == ("A-bob-1", "open", 1) and n == (2,)
    # Đường CRUD của xT cũng không ghi được trường workflow; chỉ workflow_write
    with pytest.raises(ModelError):
        env.records.update(
            env.actor("acme", "alice", "manager", unit="kt"), "ticket", rid, 1, {"state": "closed"}
        )
    wf = env.actor("acme", "alice", "xt_workflow")
    out = env.records.workflow_write(wf, "ticket", rid, 1, {"state": "closed"})["record"]
    assert (out["state"], out["revision"]) == ("closed", 2)
    r = env.get(
        alice,
        f"/ticket?select=title,state,revision&id=eq.{rid}",
        profile=pa,
        token_label="TOKEN_ACME_ALICE",
        record=True,
    )
    assert r.rows == [{"title": "A-bob-1", "state": "closed", "revision": 2}]


def test_sensitive_pii_never_exposed_audit_hmac_only(env: Env) -> None:
    pa = env.profile("acme")
    alice = env.token("acme", "kc-alice")
    rows = env.get(alice, "/ticket", profile=pa, token_label="TOKEN_ACME_ALICE", record=True).rows
    assert rows and all("national_id" not in r for r in rows)
    assert all("phone" in r for r in rows)  # basic: hiển thị theo quyết định v0.3
    assert NATIONAL_ID_A not in str(rows)
    for path in (
        "/ticket?select=national_id",
        f"/ticket?national_id=eq.{NATIONAL_ID_A}",
        "/ticket?order=national_id",
        "/ticket?select=title,nid:national_id",
    ):
        r = env.get(
            alice,
            path,
            profile=pa,
            record=path.endswith("national_id"),
            token_label="TOKEN_ACME_ALICE",
        )
        assert r.status == 400, (path, r.status, r.body)
        assert NATIONAL_ID_A not in str(r.body)
    # audit chỉ giữ HMAC có khóa cho field sensitive
    manager = env.actor("acme", "alice", "manager", unit="kt")
    events = env.audit.events(manager, env.ids["a_alice"])
    stored = events[0]["diff"]["new"]["national_id"]
    assert stored.startswith("hmac:1:") and NATIONAL_ID_A not in str(events)
    assert verify_hmac(env.st.pepper, 1, NATIONAL_ID_A, stored)
    assert not verify_hmac(env.st.pepper, 1, NATIONAL_ID_B, stored)
    assert env.audit.verify(manager).intact
    # bản ghi globex (cũng có CCCD) không lộ qua token acme ở bất kỳ profile nào
    for prof in (pa, env.profile("globex")):
        assert NATIONAL_ID_B not in str(env.get(alice, "/ticket", profile=prof).body)


def test_real_curl_transcript(env: Env, tmp_path: Path) -> None:
    """curl thật trên stack thật. Token chỉ nằm trong tệp header 0600 (`-H @tệp`), không bao giờ
    trên argv; transcript ghi vào bằng chứng đã thay token bằng `$TOKEN_<NHÃN>`."""
    curl = shutil.which("curl")
    if curl is None:
        pytest.fail("cần curl để ghi transcript thật")
    pa, pb = env.profile("acme"), env.profile("globex")
    alice = env.token("acme", "kc-alice")
    json_ct = "application/json"
    cases: list[tuple[str | None, str | None, str, str, dict[str, str], str | None, int]] = [
        ("TOKEN_ACME_ALICE", alice, "GET", "/ticket?select=title&order=title",
         {"Accept-Profile": pa}, None, 200),
        ("TOKEN_ACME_ALICE", alice, "GET", "/ticket?select=title", {"Accept-Profile": pb}, None,
         403),
        ("TOKEN_AUD_XT_MCP", env.token("acme", "kc-alice", aud="xt-mcp"), "GET", "/ticket",
         {"Accept-Profile": pa}, None, 401),
        ("TOKEN_ACME_ALICE", alice, "PATCH", f"/ticket?id=eq.{env.ids['a_bob']}",
         {"Content-Profile": pa, "Content-Type": json_ct}, '{"state":"open"}', 403),
        (None, None, "GET", "/ticket", {"Accept-Profile": pa}, None, 401),
    ]  # fmt: skip
    env.rest.evidence.write("## curl thật\n\n")
    for i, (label, tok, method, path, headers, body, want) in enumerate(cases):
        hdr = tmp_path / f"headers-{i}"
        lines = [f"{k}: {v}" for k, v in headers.items()]
        if tok is not None:
            lines.append(f"Authorization: Bearer {tok}")
        hdr.touch(mode=0o600)
        hdr.write_text("\n".join(lines) + "\n", encoding="utf-8")
        argv = [curl, "-sS", "-i", "-X", method, "-H", f"@{hdr}", env.rest.base + path]
        if body is not None:
            argv += ["--data", body]
        assert tok is None or not any(tok in a for a in argv)
        out = subprocess.run(argv, capture_output=True, text=True, timeout=20, check=True).stdout
        head, _, payload = out.replace("\r\n", "\n").partition("\n\n")
        status = int(head.split()[1])
        assert status == want, (method, path, out)
        if want == 200:
            assert [r["title"] for r in json.loads(payload)] == ["A-alice-1", "A-bob-1"]
        else:
            assert "A-alice-1" not in payload and "A-bob-1" not in payload
        shown = out.replace(tok, f"${label}") if tok else out
        assert tok is None or tok not in shown
        hdr_shown = [
            ln if not ln.startswith("Authorization:") else f"Authorization: Bearer ${label}"
            for ln in lines
        ]
        data = f" --data '{body}'" if body is not None else ""
        env.rest.evidence.write(
            "```console\n"
            + "".join(f"$ echo '{ln}' >> \"$HDR_FILE\"\n" for ln in hdr_shown)
            + f'$ curl -sS -i -X {method} -H @"$HDR_FILE" "$XT_DATA_URL{path}"{data}\n'
            + shown.replace("\r\n", "\n").rstrip()
            + "\n```\n\n"
        )


def test_transaction_context_isolation_on_single_connection(env: Env) -> None:
    """PGRST_DB_POOL=1: mọi request đi qua CÙNG một backend; ngữ cảnh request trước (kể cả request
    ghi đã đặt session_ctx) không được rò sang request sau, dù request sau lỗi hay thiếu token."""
    pa, pb = env.profile("acme"), env.profile("globex")
    alice, bob = env.token("acme", "kc-alice"), env.token("acme", "kc-bob")
    dave, carol = env.token("globex", "kc-dave"), env.token("acme", "kc-carol")
    A_ALL, A_BOB, B_ALL = {"A-alice-1", "A-bob-1"}, {"A-bob-1"}, {"B-dave-1", "B-dave-2"}
    steps: list[tuple[str, Any, str | None, set[str] | None]] = [
        ("GET", alice, pa, A_ALL),
        ("PATCH", alice, pa, None),  # transaction ghi: set_context chạy, rồi bị từ chối
        ("GET", bob, pa, A_BOB),
        ("GET", dave, pb, B_ALL),
        ("GET", alice, pb, None),
        ("GET", carol, pa, set()),
        ("GET", None, pa, None),
        ("GET", "garbage", pa, None),
        ("GET", env.token(["nope"], "kc-alice"), pa, None),
        ("GET", bob, pa, A_BOB),
        ("GET", dave, pb, B_ALL),
        ("GET", carol, pa, set()),
    ]
    for _ in range(3):
        for method, tok, prof, want in steps:
            if method == "PATCH":
                r = env.rest.call(
                    method,
                    "/ticket?id=eq." + str(env.ids["a_alice"]),
                    token=tok,
                    content_profile=prof,
                    json_body={"title": "x"},
                )
            else:
                r = env.rest.get("/ticket?select=title", token=tok, profile=prof)
            if want is None:
                assert_denied(r)
            else:
                assert titles(r) == want, (method, prof, r.status, r.body)

    def one(i: int) -> bool:
        tok, prof, want = [
            (alice, pa, A_ALL),
            (bob, pa, A_BOB),
            (dave, pb, B_ALL),
            (dave, pa, None),
            (carol, pa, set()),
        ][i % 5]
        r = env.rest.get("/ticket?select=title", token=tok, profile=prof)
        return (r.status in DENIED) if want is None else (r.status == 200 and titles(r) == want)

    with ThreadPoolExecutor(8) as ex:
        assert all(ex.map(one, range(60)))
    # transaction mới trên kết nối khác của xt_web: không thừa hưởng ngữ cảnh nào
    with env.st.connect(hs.AUTHENTICATOR, env.st.authenticator_password) as c:
        c.execute("SET ROLE xt_web")
        for t in env.tenants.values():
            row = c.execute(f"SELECT count(*) FROM api_t{t.seq}.ticket").fetchone()
            assert row == (0,)


def test_dynamic_tenant_and_model_reload_while_running(env: Env) -> None:
    """Server đang chạy: tạo tenant + model (NOTIFY reload config/schema) → đọc được; thêm/xóa
    model; xóa tenant → profile biến mất khỏi db-schemas."""
    with env.st.migrator() as m:
        c = register_tenant(
            m,
            alias="initech",
            name="Công ty Initech",
            timezone="Asia/Ho_Chi_Minh",
            llm_egress="masked",
        )
        import_org(m, c, ORG_C)
    env.tenants["initech"] = c
    pc = f"api_t{c.seq}"
    erin = env.token("initech", "kc-erin")
    hs.wait_until(lambda: env.get(erin, "/asset", profile=pc).status, lambda s: s != 406)
    apply(env, c, catalog(1, ASSET))
    erin_actor = env.actor("initech", "erin", "manager")
    env.records.create(erin_actor, "asset", {"body": "C-asset-1"})
    r = hs.wait_until(
        lambda: env.get(erin, "/asset?select=body", profile=pc),
        lambda x: x.status == 200 and x.body == [{"body": "C-asset-1"}],
    )
    env.rest.evidence.record(
        "GET", "/asset?select=body", {"Accept-Profile": pc}, "TOKEN_INITECH_ERIN", None, r
    )
    # tenant mới cũng bị cô lập hai chiều
    assert_denied(env.get(erin, "/ticket", profile=env.profile("acme")), "profile_mismatch")
    assert_denied(env.get(("acme", "kc-alice"), "/asset", profile=pc), "profile_mismatch")
    # thêm model khi server đang chạy
    apply(env, c, catalog(2, ASSET, ASSET_NOTE))
    env.records.create(erin_actor, "asset_note", {"body": "C-note-1"})
    hs.wait_until(
        lambda: env.get(erin, "/asset_note?select=body", profile=pc),
        lambda x: x.status == 200 and x.body == [{"body": "C-note-1"}],
    )
    # xóa model (DESTRUCTIVE đã xác nhận) → view biến mất khỏi schema cache
    apply(env, c, catalog(3, ASSET))
    r = hs.wait_until(lambda: env.get(erin, "/asset_note", profile=pc), lambda x: x.status == 404)
    env.rest.evidence.record(
        "GET", "/asset_note", {"Accept-Profile": pc}, "TOKEN_INITECH_ERIN", None, r
    )
    assert titles_body(env.get(erin, "/asset?select=body", profile=pc)) == {"C-asset-1"}
    # xóa tenant → schema rời db-schemas; token cũ không còn tenant
    with env.st.migrator() as m:
        drop_tenant(m, c)
    r = hs.wait_until(lambda: env.get(erin, "/asset", profile=pc), lambda x: x.status == 406)
    env.rest.evidence.record("GET", "/asset", {"Accept-Profile": pc}, "TOKEN_INITECH_ERIN", None, r)
    del env.tenants["initech"]
    with env.st.su() as su:
        assert su.execute(
            "SELECT count(*) FROM pg_namespace WHERE nspname IN (%s, %s)", (pc, f"xt_t{c.seq}")
        ).fetchone() == (0,)
    # tenant còn lại không bị ảnh hưởng
    assert titles(
        env.get(("acme", "kc-alice"), "/ticket?select=title", profile=env.profile("acme"))
    ) == {"A-alice-1", "A-bob-1"}


def titles_body(resp: hs.Resp) -> set[str]:
    return {r["body"] for r in resp.rows}


def test_audited_context_logic_preserved_and_web_cannot_set_it(env: Env) -> None:
    """Điểm nối READ ONLY không sửa thân hàm RBAC đã audit, không mở setter ra cho client."""
    core = Path("src/xt/adapters/db/sql/bootstrap.sql").read_text(encoding="utf-8")
    with env.st.su() as su:
        for signature in (
            "xt.backend_cur()",
            "xt.set_context(uuid,uuid,text[],uuid[],uuid[],uuid,text,text)",
        ):
            row = su.execute(
                "SELECT prosrc FROM pg_proc WHERE oid = %s::regprocedure", (signature,)
            ).fetchone()
            assert row is not None and row[0].strip() in core
        for who in ("xt_web", hs.AUTHENTICATOR):
            assert su.execute(
                "SELECT has_function_privilege(%s, 'xt.set_context(jsonb)', 'EXECUTE')", (who,)
            ).fetchone() == (False,)
    # Custom GUC vẫn đặt được bởi SQL client, nhưng chỉ session authenticator được dùng kênh này.
    forged = json.dumps({"t": str(env.tenants["globex"].id), "a": None, "r": ["manager"]})
    with env.app.connection() as conn, conn.transaction():
        conn.execute("SELECT set_config('xt.pgrst_ctx', %s, true)", (forged,))
        assert conn.execute("SELECT (xt.cur()).tenant_id").fetchone() == (None,)


def test_existing_database_upgrade_preserves_bi_acl_and_function_bodies(env: Env) -> None:
    """DB trước PostgREST, trong cùng container: nâng cấp không đổi OID/quyền BI/thân RBAC."""
    dbname = "xt_upgrade_" + uuid.uuid4().hex[:8]
    login = "bi_upgrade_" + uuid.uuid4().hex[:8]
    password = uuid.uuid4().hex
    core = Path("src/xt/adapters/db/sql/bootstrap.sql").read_text(encoding="utf-8")

    def connect(user: str, secret: str) -> psycopg.Connection[Any]:
        return psycopg.connect(
            psycopg.conninfo.make_conninfo(env.st.dsn(user, secret), dbname=dbname),
            autocommit=True,
        )

    with env.st.su() as su:
        su.execute(sql.SQL("CREATE DATABASE {} OWNER xt_owner").format(sql.Identifier(dbname)))
        su.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                sql.Identifier(login), sql.Literal(password)
            )
        )
    try:
        with connect("xt_owner", env.st.owner_password) as su:
            # Đây chính là schema trước lớp PostgREST, không thay hàm runtime bằng mock.
            with su.transaction():
                su.execute(core.encode())
            before = su.execute(
                "SELECT oid, prosrc, proacl FROM pg_proc WHERE oid='xt.cur()'::regprocedure"
            ).fetchone()
            old_setter = su.execute(
                "SELECT oid, prosrc, proacl FROM pg_proc WHERE oid="
                "'xt.set_context(uuid,uuid,text[],uuid[],uuid[],uuid,text,text)'::regprocedure"
            ).fetchone()
            assert before is not None
        with connect("xt_migrator", env.st.migrator_password) as m:
            tenant = provision_tenant(m, uuid.uuid4(), "Legacy BI")
            bind_bi_login(m, login, tenant, roles=("manager",))
        with connect(login, password) as bi:
            expected = bi.execute("SELECT (xt.cur()).tenant_id, (xt.cur()).roles").fetchone()
            assert expected == (tenant.id, ["manager"])
        with connect("xt_owner", env.st.owner_password) as su:
            # Ghi ACL sau khi provision login BI, trước khi nâng cấp.
            acl = su.execute(
                "SELECT proacl FROM pg_proc WHERE oid='xt.cur()'::regprocedure"
            ).fetchone()
            for _ in range(2):
                install_postgrest_role(
                    su,
                    authenticator_password=env.st.authenticator_password,
                    issuer=hs.ISSUER,
                    clients=[hs.DATA_CLIENT],
                )
                assert su.execute(
                    "SELECT oid FROM pg_proc WHERE oid='xt.cur()'::regprocedure"
                ).fetchone() == (before[0],)
                assert (
                    su.execute(
                        "SELECT proacl FROM pg_proc WHERE oid='xt.cur()'::regprocedure"
                    ).fetchone()
                    == acl
                )
                assert su.execute(
                    "SELECT prosrc FROM pg_proc WHERE oid='xt.backend_cur()'::regprocedure"
                ).fetchone() == (before[1],)
                assert (
                    su.execute(
                        "SELECT oid, prosrc, proacl FROM pg_proc WHERE oid="
                        "'xt.set_context(uuid,uuid,text[],uuid[],uuid[],uuid,text,text)'::regprocedure"
                    ).fetchone()
                    == old_setter
                )
            with su.transaction(force_rollback=True):
                su.execute("DELETE FROM xt_meta.tenant")
                su.execute("SELECT xt.pgrst_config()")
                assert su.execute("SELECT current_setting('pgrst.db_schemas')").fetchone() == (
                    "xt_api_none",
                )
            # Mất backend_cur không được clone ngược wrapper gây đệ quy khi reinstall.
            with su.transaction(force_rollback=True):
                su.execute("DROP FUNCTION xt.backend_cur()")
                with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
                    install_postgrest_role(
                        su,
                        authenticator_password=env.st.authenticator_password,
                        issuer=hs.ISSUER,
                        clients=[hs.DATA_CLIENT],
                    )
        with connect(login, password) as bi:
            assert (
                bi.execute("SELECT (xt.cur()).tenant_id, (xt.cur()).roles").fetchone() == expected
            )
    finally:
        with env.st.su() as su:
            su.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(dbname)))
            su.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))


def test_data_setup_never_grants_migrator_audit_write(env: Env) -> None:
    """Hồi quy R2-04: lần cài đầu (fixture, migration thật) và lần cài lại đều không lúc nào cho
    xt_migrator EXECUTE xt_audit.log/append; audit metadata (install_meta) vẫn được gắn."""
    for probes in (env.setup_probes, probed_data_setup(env.st)):
        phases = {p for p, _, _ in probes}
        assert phases == {"before", "during:before-migrate", "during:after-migrate", "after"}
        assert [p for p in probes if p[2]] == [], probes
    with env.st.su() as su:
        assert su.execute(
            "SELECT count(*) FROM pg_trigger g JOIN pg_class c ON c.oid = g.tgrelid"
            " WHERE c.relname = 'iam_policy' AND g.tgname = 'xt_audit_meta'"
        ).fetchone() == (1,)


def test_tenant_notifications_only_after_commit(env: Env) -> None:
    """Rollback tenant không gửi NOTIFY; commit gửi đúng config + schema, dùng listener thật."""
    with env.st.migrator() as listener, env.st.migrator() as migrator:
        listener.execute("LISTEN pgrst")
        with migrator.transaction(force_rollback=True):
            provision_tenant(migrator, uuid.uuid4(), "Rollback notification")
        assert list(listener.notifies(timeout=0.2, stop_after=1)) == []
        tenant = provision_tenant(migrator, uuid.uuid4(), "Commit notification")
        try:
            messages = list(listener.notifies(timeout=2, stop_after=2))
            assert [n.payload for n in messages] == ["reload config", "reload schema"]
        finally:
            drop_tenant(migrator, tenant)


def test_administrative_setup_failure_rolls_back_ddl_and_privileges(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lỗi SQL thật giữa migration: DDL rollback, ACL hàm ghi audit không đổi, role trở lại."""
    table = "it_rollback_" + uuid.uuid4().hex[:8]

    def failing_migration(conn: psycopg.Connection[Any]) -> list[str]:
        conn.execute(sql.SQL("CREATE TABLE xt_meta.{} (id int)").format(sql.Identifier(table)))
        conn.execute("SELECT 1 / 0")
        raise AssertionError("SQL phải báo lỗi")

    monkeypatch.setattr(data_setup, "migrate", failing_migration)
    with env.st.su() as su:
        before = audit_write_acl(su)
        with pytest.raises(psycopg.errors.DivisionByZero):
            install_data_prerequisites(su)
        assert su.execute("SELECT current_user").fetchone() == ("xt_owner",)
        assert audit_write_acl(su) == before
        assert [p for p in migrator_audit_privs(su, "after") if p[2]] == []
        assert su.execute("SELECT to_regclass(%s)", (f"xt_meta.{table}",)).fetchone() == (None,)
    with env.st.connect(hs.AUTHENTICATOR, env.st.authenticator_password) as conn:
        conn.execute("SET ROLE xt_web")
        # Không pre-request ở transaction mới: không có ngữ cảnh, dù pool đã phục vụ tenant trước.
        assert conn.execute("SELECT (xt.cur()).tenant_id").fetchone() == (None,)
