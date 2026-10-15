# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kho Postgres của lớp ghép (migration 0003): hồ sơ tenant, tổ chức, policy IAM, định nghĩa quy
trình, ràng buộc primitive, gói đã cài, thông báo, làn PII.

Đọc/ghi lúc chạy đi qua `PgUnitOfWork` (xt_app + RLS theo tenant của transaction). Ghi cấu hình
(`register_tenant`, `import_org`, `save_*`) dùng kết nối xt_migrator của lệnh quản trị, luôn kèm
tenant tường minh; không có đường nào suy tenant từ tham số tool.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from xt.adapters.db.connection import Database
from xt.adapters.db.tenancy import provision_tenant
from xt.domain.model.context import ModelActor, TenantRef

PERSON_NS = uuid.UUID("6d1f3c52-6a4b-4d55-9c58-7b0f6f1e2a10")


class _Uow(Protocol):
    def tenant_tx(self, actor: ModelActor, *, workflow: bool = False) -> Any: ...


@dataclass(frozen=True, slots=True)
class TenantProfile:
    tenant: TenantRef
    name: str
    alias: str
    timezone: str
    llm_egress: str
    pii_epoch: int


@dataclass(frozen=True, slots=True)
class Person:
    id: UUID
    key: str
    name: str
    subject: str | None
    roles: tuple[str, ...]
    unit: UUID | None
    unit_key: str | None
    manager: UUID | None
    title: str | None
    active: bool
    limit_amount: float | None
    email: str | None = None


@dataclass(frozen=True, slots=True)
class Unit:
    id: UUID
    key: str
    name: str
    head: UUID | None


@dataclass(frozen=True, slots=True)
class ProcessDefinition:
    process_id: str
    version: int
    spec: dict[str, Any]
    spec_sha256: str
    allowed_ops: dict[str, list[str]]
    active: bool
    pack: str


@dataclass(frozen=True, slots=True)
class Binding:
    model: str
    primitive: str
    config: dict[str, Any]
    pack: str


def person_id(tenant: UUID, key: str) -> UUID:
    """Id ổn định của người theo (tenant, key): nhập lại tệp tổ chức không đổi id."""
    return uuid.uuid5(PERSON_NS, f"{tenant}:person:{key}")


def unit_id(tenant: UUID, key: str) -> UUID:
    return uuid.uuid5(PERSON_NS, f"{tenant}:unit:{key}")


# ---------------------------------------------------------------- tra cứu trước ngữ cảnh ----
def tenant_by_alias(db: Database, alias: str) -> TenantRef | None:
    with db.connection() as conn:
        row = conn.execute("SELECT seq, id FROM xt.tenant_by_alias(%s)", (alias,)).fetchone()
    return TenantRef(int(row[0]), row[1]) if row else None


def tenant_by_id(db: Database, tenant_id: UUID) -> TenantRef | None:
    with db.connection() as conn:
        row = conn.execute("SELECT seq, id FROM xt.tenant_by_id(%s)", (tenant_id,)).fetchone()
    return TenantRef(int(row[0]), row[1]) if row else None


def pii_ticket_tenant(db: Database, ticket_hash: bytes) -> TenantRef | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT seq, id FROM xt.pii_ticket_tenant(%s)", (ticket_hash,)
        ).fetchone()
    return TenantRef(int(row[0]), row[1]) if row else None


def confirmation_tenant(db: Database, confirmation_id: str) -> TenantRef | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT seq, id FROM xt.confirmation_tenant(%s)", (confirmation_id,)
        ).fetchone()
    return TenantRef(int(row[0]), row[1]) if row else None


# ------------------------------------------------------------------------ đọc lúc chạy ----
_PERSON_SQL = (
    "SELECT p.*, u.key AS unit_key FROM xt_meta.person p"
    " LEFT JOIN xt_meta.org_unit u ON u.tenant_id = p.tenant_id AND u.id = p.unit"
)


