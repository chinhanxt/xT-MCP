# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bootstrap nền tảng và vòng đời tenant (architecture-v2 §6.3, SMT B2).

Mỗi tenant hai schema: `xt_t<seq>` (bảng model, chủ xt_migrator) và `api_t<seq>` (view, chủ
xt_api_owner). Xóa tenant = `DROP SCHEMA … CASCADE` + xóa dòng lõi theo tenant (Luật BVDLCN).
Trần ~200 tenant mỗi CSDL (SMT §3).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from importlib.resources import files
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from xt.adapters.db.postgrest import notify_postgrest
from xt.domain.model.context import MAX_TENANTS_PER_DB, TenantRef, api_schema, data_schema
from xt.domain.model.errors import ModelError
from xt.domain.model.ident import check_role


def _sql_file(name: str) -> str:
    return files("xt.adapters.db").joinpath(f"sql/{name}").read_text(encoding="utf-8")


def postgrest_sql() -> str:
    return _sql_file("postgrest.sql")


def bootstrap_sql() -> str:
    return _sql_file("bootstrap.sql") + "\n" + postgrest_sql()


def audit_meta_sql() -> str:
    return files("xt.adapters.db").joinpath("sql/audit_meta.sql").read_text(encoding="utf-8")


def install_meta_audit(superuser: psycopg.Connection[Any]) -> None:
    """Cài/cập nhật API audit cho bảng `xt_meta.*` (I-AUD-META) và event trigger bảo vệ audit.
    Idempotent; cần superuser. CSDL mới: `bootstrap()` đã gọi. CSDL bootstrap trước 16/10/2026:
    chạy hàm này một lần rồi `xt db migrate` (migration 0006 gắn trigger cho bảng xt_meta)."""
    row = superuser.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    if row is None or not row[0]:
        raise ValueError("install_meta_audit cần superuser")
    with superuser.transaction():
        superuser.execute(audit_meta_sql().encode())


def bootstrap(
    superuser: psycopg.Connection[Any],
    *,
    migrator_password: str,
    app_password: str,
    pepper: bytes,
    pepper_kid: int = 1,
) -> bool:
    """Chạy bootstrap một lần (idempotent: đã có thì trả False). Mật khẩu/khóa HMAC lấy từ
    secret (Compose secret/OpenBao), không sinh hay hard-code trong mã. Khóa HMAC KHÔNG được ghi
    vào DB (cũng không đi qua câu lệnh SQL): chỉ dấu vân tay sha256 tính ở đây."""
    if len(pepper) < 32:
        raise ValueError("khóa HMAC audit phải ≥ 32 byte")
    exists = superuser.execute("SELECT to_regclass('xt_meta.platform') IS NOT NULL").fetchone()
    if exists and exists[0]:
        return False
    with superuser.transaction():
        superuser.execute(bootstrap_sql().encode())
        superuser.execute(audit_meta_sql().encode())
        superuser.execute(
            sql.SQL("ALTER ROLE xt_migrator PASSWORD {}").format(sql.Literal(migrator_password))
        )
        superuser.execute(
            sql.SQL("ALTER ROLE xt_app PASSWORD {}").format(sql.Literal(app_password))
        )
        _install_fingerprint(superuser, pepper_kid, pepper)
    return True


def _install_fingerprint(superuser: psycopg.Connection[Any], kid: int, key: bytes) -> None:
    fp = hashlib.sha256(key).digest()
    superuser.execute("UPDATE xt_audit.pepper SET active = false WHERE active")
    superuser.execute(
        "INSERT INTO xt_audit.pepper (kid, fingerprint, active) VALUES (%s, %s, true)", (kid, fp)
    )


def install_postgrest_role(
    superuser: psycopg.Connection[Any],
    *,
    authenticator_password: str,
    issuer: str,
    clients: Sequence[str],
) -> None:
    """Cài/cập nhật lớp PostgREST (idempotent, cả trên CSDL bootstrap trước khi có postgrest.sql):
    role `xt_authenticator` + mật khẩu (từ secret), `iss` Keycloak phải khớp, và allowlist `azp`
    được đọc /data (thay toàn bộ danh sách cũ). Chỉ superuser (CREATE ROLE)."""
    if not issuer.startswith(("https://", "http://")):
        raise ValueError("issuer phải là URL realm Keycloak")
    with superuser.transaction():
        superuser.execute(postgrest_sql().encode())
        superuser.execute(
            sql.SQL("ALTER ROLE xt_authenticator PASSWORD {}").format(
                sql.Literal(authenticator_password)
            )
        )
        superuser.execute(
            "INSERT INTO xt_meta.platform (key, value) VALUES ('pgrst_issuer', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (issuer,),
        )
        superuser.execute("DELETE FROM xt_meta.pgrst_client")
        for c in sorted(set(clients)):
            superuser.execute("INSERT INTO xt_meta.pgrst_client (client_id) VALUES (%s)", (c,))
        notify_postgrest(superuser)


