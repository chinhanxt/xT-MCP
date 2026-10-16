# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# ruff: noqa: F811  (fixture cluster/envs dùng lại từ vòng 3)
"""Audit Model Engine vòng 4 (docs/audit-model-engine.md "Vòng 4") trên Postgres 17 thật.

Dùng lại cụm của vòng 3 (một container `postgres:17-alpine`, tên/cổng ngẫu nhiên, `docker rm -f
-v`; CSDL `xt` cài mới và `xt_up` nâng cấp từ 98e1a1b). Mục tiêu: các bề mặt mới sau sửa vòng 3
(`xt_audit.set_policies`, chặn hàm ngữ cảnh RLS theo oid) và các đường khác để `xt_migrator` chạy
mã dưới role app / đổi ngữ cảnh RLS (CHECK, DEFAULT, cột generated, trigger trên bảng ngữ cảnh,
hàm trigger được phép, DROP … CASCADE), event trigger, độ phủ `functions_changed`.

Test thường = phòng thủ đứng vững. `xfail(strict=True)` = lỗi ĐÃ tái hiện (gỡ xfail khi sửa).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg
import pytest

from tests.integration.model_audit_r3.test_model_engine_audit_r3_pg import (  # noqa: F401
    Env,
    _checkpoint,
    _status,
    cluster,
    envs,
    events_for,
    hr,
    new_tenant,
    q1,
)

pytestmark = pytest.mark.integration

FORGED = uuid.UUID(int=0xFF)
DBS = ["xt", "xt_up"]


def _ctx(c: psycopg.Connection[Any], tenant: uuid.UUID, roles: str = "{}", uid: int = 7) -> None:
    c.execute(
        "SELECT xt.set_context(%s, %s, %s::text[], '{}', '{}', NULL, NULL, NULL)",
        (tenant, uuid.UUID(int=uid), roles),
    )


def _cross_read(e: Env, me: Any, victim: Any) -> int:
    """App của tenant `me` (không vai trò) đếm dòng emp của tenant `victim`."""
    with e.c.app(e.db) as c, c.transaction():
        _ctx(c, me.id)
        return int(q1(c, f"SELECT count(*) FROM xt_t{victim.seq}.emp"))


# --------------------------------------------------- 1. event trigger: chỉ superuser ----
@pytest.mark.parametrize("db", DBS)
def test_event_triggers_untouchable_by_migrator(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    with e.mig() as m:
        for q in [
            "ALTER EVENT TRIGGER xt_audit_ddl_guard DISABLE",
            "ALTER EVENT TRIGGER xt_audit_drop_guard ENABLE REPLICA",
            "ALTER EVENT TRIGGER xt_audit_ddl_guard RENAME TO zz",
            "ALTER EVENT TRIGGER xt_audit_ddl_guard OWNER TO xt_migrator",
            "DROP EVENT TRIGGER xt_audit_ddl_guard",
            "CREATE EVENT TRIGGER r4 ON ddl_command_start EXECUTE FUNCTION xt_audit.ddl_guard()",
            "SET session_replication_role = replica",
        ]:
            with pytest.raises(psycopg.Error) as ex, m.transaction():
                m.execute(q)
            assert ex.value.sqlstate == "42501", (q, ex.value.sqlstate)
    with e.su() as su:
        ev = su.execute("SELECT evtname, evtenabled FROM pg_event_trigger ORDER BY 1").fetchall()
    assert ev == [("xt_audit_ddl_guard", "O"), ("xt_audit_drop_guard", "O")]


# ---------------------------------------------- 2. set_policies: không tiêm biểu thức ----
@pytest.mark.parametrize("db", DBS)
def test_set_policies_rejects_injection_and_keeps_tenant_restrictive(
    envs: dict[str, Env], db: str
) -> None:
    e = envs[db]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    with e.su() as su:
        before = su.execute(
            "SELECT polname, polpermissive, pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid = %s::regclass ORDER BY 1",
            (f"{s}.emp",),
        ).fetchall()
    bad = [
        (s, "emp", "m2", '{"hr\') OR true OR (\'": {"actions": ["read"], "scope": "all"}}'),
        (s, "emp", "m2", '{"hr": {"actions": ["read"], "scope": "all) OR (true"}}'),
        (s, "emp", "m2", '{"hr": {"actions": ["read OR true"], "scope": "all"}}'),
        (s, "emp", "m2", '{"hr": {"actions": "read", "scope": "all"}}'),
        (s, "emp", "m2", '["x"]'),
        (s, "emp", 'm2_tenant" ON x; --', "{}"),
        (s, 'emp" USING (true); --', "m2", "{}"),
        ("xt_meta", "iam_policy", "m2", "{}"),
        ("public", "emp", "m2", "{}"),
        (f"xt_t{t.seq}0000000", "emp", "m2", "{}"),
    ]
    with e.mig() as m:
        m.execute("CREATE TABLE xt_meta.r4_np (tenant_id uuid, id uuid)")
        try:
            bad.append(("xt_meta", "r4_np", "zz", "{}"))
            for args in bad:
                with pytest.raises(psycopg.Error), m.transaction():
                    m.execute("SELECT xt_audit.set_policies(%s, %s, %s, %s::jsonb)", args)
        finally:
            m.execute("DROP TABLE xt_meta.r4_np")
    with e.su() as su:
        after = su.execute(
            "SELECT polname, polpermissive, pg_get_expr(polqual, polrelid) FROM pg_policy"
            " WHERE polrelid = %s::regclass ORDER BY 1",
            (f"{s}.emp",),
        ).fetchall()
        assert q1(su, "SELECT count(*) FROM xt_audit.policy_pass") == 0
    assert after == before
    # thêm bộ policy mid khác vẫn luôn kèm RESTRICTIVE theo tenant (nới trong tenant = INFO)
    other = new_tenant(e)
    e.records.create(hr(other), "emp", {"code": "OTHER"})
    with e.mig() as m, m.transaction(force_rollback=True):
        wide = '{"nobody": {"actions": ["read"], "scope": "all"}}'
        m.execute(
            "SELECT xt_audit.set_policies(%s, 'emp', 'zz', %s::jsonb)", (f"xt_t{other.seq}", wide)
        )
        pols = dict(
            m.execute(
                "SELECT polname, polpermissive FROM pg_policy WHERE polrelid = %s::regclass",
                (f"xt_t{other.seq}.emp",),
            ).fetchall()
        )
        assert pols["p_zz_tenant"] is False and pols["p_m2_tenant"] is False


# ------------------------- 3. hàm ngữ cảnh: chặn theo oid (ALTER/DROP/CREATE OR REPLACE) ----
@pytest.mark.parametrize("db", DBS)
def test_protected_functions_alter_variants_blocked(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    with e.mig() as m:
        for q in [
            "ALTER FUNCTION xt.tenant() SET search_path = public",
            "ALTER FUNCTION xt.has_role(text) VOLATILE",
            "ALTER FUNCTION xt.actor() RENAME TO actor0",
            "ALTER FUNCTION xt.cur_meta() SET SCHEMA xt_meta",
            "DROP FUNCTION xt.tenant() CASCADE",
            "DROP FUNCTION xt.backend_cur()",
            "ALTER FUNCTION xt.set_context(jsonb) SECURITY INVOKER",
        ]:
            with pytest.raises(psycopg.Error) as ex, m.transaction():
                m.execute(q)
            assert ex.value.sqlstate in ("42501", "2BP01", "42883"), (q, ex.value.sqlstate)


# --------- 4. R4-01: migrator đổi ngữ cảnh RLS của xt_app qua bảng ngữ cảnh (session_ctx) ----
@pytest.mark.xfail(
    strict=True,
    reason="R4-01: trigger của migrator trên xt_meta.session_ctx"
    " viết lại tenant/roles của mọi phiên xt_app (đọc chéo tenant)",
)
@pytest.mark.parametrize("db", DBS)
def test_r4_01_migrator_cannot_rewrite_app_context_via_session_ctx_trigger(
    envs: dict[str, Env], db: str
) -> None:
    e = envs[db]
    me, victim = new_tenant(e), new_tenant(e)
    e.records.create(hr(victim), "emp", {"code": "SECRET"})
    assert _cross_read(e, me, victim) == 0
    planted = False
    with e.mig() as m:
        try:
            m.execute(
                "CREATE FUNCTION xt.r4_ctx() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN"
                f" NEW.tenant_id := '{victim.id}'; NEW.roles := '{{hr}}'; RETURN NEW; END $$;"
                " GRANT EXECUTE ON FUNCTION xt.r4_ctx() TO PUBLIC;"
                " CREATE TRIGGER r4_ctx BEFORE INSERT OR UPDATE ON xt_meta.session_ctx"
                " FOR EACH ROW EXECUTE FUNCTION xt.r4_ctx()"
            )
            planted = True
        except psycopg.Error:
            pass
    try:
        assert _cross_read(e, me, victim) == 0
    finally:
        if planted:
            with e.mig() as m:
                m.execute("DROP TRIGGER r4_ctx ON xt_meta.session_ctx; DROP FUNCTION xt.r4_ctx()")
        else:
            with e.mig() as m:
                m.execute("DROP FUNCTION IF EXISTS xt.r4_ctx()")


@pytest.mark.xfail(
    strict=True,
    reason="R4-01: migrator gỡ CHECK login !~ '^xt_' rồi gắn xt_app vào"
    " login_tenant → mọi phiên xt_app mang ngữ cảnh tenant nạn nhân",
)
@pytest.mark.parametrize("db", DBS)
def test_r4_01_migrator_cannot_bind_xt_app_in_login_tenant(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    me, victim = new_tenant(e), new_tenant(e)
    e.records.create(hr(victim), "emp", {"code": "SECRET"})
    planted: str | None = None
    with e.mig() as m:
        con = q1(
            m,
            "SELECT conname FROM pg_constraint WHERE conrelid = 'xt_meta.login_tenant'"
            "::regclass AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%xt_%'",
        )
        try:
            with m.transaction():
                if con:
                    m.execute(f"ALTER TABLE xt_meta.login_tenant DROP CONSTRAINT {con}")
                cols = [
                    r[0]
                    for r in m.execute(
                        "SELECT attname FROM pg_attribute WHERE attrelid = 'xt_meta.login_tenant'"
                        "::regclass AND attnum > 0 AND NOT attisdropped"
                    ).fetchall()
                ]
                vals = {
                    "login": "xt_app",
                    "tenant_id": victim.id,
                    "actor_id": uuid.UUID(int=7),
                    "roles": ["hr"],
                    "team_ids": [],
                    "department_ids": [],
                }
                use = [c for c in cols if c in vals]
                m.execute(
                    f"INSERT INTO xt_meta.login_tenant ({', '.join(use)}) VALUES"
                    f" ({', '.join(['%s'] * len(use))})",
                    [vals[c] for c in use],
                )
            planted = con or ""
        except psycopg.Error:
            pass
    try:
        assert _cross_read(e, me, victim) == 0
    finally:
        if planted is not None:
            with e.mig() as m:
                m.execute("DELETE FROM xt_meta.login_tenant WHERE login = 'xt_app'")
                if planted:
                    m.execute(
                        f"ALTER TABLE xt_meta.login_tenant ADD CONSTRAINT {planted}"
                        " CHECK (login !~ '^xt_')"
                    )


# ------------- 5. R4-02: mã của migrator chạy dưới xt_app (CHECK/DEFAULT/generated) ----
PLANT = {
    "check": "ALTER TABLE {s}.emp ADD CONSTRAINT r4 CHECK (xt.r4_forge()) NOT VALID",
    "default": "ALTER TABLE {s}.emp ALTER COLUMN note SET DEFAULT (xt.r4_forge())::text",
    "domain": "CREATE DOMAIN xt.r4_dom AS text CHECK (xt.r4_forge());"
    " ALTER TABLE {s}.emp ADD COLUMN r4d xt.r4_dom DEFAULT 'x'",
    "generated": "CREATE FUNCTION xt.r4_forge_i(x text) RETURNS boolean LANGUAGE plpgsql"
    " IMMUTABLE AS $$ BEGIN RETURN xt.r4_forge(); END $$;"
    " GRANT EXECUTE ON FUNCTION xt.r4_forge_i(text) TO xt_app;"
    " ALTER TABLE {s}.emp ADD COLUMN r4g boolean GENERATED ALWAYS AS (xt.r4_forge_i(code)) STORED",
}
UNPLANT = {
    "check": "ALTER TABLE {s}.emp DROP CONSTRAINT IF EXISTS r4",
    "default": "ALTER TABLE {s}.emp ALTER COLUMN note DROP DEFAULT",
    "domain": "ALTER TABLE {s}.emp DROP COLUMN IF EXISTS r4d; DROP DOMAIN IF EXISTS xt.r4_dom",
    "generated": "ALTER TABLE {s}.emp DROP COLUMN IF EXISTS r4g;"
    " DROP FUNCTION IF EXISTS xt.r4_forge_i(text)",
}


@pytest.mark.xfail(
    strict=True,
    reason="R4-02: CHECK/DEFAULT/domain của migrator gọi xt.set_context"
    " dưới xt_app → giả actor trong audit + nâng vai trò giữa transaction",
)
@pytest.mark.parametrize("how", list(PLANT))
@pytest.mark.parametrize("db", DBS)
def test_r4_02_planted_expression_cannot_forge_audit_actor(
    envs: dict[str, Env], db: str, how: str
) -> None:
    e = envs[db]
    t = new_tenant(e)
    s = f"xt_t{t.seq}"
    with e.mig() as m:
        m.execute(
            "CREATE OR REPLACE FUNCTION xt.r4_forge() RETURNS boolean LANGUAGE plpgsql AS $$ BEGIN"
            f" PERFORM xt.set_context(xt.tenant(), '{FORGED}', '{{hr,admin}}', '{{}}', '{{}}',"
            " NULL, NULL, NULL); RETURN true; END $$;"
            " GRANT EXECUTE ON FUNCTION xt.r4_forge() TO xt_app"
        )
        try:
            m.execute(PLANT[how].format(s=s))
        except psycopg.Error:
            m.execute(UNPLANT[how].format(s=s))
            m.execute("DROP FUNCTION xt.r4_forge()")
            return  # bị chặn: đúng
    try:
        rid = e.records.create(hr(t), "emp", {"code": "FORGE"})["record"]["id"]
        actors = [ev[1] for ev in events_for(e, t, rid)]
        assert actors == [uuid.UUID(int=7)], actors
    finally:
        with e.mig() as m:
            m.execute(UNPLANT[how].format(s=s))
            m.execute("DROP FUNCTION xt.r4_forge()")


# ------------- 6. R4-03: DROP COLUMN … CASCADE gỡ policy RESTRICTIVE theo tenant ----
@pytest.mark.xfail(
    strict=True,
    reason="R4-03: drop_guard chỉ chặn policy bị xóa trực tiếp"
    " (original); DROP COLUMN tenant_id CASCADE gỡ p_*_tenant → đọc chéo tenant",
)
@pytest.mark.parametrize("db", DBS)
def test_r4_03_cascade_cannot_drop_tenant_policy(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    me, victim = new_tenant(e), new_tenant(e)
    e.records.create(hr(victim), "emp", {"code": "SECRET"})
    vs = f"xt_t{victim.seq}"
    with e.mig() as m:
        try:
            with m.transaction():
                m.execute(f"ALTER TABLE {vs}.emp DROP COLUMN tenant_id CASCADE")
                m.execute(
                    f"ALTER TABLE {vs}.emp ADD COLUMN tenant_id uuid NOT NULL DEFAULT '{victim.id}'"
                )
        except psycopg.Error:
            return  # bị chặn: đúng
        tenant_pol = q1(
            m,
            "SELECT count(*) FROM pg_policy WHERE polrelid = %s::regclass AND NOT polpermissive",
            f"{vs}.emp",
        )
    with e.c.app(e.db) as c, c.transaction():
        _ctx(c, me.id, "{hr}")
        seen = q1(c, f"SELECT count(*) FROM {vs}.emp")
    assert tenant_pol >= 1 and seen == 0, (tenant_pol, seen)


# ----------- 7. R4-04: hàm trigger được phép (xt.row_touch) không được bảo vệ ----
@pytest.mark.xfail(
    strict=True,
    reason="R4-04: migrator CREATE OR REPLACE xt.row_touch() (oid"
    " giữ nguyên, vẫn qua whitelist trigger) → mã chạy trên mọi lần ghi của xt_app",
)
@pytest.mark.parametrize("db", DBS)
def test_r4_04_row_touch_immutable_for_migrator(envs: dict[str, Env], db: str) -> None:
    e = envs[db]
    t = new_tenant(e)
    with e.su() as su:
        orig = str(q1(su, "SELECT pg_get_functiondef('xt.row_touch()'::regprocedure)"))
    forged = re.sub(
        r"\nBEGIN\n",
        "\nBEGIN\n  PERFORM xt.set_context(NEW.tenant_id,"
        f" '{FORGED}', '{{hr}}', '{{}}', '{{}}', NULL, NULL, NULL);\n",
        orig,
        count=1,
    )
    assert forged != orig
    with e.mig() as m:
        try:
            m.execute(forged)
        except psycopg.Error:
            return  # bị chặn: đúng
    try:
        rid = e.records.create(hr(t), "emp", {"code": "TOUCH"})["record"]["id"]
        actors = [ev[1] for ev in events_for(e, t, rid)]
        assert actors == [uuid.UUID(int=7)], actors
    finally:
        with e.su() as su:
            su.execute(orig)


# ----------- 8. functions_changed: hàm chạy trong đường ghi có audit phải nằm trong digest ----
@pytest.mark.xfail(
    strict=True,
    reason="R4-05: digest checkpoint không gồm xt.row_touch (trigger BEFORE trên mọi bảng model)",
)
def test_r4_05_checkpoint_covers_row_touch(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    e.records.create(hr(t), "emp", {"code": "CK4"})
    doc = _checkpoint(e)
    with e.su() as su:
        orig = str(q1(su, "SELECT pg_get_functiondef('xt.row_touch()'::regprocedure)"))
        su.execute(orig.replace("\nBEGIN\n", "\nBEGIN\n  -- r4\n", 1))
    try:
        assert _status(e, doc, t) == "functions_changed"
    finally:
        with e.su() as su:
            su.execute(orig)


def test_checkpoint_detects_protected_function_config_change(envs: dict[str, Env]) -> None:
    e = envs["xt"]
    t = new_tenant(e)
    e.records.create(hr(t), "emp", {"code": "CK5"})
    doc = _checkpoint(e)
    with e.su() as su:
        su.execute("ALTER FUNCTION xt.tenant() SET search_path = public")
    try:
        assert _status(e, doc, t) == "functions_changed"
    finally:
        with e.su() as su:
            su.execute("ALTER FUNCTION xt.tenant() RESET search_path")
    assert _status(e, doc, t) == "ok"