def _person(row: Mapping[str, Any]) -> Person:
    lim = row["limit_amount"]
    return Person(
        id=row["id"],
        key=row["key"],
        name=row["name"],
        subject=row["subject"],
        roles=tuple(row["roles"]),
        unit=row["unit"],
        unit_key=row["unit_key"],
        manager=row["manager"],
        title=row["title"],
        active=bool(row["active"]),
        limit_amount=float(lim) if lim is not None else None,
        email=row.get("email"),
    )


class PgIntegrationStore:
    def __init__(self, uow: _Uow) -> None:
        self.uow = uow

    # ---- tenant
    def profile(self, actor: ModelActor) -> TenantProfile | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT p.*, t.name, t.seq FROM xt_meta.tenant_profile p"
                " JOIN xt_meta.tenant t ON t.id = p.tenant_id WHERE p.tenant_id = xt.tenant()"
            ).fetchone()
        if row is None:
            return None
        return TenantProfile(
            TenantRef(int(row["seq"]), row["tenant_id"]),
            row["name"],
            row["alias"],
            row["timezone"],
            row["llm_egress"],
            int(row["pii_epoch"]),
        )

    # ---- tổ chức
    def person_by_subject(self, actor: ModelActor, subject: str) -> Person | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(_PERSON_SQL + " WHERE p.subject = %s", (subject,)).fetchone()
        return _person(row) if row else None

    def person(self, actor: ModelActor, pid: UUID) -> Person | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(_PERSON_SQL + " WHERE p.id = %s", (pid,)).fetchone()
        return _person(row) if row else None

    def people(
        self,
        actor: ModelActor,
        *,
        query: str | None = None,
        role: str | None = None,
        unit: UUID | None = None,
        manager: UUID | None = None,
        ids: Sequence[UUID] | None = None,
        active_only: bool = False,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[int, list[Person]]:
        conds: list[str] = ["true"]
        params: list[Any] = []
        if query:
            conds.append("(p.name ILIKE %s OR p.key ILIKE %s)")
            like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params += [like, like]
        if role:
            conds.append("%s = ANY(p.roles)")
            params.append(role)
        if unit:
            conds.append("p.unit = %s")
            params.append(unit)
        if manager:
            conds.append("p.manager = %s")
            params.append(manager)
        if ids is not None:
            conds.append("p.id = ANY(%s)")
            params.append(list(ids))
        if active_only:
            conds.append("p.active")
        where = " AND ".join(conds)
        with self.uow.tenant_tx(actor) as cur:
            total = cur.execute(
                f"SELECT count(*) AS n FROM xt_meta.person p WHERE {where}", params
            ).fetchone()
            rows = cur.execute(
                f"{_PERSON_SQL} WHERE {where} ORDER BY p.name, p.id LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return int(total["n"]), [_person(r) for r in rows]

    def unit(
        self, actor: ModelActor, *, key: str | None = None, uid: UUID | None = None
    ) -> Unit | None:
        with self.uow.tenant_tx(actor) as cur:
            if key is not None:
                row = cur.execute(
                    "SELECT * FROM xt_meta.org_unit WHERE key = %s", (key,)
                ).fetchone()
            else:
                row = cur.execute("SELECT * FROM xt_meta.org_unit WHERE id = %s", (uid,)).fetchone()
        return Unit(row["id"], row["key"], row["name"], row["head"]) if row else None

    def agent_grant(
        self, actor: ModelActor, person: UUID, client_id: str
    ) -> tuple[int, tuple[str, ...]] | None:
        """(trần tự chủ 0..3, vai trò IAM của agent) mà `person` cấp cho client; None = chưa cấp."""
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT autonomy, roles FROM xt_meta.agent_grant WHERE person = %s"
                " AND client_id = %s",
                (person, client_id),
            ).fetchone()
        return (int(row["autonomy"]), tuple(row["roles"])) if row else None

    # ---- định nghĩa
    def active_policy(self, actor: ModelActor) -> tuple[int, dict[str, Any]] | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT revision, document FROM xt_meta.iam_policy ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        return (int(row["revision"]), dict(row["document"])) if row else None

    def definitions(
        self, actor: ModelActor, *, process_id: str | None = None, active_only: bool = True
    ) -> list[ProcessDefinition]:
        conds: list[str] = ["true"]
        params: list[Any] = []
        if process_id is not None:
            conds.append("process_id = %s")
            params.append(process_id)
        if active_only:
            conds.append("active")
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute(
                "SELECT * FROM xt_meta.process_definition WHERE "
                + " AND ".join(conds)
                + " ORDER BY process_id, version",
                params,
            ).fetchall()
        return [
            ProcessDefinition(
                r["process_id"],
                int(r["version"]),
                dict(r["spec"]),
                r["spec_sha256"],
                {k: list(v) for k, v in dict(r["allowed_ops"]).items()},
                bool(r["active"]),
                r["pack"],
            )
            for r in rows
        ]

    def definition(
        self, actor: ModelActor, process_id: str, version: int
    ) -> ProcessDefinition | None:
        with self.uow.tenant_tx(actor) as cur:
            r = cur.execute(
                "SELECT * FROM xt_meta.process_definition WHERE process_id = %s AND version = %s",
                (process_id, version),
            ).fetchone()
        if r is None:
            return None
        return ProcessDefinition(
            r["process_id"],
            int(r["version"]),
            dict(r["spec"]),
            r["spec_sha256"],
            {k: list(v) for k, v in dict(r["allowed_ops"]).items()},
            bool(r["active"]),
            r["pack"],
        )

    def bindings(self, actor: ModelActor) -> dict[str, Binding]:
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute("SELECT * FROM xt_meta.model_binding ORDER BY model").fetchall()
        return {
            r["model"]: Binding(r["model"], r["primitive"], dict(r["config"]), r["pack"])
            for r in rows
        }

    def packs(self, actor: ModelActor) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute(
                "SELECT pack, version, digest, installed_at FROM xt_meta.pack_install ORDER BY pack"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- thông báo
    def notify(
        self, actor: ModelActor, recipient: UUID, title: str, ref: str | None, dedupe_key: str
    ) -> bool:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "INSERT INTO xt_meta.notification (tenant_id, recipient, title, ref, dedupe_key)"
                " VALUES (xt.tenant(), %s, %s, %s, %s)"
                " ON CONFLICT (tenant_id, recipient, dedupe_key) DO NOTHING RETURNING id",
                (recipient, title[:500], ref, dedupe_key[:255]),
            ).fetchone()
        return row is not None

    def notifications(
        self, actor: ModelActor, recipient: UUID, *, open_only: bool
    ) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute(
                "SELECT id, title, ref, created_at, read_at FROM xt_meta.notification"
                " WHERE recipient = %s AND (NOT %s OR read_at IS NULL)"
                " ORDER BY created_at DESC, id LIMIT 1000",
                (recipient, open_only),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- sự kiện / sự cố của process runtime
    def process_event(
        self, actor: ModelActor, instance_id: str, type_: str, payload: Mapping[str, Any]
    ) -> None:
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.process_event (tenant_id, instance_id, type, payload)"
                " VALUES (xt.tenant(), %s, %s, %s)",
                (instance_id, type_[:100], Jsonb(dict(payload))),
            )

    def process_events(self, actor: ModelActor, instance_id: str) -> list[dict[str, Any]]:
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute(
                "SELECT type, payload, created_at FROM xt_meta.process_event"
                " WHERE instance_id = %s ORDER BY created_at, id",
                (instance_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- quyết định đề xuất thay đổi model (outbox của `model.apply`)
    def model_change(self, actor: ModelActor, proposal_id: UUID) -> dict[str, Any] | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT * FROM xt_meta.model_change_request WHERE proposal_id = %s",
                (proposal_id,),
            ).fetchone()
        return dict(row) if row else None

    def decide_model_change(
        self,
        actor: ModelActor,
        proposal_id: UUID,
        *,
        state: str,
        plan_hash: str,
        confirm: Sequence[str],
        decided_by: Sequence[str],
        reason: str | None,
    ) -> bool:
        """Ghi quyết định một lần (PK): False = đã có quyết định khác."""
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "INSERT INTO xt_meta.model_change_request (tenant_id, proposal_id, state,"
                " plan_hash, confirm, decided_by, reason) VALUES (xt.tenant(), %s, %s, %s, %s,"
                " %s, %s) ON CONFLICT DO NOTHING RETURNING proposal_id",
                (proposal_id, state, plan_hash, list(confirm), list(decided_by), reason),
            ).fetchone()
        return row is not None

    def finish_model_change(
        self,
        actor: ModelActor,
        proposal_id: UUID,
        *,
        state: str,
        applied_version: int | None,
        error: str | None,
    ) -> None:
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "UPDATE xt_meta.model_change_request SET state = %s, applied_version = %s,"
                " error = %s, updated_at = clock_timestamp()"
                " WHERE proposal_id = %s AND state = 'approved'",
                (state, applied_version, error[:1000] if error else None, proposal_id),
            )

    # ---- làn PII
    def pii_put(
        self,
        actor: ModelActor,
        token: str,
        value: str,
        *,
        field: str | None = None,
        level: str | None = None,
    ) -> None:
        """Ghi token vào vault; mức chỉ nâng (basic -> sensitive), không bao giờ hạ."""
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.pii_token (tenant_id, token, value, field, level)"
                " VALUES (xt.tenant(), %s, %s, %s, %s)"
                " ON CONFLICT (tenant_id, token) DO UPDATE SET"
                " field = coalesce(xt_meta.pii_token.field, EXCLUDED.field),"
                " level = CASE WHEN EXCLUDED.level = 'sensitive' THEN 'sensitive'"
                "  ELSE coalesce(xt_meta.pii_token.level, EXCLUDED.level) END"
                " WHERE EXCLUDED.level IS NOT NULL OR EXCLUDED.field IS NOT NULL",
                (token, value, field, level),
            )

    def pii_lookup(self, actor: ModelActor, tokens: Sequence[str]) -> dict[str, dict[str, Any]]:
        with self.uow.tenant_tx(actor) as cur:
            rows = cur.execute(
                "SELECT token, value, field, level FROM xt_meta.pii_token WHERE token = ANY(%s)",
                (list(tokens),),
            ).fetchall()
        return {r["token"]: dict(r) for r in rows}

    def pii_ticket_put(
        self,
        actor: ModelActor,
        *,
        ticket_hash: bytes,
        person: UUID,
        client_id: str,
        tokens: Sequence[str],
        pubkey: Mapping[str, Any],
        ttl_s: int,
    ) -> None:
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.pii_reveal_ticket (tenant_id, ticket_hash, person, client_id,"
                " tokens, pubkey, expires_at) VALUES (xt.tenant(), %s, %s, %s, %s, %s,"
                " clock_timestamp() + make_interval(secs => %s))",
                (ticket_hash, person, client_id, list(tokens), Jsonb(dict(pubkey)), ttl_s),
            )

    def pii_ticket_lock(self, actor: ModelActor, ticket_hash: bytes) -> dict[str, Any] | None:
        """Khóa vé còn hạn, chưa dùng (cùng transaction với lúc đánh dấu đã dùng)."""
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT * FROM xt_meta.pii_reveal_ticket WHERE ticket_hash = %s"
                " AND used_at IS NULL AND expires_at > clock_timestamp() FOR UPDATE",
                (ticket_hash,),
            ).fetchone()
        return dict(row) if row else None

    def pii_ticket_use(self, actor: ModelActor, ticket_hash: bytes) -> bool:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "UPDATE xt_meta.pii_reveal_ticket SET used_at = clock_timestamp()"
                " WHERE ticket_hash = %s AND used_at IS NULL RETURNING ticket_hash",
                (ticket_hash,),
            ).fetchone()
        return row is not None

    def pii_resolve(self, actor: ModelActor, token: str) -> str | None:
        with self.uow.tenant_tx(actor) as cur:
            row = cur.execute(
                "SELECT value FROM xt_meta.pii_token WHERE token = %s", (token,)
            ).fetchone()
        return str(row["value"]) if row else None

    def pii_egress(
        self,
        actor: ModelActor,
        *,
        trace_id: str,
        tool: str,
        client_class: str,
        mode: str,
        fields: Sequence[str],
        person: UUID | None = None,
    ) -> None:
        with self.uow.tenant_tx(actor) as cur:
            cur.execute(
                "INSERT INTO xt_meta.pii_egress (tenant_id, trace_id, tool, client_class, mode,"
                " fields, person) VALUES (xt.tenant(), %s, %s, %s, %s, %s, %s)",
                (trace_id, tool, client_class, mode, sorted(set(fields)), person),
            )