def rotate_audit_key(superuser: psycopg.Connection[Any], kid: int, key: bytes) -> None:
    """Xoay khóa HMAC audit (chỉ superuser/đường vận hành): kid mới thành active, kid cũ giữ để
    kiểm chứng audit cũ. Sau đó cấu hình xt-api/xt-worker dùng AuditKey(kid, key) mới."""
    if len(key) < 32:
        raise ValueError("khóa HMAC audit phải ≥ 32 byte")
    with superuser.transaction():
        _install_fingerprint(superuser, kid, key)


def provision_tenant(
    migrator: psycopg.Connection[Any],
    tenant_id: UUID,
    name: str,
    seq: int | None = None,
    *,
    max_tenants: int = MAX_TENANTS_PER_DB,
) -> TenantRef:
    with migrator.transaction():
        migrator.execute("LOCK TABLE xt_meta.tenant IN SHARE ROW EXCLUSIVE MODE")
        row = migrator.execute("SELECT count(*) FROM xt_meta.tenant").fetchone()
        if row is not None and int(row[0]) >= max_tenants:
            raise ModelError(
                "resource_constraint",
                f"CSDL này đã đủ {max_tenants} tenant — tạo tenant mới ở CSDL khác",
                details={"limit": max_tenants},
            )
        if seq is None:
            r = migrator.execute(
                "INSERT INTO xt_meta.tenant (id, name) VALUES (%s, %s) RETURNING seq",
                (tenant_id, name),
            ).fetchone()
        else:
            r = migrator.execute(
                "INSERT INTO xt_meta.tenant (seq, id, name) VALUES (%s, %s, %s) RETURNING seq",
                (seq, tenant_id, name),
            ).fetchone()
        assert r is not None
        ref = TenantRef(int(r[0]), tenant_id)
        ds, ap = sql.Identifier(data_schema(ref.seq)), sql.Identifier(api_schema(ref.seq))
        migrator.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION xt_migrator").format(ds))
        migrator.execute(
            sql.SQL(
                "GRANT USAGE ON SCHEMA {} TO xt_app, xt_workflow, xt_api_owner, xt_audit_owner"
            ).format(ds)
        )
        migrator.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION xt_api_owner").format(ap))
        migrator.execute("SET LOCAL ROLE xt_api_owner")
        migrator.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(ap))
        migrator.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO xt_web").format(ap))
        migrator.execute("RESET ROLE")
        notify_postgrest(migrator)
    return ref


def drop_tenant(migrator: psycopg.Connection[Any], tenant: TenantRef) -> None:
    """Xóa toàn bộ dữ liệu + cấu trúc của tenant. Audit append-only không bị đụng (Đ.25)."""
    with migrator.transaction():
        migrator.execute("SET LOCAL ROLE xt_api_owner")
        migrator.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(api_schema(tenant.seq))
            )
        )
        migrator.execute("RESET ROLE")
        migrator.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(data_schema(tenant.seq))
            )
        )
        migrator.execute("DELETE FROM xt_meta.tenant WHERE seq = %s", (tenant.seq,))
        notify_postgrest(migrator)


def bind_bi_login(
    migrator: psycopg.Connection[Any],
    login: str,
    tenant: TenantRef,
    *,
    roles: Sequence[str] = (),
    actor_id: UUID | None = None,
    team_ids: Sequence[UUID] = (),
    department_ids: Sequence[UUID] = (),
) -> None:
    """Gắn một login SQL trực tiếp (BI/DuckDB) vào đúng một tenant với tập role/phạm vi CỐ ĐỊNH:
    tenant, role, actor, team, phòng ban đều lấy theo session_user từ `xt_meta.login_tenant`,
    client không đổi được bằng set_config. Chỉ có quyền schema api_t<seq> của tenant đó. Login do
    quản trị tạo trước (migrator không có CREATEROLE); tên `xt_*` bị cấm."""
    check_role(login)
    if login.startswith("xt_"):
        raise ValueError("không gắn tenant cho role nội bộ xt_*")
    for r in roles:
        check_role(r)
    ap = sql.Identifier(api_schema(tenant.seq))
    who = sql.Identifier(login)
    with migrator.transaction():
        migrator.execute(
            "INSERT INTO xt_meta.login_tenant (login, tenant_id, actor_id, roles, team_ids,"
            " department_ids) VALUES (%s, %s, %s, %s::text[], %s::uuid[], %s::uuid[])"
            " ON CONFLICT (login) DO UPDATE SET tenant_id = EXCLUDED.tenant_id,"
            " actor_id = EXCLUDED.actor_id, roles = EXCLUDED.roles, team_ids = EXCLUDED.team_ids,"
            " department_ids = EXCLUDED.department_ids",
            (login, tenant.id, actor_id, list(roles), list(team_ids), list(department_ids)),
        )
        migrator.execute("SET LOCAL ROLE xt_api_owner")
        migrator.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(ap, who))
        migrator.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(ap, who))
        migrator.execute("RESET ROLE")
        migrator.execute(sql.SQL("GRANT USAGE ON SCHEMA xt TO {}").format(who))
        migrator.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION xt.cur(), xt.tenant(), xt.actor(),"
                " xt.has_role(text), xt.in_teams(uuid), xt.in_departments(uuid) TO {}"
            ).format(who)
        )
        db = migrator.execute("SELECT current_database()").fetchone()
        assert db is not None
        migrator.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(str(db[0])), who)
        )