# ------------------------------------------------------------------- lệnh quản trị ----
class AdminError(ValueError):
    """Dữ liệu quản trị sai (tệp tổ chức, đăng ký tenant)."""


def connect(dsn: str) -> psycopg.Connection[Any]:
    """Kết nối autocommit cho lệnh quản trị / PgMigrator (transaction tường minh)."""
    return psycopg.connect(dsn, autocommit=True)


def register_tenant(
    migrator: psycopg.Connection[Any],
    *,
    alias: str,
    name: str,
    timezone: str,
    llm_egress: str,
    tenant_id: UUID | None = None,
) -> TenantRef:
    exists = migrator.execute(
        "SELECT 1 FROM xt_meta.tenant_profile WHERE alias = %s", (alias,)
    ).fetchone()
    if exists:
        raise AdminError(f"alias tenant {alias!r} đã tồn tại")
    ref = provision_tenant(migrator, tenant_id or uuid.uuid4(), name)
    with migrator.transaction():
        migrator.execute(
            "INSERT INTO xt_meta.tenant_profile (tenant_id, alias, timezone, llm_egress)"
            " VALUES (%s, %s, %s, %s)",
            (ref.id, alias, timezone, llm_egress),
        )
    return ref


def tenant_of_alias(migrator: psycopg.Connection[Any], alias: str) -> TenantRef:
    row = migrator.execute(
        "SELECT t.seq, t.id FROM xt_meta.tenant_profile p JOIN xt_meta.tenant t"
        " ON t.id = p.tenant_id WHERE p.alias = %s",
        (alias,),
    ).fetchone()
    if row is None:
        raise AdminError(f"không có tenant alias {alias!r} (đăng ký bằng `xt tenant create`)")
    return TenantRef(int(row[0]), row[1])


def import_org(
    migrator: psycopg.Connection[Any], tenant: TenantRef, data: Mapping[str, Any]
) -> dict[str, int]:
    """Nạp (upsert) đơn vị, người, quyền tự chủ agent. Khóa tham chiếu trong tệp là `key`."""
    units = list(data.get("units", ()))
    people = list(data.get("people", ()))
    grants = list(data.get("agent_grants", ()))
    unit_keys = {str(u["key"]) for u in units}
    person_keys = {str(p["key"]) for p in people}
    problems: list[str] = []
    for u in units:
        if u.get("head") is not None and u["head"] not in person_keys:
            problems.append(f"đơn vị {u['key']}: trưởng đơn vị {u['head']!r} không có trong people")
    for p in people:
        if p.get("unit") is not None and p["unit"] not in unit_keys:
            problems.append(f"người {p['key']}: đơn vị {p['unit']!r} không có trong units")
        if p.get("manager") is not None and p["manager"] not in person_keys:
            problems.append(f"người {p['key']}: quản lý {p['manager']!r} không có trong people")
        if not isinstance(p.get("roles", []), list):
            problems.append(f"người {p['key']}: roles phải là danh sách")
    for g in grants:
        if g.get("person") not in person_keys:
            problems.append(f"agent_grants: người {g.get('person')!r} không có trong people")
        if not isinstance(g.get("autonomy"), int) or not 0 <= g["autonomy"] <= 3:
            problems.append(f"agent_grants {g.get('client_id')!r}: autonomy phải là 0..3")
    if problems:
        raise AdminError("; ".join(problems))
    t = tenant.id
    with migrator.transaction():
        for u in units:
            migrator.execute(
                "INSERT INTO xt_meta.org_unit (tenant_id, id, key, name, head)"
                " VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant_id, id) DO UPDATE SET name = EXCLUDED.name,"
                " head = EXCLUDED.head",
                (
                    t,
                    unit_id(t, u["key"]),
                    u["key"],
                    u["name"],
                    person_id(t, u["head"]) if u.get("head") else None,
                ),
            )
        for p in people:
            migrator.execute(
                "INSERT INTO xt_meta.person (tenant_id, id, key, name, subject, roles, unit,"
                " manager, title, email, active, limit_amount)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant_id, id) DO UPDATE SET name = EXCLUDED.name,"
                " subject = EXCLUDED.subject, roles = EXCLUDED.roles, unit = EXCLUDED.unit,"
                " manager = EXCLUDED.manager, title = EXCLUDED.title, email = EXCLUDED.email,"
                " active = EXCLUDED.active, limit_amount = EXCLUDED.limit_amount",
                (
                    t,
                    person_id(t, p["key"]),
                    p["key"],
                    p["name"],
                    p.get("subject"),
                    list(p.get("roles", [])),
                    unit_id(t, p["unit"]) if p.get("unit") else None,
                    person_id(t, p["manager"]) if p.get("manager") else None,
                    p.get("title"),
                    p.get("email"),
                    bool(p.get("active", True)),
                    p.get("limit_amount"),
                ),
            )
        for g in grants:
            migrator.execute(
                "INSERT INTO xt_meta.agent_grant (tenant_id, person, client_id, autonomy, roles)"
                " VALUES (%s,%s,%s,%s,%s) ON CONFLICT (tenant_id, person, client_id)"
                " DO UPDATE SET autonomy = EXCLUDED.autonomy, roles = EXCLUDED.roles",
                (
                    t,
                    person_id(t, g["person"]),
                    g["client_id"],
                    g["autonomy"],
                    list(g.get("roles", ["agent"])),
                ),
            )
    return {"units": len(units), "people": len(people), "agent_grants": len(grants)}


def installed_pack(
    migrator: psycopg.Connection[Any], tenant: TenantRef, pack: str
) -> dict[str, Any] | None:
    with migrator.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT pack, version, digest FROM xt_meta.pack_install"
            " WHERE tenant_id = %s AND pack = %s",
            (tenant.id, pack),
        ).fetchone()
    return dict(row) if row else None


def bind_queue_tenant(owner: psycopg.Connection[Any], tenant: TenantRef) -> str:
    """Gắn tenant vào hàng đợi (docs/integration-notes.md "Job queue" §4): job role NOLOGIN riêng
    `xt_job_t<seq>` (thành viên nhóm xt_job), xt_app được SET ROLE sang nó, chỉ USAGE schema
    tenant của chính nó, và dòng `xt_queue.tenant_bindings`. Cần role có CREATEROLE (owner/DBA);
    idempotent."""
    schema, role = f"xt_t{tenant.seq}", f"xt_job_t{tenant.seq}"
    with owner.transaction():
        if not owner.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            owner.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOBYPASSRLS IN ROLE xt_job").format(
                    sql.Identifier(role)
                )
            )
        owner.execute(
            sql.SQL("GRANT {} TO xt_app WITH INHERIT FALSE, SET TRUE").format(sql.Identifier(role))
        )
        owner.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        owner.execute(
            "INSERT INTO xt_queue.tenant_bindings (tenant_id, schema_name, job_role)"
            " VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (tenant.id, schema, role),
        )
    return role


def installed_packs(migrator: psycopg.Connection[Any], tenant: TenantRef) -> set[str]:
    rows = migrator.execute(
        "SELECT pack FROM xt_meta.pack_install WHERE tenant_id = %s", (tenant.id,)
    ).fetchall()
    return {str(r[0]) for r in rows}


def current_policy(
    migrator: psycopg.Connection[Any], tenant: TenantRef
) -> tuple[int, dict[str, Any]] | None:
    row = migrator.execute(
        "SELECT revision, document FROM xt_meta.iam_policy WHERE tenant_id = %s"
        " ORDER BY revision DESC LIMIT 1",
        (tenant.id,),
    ).fetchone()
    return (int(row[0]), dict(row[1])) if row else None


def active_definitions(
    migrator: psycopg.Connection[Any], tenant: TenantRef
) -> dict[str, tuple[int, str]]:
    rows = migrator.execute(
        "SELECT process_id, version, spec_sha256 FROM xt_meta.process_definition"
        " WHERE tenant_id = %s AND active",
        (tenant.id,),
    ).fetchall()
    return {r[0]: (int(r[1]), str(r[2])) for r in rows}


def save_pack(
    migrator: psycopg.Connection[Any],
    tenant: TenantRef,
    *,
    pack: str,
    version: str,
    digest: str,
    proposed_by: str,
    approved_by: str,
    policy: tuple[dict[str, Any], str] | None,
    processes: Sequence[tuple[str, int, dict[str, Any], str, dict[str, list[str]]]],
    bindings: Sequence[tuple[str, str, dict[str, Any]]],
) -> None:
    """Ghi phần không phải DDL của một gói trong MỘT transaction (DDL đã áp qua PgMigrator)."""
    t = tenant.id
    with migrator.transaction():
        migrator.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"xt:pack:{t}",))
        if policy is not None:
            doc, pol_digest = policy
            cur = current_policy(migrator, tenant)
            migrator.execute(
                "INSERT INTO xt_meta.iam_policy (tenant_id, revision, document, digest,"
                " proposed_by, approved_by) VALUES (%s, %s, %s, %s, %s, %s)",
                (t, (cur[0] + 1) if cur else 1, Jsonb(doc), pol_digest, proposed_by, approved_by),
            )
        for pid, ver, spec, sha, ops in processes:
            existing = migrator.execute(
                "SELECT spec_sha256 FROM xt_meta.process_definition"
                " WHERE tenant_id = %s AND process_id = %s AND version = %s",
                (t, pid, ver),
            ).fetchone()
            if existing is not None and existing[0] != sha:
                raise AdminError(
                    f"quy trình {pid} v{ver} đã có nội dung khác"
                    " (định nghĩa bất biến: tăng version)"
                )
            migrator.execute(
                "UPDATE xt_meta.process_definition SET active = false"
                " WHERE tenant_id = %s AND process_id = %s AND active",
                (t, pid),
            )
            if existing is None:
                migrator.execute(
                    "INSERT INTO xt_meta.process_definition (tenant_id, process_id, version, spec,"
                    " spec_sha256, allowed_ops, active, pack, approved_by)"
                    " VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s)",
                    (t, pid, ver, Jsonb(spec), sha, Jsonb(ops), pack, approved_by),
                )
            else:
                migrator.execute(
                    "UPDATE xt_meta.process_definition SET active = true"
                    " WHERE tenant_id = %s AND process_id = %s AND version = %s",
                    (t, pid, ver),
                )
        for model, primitive, config in bindings:
            migrator.execute(
                "INSERT INTO xt_meta.model_binding (tenant_id, model, primitive, config, pack)"
                " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (tenant_id, model) DO UPDATE SET"
                " primitive = EXCLUDED.primitive, config = EXCLUDED.config, pack = EXCLUDED.pack",
                (t, model, primitive, Jsonb(config), pack),
            )
        migrator.execute(
            "INSERT INTO xt_meta.pack_install (tenant_id, pack, version, digest, proposed_by,"
            " approved_by) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (tenant_id, pack) DO UPDATE"
            " SET version = EXCLUDED.version, digest = EXCLUDED.digest,"
            " proposed_by = EXCLUDED.proposed_by, approved_by = EXCLUDED.approved_by,"
            " installed_at = clock_timestamp()",
            (t, pack, version, digest, proposed_by, approved_by),
        )
