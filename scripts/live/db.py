# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Fixed-query, rollback-only collection of live verification evidence."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg import IsolationLevel, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")
_LIMIT = 10000


class CollectionError(ValueError):
    """Evidence cannot be collected safely or completely."""


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CollectionError("Invalid identifier")
    return value


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(v) for v in value]
    return value


def _rows(cur: Any, query: Any, params: Any = ()) -> list[dict[str, Any]]:
    cur.execute(query, params)
    rows = cur.fetchmany(_LIMIT + 1)
    if len(rows) > _LIMIT:
        raise CollectionError("Evidence row limit exceeded")
    return [dict(row) for row in rows]


def collect(
    expectations: dict[str, Any],
    tenant_alias: str,
    since: datetime,
    until: datetime,
    dsn: str,
) -> dict[str, Any]:
    """Collect metadata using one consistent, strictly read-only DB snapshot.

    Database errors are sanitized. Credentials and receipt contents
    must never be logged. No application context setter or mutation is invoked.
    """
    if since.tzinfo is None or until.tzinfo is None or since >= until:
        raise CollectionError("Invalid evidence interval")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", tenant_alias):
        raise CollectionError("Invalid tenant alias")
    options = (
        "-c default_transaction_read_only=on -c statement_timeout=15000 "
        "-c lock_timeout=3000 -c idle_in_transaction_session_timeout=30000 "
        "-c search_path=pg_catalog -c timezone=UTC"
    )
    try:
        conn = psycopg.connect(
            dsn,
            autocommit=False,
            row_factory=dict_row,
            options=options,
            connect_timeout=10,
            application_name="xt-live-readonly-verifier",
        )
    except psycopg.Error:
        raise CollectionError("Database connection failed") from None
    try:
        conn.read_only = True
        conn.isolation_level = IsolationLevel.REPEATABLE_READ
        with conn.cursor() as cur:
            settings = _rows(cur, "SELECT current_setting('transaction_read_only') AS readonly")
            if len(settings) != 1 or settings[0]["readonly"] != "on":
                raise CollectionError("Read-only transaction required")
            tenants = _rows(cur, "SELECT seq,id FROM xt.tenant_by_alias(%s)", (tenant_alias,))
            if len(tenants) != 1:
                raise CollectionError("Tenant cannot be resolved uniquely")
            tenant = tenants[0]["id"]
            seq = tenants[0]["seq"]
            if type(seq) is not int or not 1 <= seq <= 9999999:
                raise CollectionError("Invalid tenant sequence")
            access = _rows(
                cur,
                """SELECT r.rolsuper OR r.rolbypassrls AS bypass,
                current_user AS role FROM pg_catalog.pg_roles r
                WHERE r.rolname = current_user""",
            )
            if len(access) != 1 or access[0]["bypass"] is not True:
                raise CollectionError("Verifier requires unrestricted administrative visibility")
            result: dict[str, Any] = {
                "format": "xt-live-snapshot/1",
                "tenant": tenant_alias,
                "tenant_id": tenant,
                "since": since,
                "until": until,
                "captured_at": datetime.now(UTC),
                "records": {},
                "record_audit": {},
                "stock_changes": [],
                "operations": [],
                "movements": [],
                "decisions": [],
                "workflow": [],
                "packs": [],
                "pii": [],
                "audit": [],
            }
            for spec in expectations.get("records", []):
                label = spec["label"]
                if (
                    not isinstance(label, str)
                    or not _LABEL.fullmatch(label)
                    or label in result["records"]
                ):
                    raise CollectionError("Invalid or duplicate record label")
                model = _identifier(spec["model"])
                matches = spec["match"]
                if not isinstance(matches, dict) or not matches:
                    raise CollectionError("Exact record match required")
                columns = list(
                    dict.fromkeys(
                        [
                            "id",
                            "revision",
                            "owner_id",
                            "created_at",
                            "updated_at",
                            *(_identifier(k) for k in matches),
                            *(_identifier(k) for k in spec.get("fields", {})),
                        ]
                    )
                )
                predicates = []
                values: list[Any] = []
                for field, value in matches.items():
                    if value is not None and not isinstance(value, (str, int, float, bool)):
                        raise CollectionError("Record matches must be scalar")
                    predicates.append(
                        sql.SQL("{} IS NOT DISTINCT FROM %s").format(sql.Identifier(field))
                    )
                    values.append(value)
                query = sql.SQL("SELECT {} FROM {} WHERE {} ORDER BY id LIMIT 10001").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.Identifier(f"xt_t{seq}", model),
                    sql.SQL(" AND ").join(predicates),
                )
                result["records"][label] = _rows(cur, query, values)
                current_ids = [row["id"] for row in result["records"][label]]
                audit_query = """WITH marked AS (
                    SELECT record_id FROM xt_audit.event WHERE tenant_id=%s AND model=%s
                    AND (diff->'new' @> %s::jsonb OR diff->'old' @> %s::jsonb))
                    SELECT seq,model,record_id,op,at FROM xt_audit.event
                    WHERE tenant_id=%s AND model=%s
                    AND (record_id IN (SELECT record_id FROM marked) OR record_id=ANY(%s::uuid[]))
                    """
                audit_values: list[Any] = [
                    tenant,
                    model,
                    Jsonb(matches),
                    Jsonb(matches),
                    tenant,
                    model,
                    current_ids,
                ]
                if not spec.get("in_window", True):
                    audit_query += " AND at>=%s AND at<=%s"
                    audit_values.extend([since, until])
                audit_query += " ORDER BY seq LIMIT 10001"
                result["record_audit"][label] = _rows(cur, audit_query, audit_values)

            for operation in expectations.get("operations", []):
                rows = _rows(
                    cur,
                    """SELECT id,actor_key,tool,idempotency_key,state,receipt,fingerprint
                    FROM xt_meta.tool_operation WHERE tenant_id=%s AND actor_key=%s
                    AND tool=%s AND idempotency_key=%s LIMIT 10001""",
                    (tenant, operation["actor_key"], operation["tool"], operation["key"]),
                )
                result["operations"].extend(rows)
            warehouse = expectations.get("warehouse")
            requests = {
                row["id"]: row
                for record in expectations.get("records", [])
                if record["model"] == "stock_request"
                for row in result["records"][record["label"]]
            }.values()
            for request in requests:
                rid = request["id"]
                result["movements"].extend(
                    _rows(
                        cur,
                        sql.SQL("""SELECT id,key,request,item,
                    location,warehouse,quantity_milli,approved_by,posted_at FROM {}
                    WHERE request=%s ORDER BY key LIMIT 10001""").format(
                            sql.Identifier(f"xt_t{seq}", "stock_movement")
                        ),
                        (rid,),
                    )
                )
                ref = f"stock_request:{rid}"
                result["decisions"].extend(
                    _rows(
                        cur,
                        """SELECT id,kind,ref,instance_id,task_id,
                    step,decision,approver,requester,actor_kind,actor_client,confirmed_by,decided_at
                    FROM xt_meta.decision WHERE tenant_id=%s AND ref=%s
                    ORDER BY decided_at,id LIMIT 10001""",
                        (tenant, ref),
                    )
                )
                result["workflow"].extend(
                    _rows(
                        cur,
                        """SELECT r.ref,w.id,w.process_id,w.version,
                    w.status,w.state,w.candidates,w.next_due FROM xt_meta.workflow_request r
                    JOIN xt_meta.workflow_instance w ON w.tenant_id=r.tenant_id
                     AND w.id=r.instance_id
                    WHERE r.tenant_id=%s AND r.ref=%s ORDER BY w.id LIMIT 10001""",
                        (tenant, ref),
                    )
                )
            if warehouse:
                stock_seen: set[tuple[str, str]] = set()
                by_label = {s["label"]: s for s in expectations.get("records", [])}
                for balance in warehouse.get("balances", []):
                    for kind, model, field in (
                        ("balance", "stock_balance", "quantity_milli"),
                        ("level", "stock_level", "on_hand_milli"),
                    ):
                        label = balance[kind]
                        if by_label[label]["model"] != model:
                            raise CollectionError("Invalid stock evidence model")
                        for row in result["records"][label]:
                            identity = (model, str(row["id"]))
                            if identity in stock_seen:
                                continue
                            stock_seen.add(identity)
                            result["stock_changes"].extend(
                                _rows(
                                    cur,
                                    """SELECT record_id,model,at,op,
                                diff->'old'->%s AS before,diff->'new'->%s AS after
                                FROM xt_audit.event WHERE tenant_id=%s AND model=%s
                                AND record_id=%s AND at>=%s AND at<=%s
                                AND diff->'new' ? %s ORDER BY seq LIMIT 10001""",
                                    (field, field, tenant, model, row["id"], since, until, field),
                                )
                            )
                identities = [UUID(warehouse[k]) for k in ("approver", "requester")]
                result["people"] = _rows(
                    cur,
                    """SELECT id,subject,active FROM xt_meta.person
                    WHERE tenant_id=%s AND id=ANY(%s::uuid[]) ORDER BY id LIMIT 10001""",
                    (tenant, identities),
                )
            if expectations.get("disabled_pack"):
                result["packs"] = _rows(
                    cur,
                    """SELECT pack,status,models FROM xt_meta.pack_install
                    WHERE tenant_id=%s AND pack=%s LIMIT 10001""",
                    (tenant, expectations["disabled_pack"]),
                )
            pii = expectations.get("pii")
            if pii:
                result["pii"] = _rows(
                    cur,
                    """SELECT trace_id,tool,client_class,mode,fields,at FROM xt_meta.pii_egress
                    WHERE tenant_id=%s AND trace_id=%s AND at>=%s AND at<=%s
                    ORDER BY at,id LIMIT 10001""",
                    (tenant, pii["trace_id"], since, until),
                )
            status = _rows(cur, "SELECT n,first_bad_seq FROM xt_audit.verify(%s)", (tenant,))
            if len(status) != 1:
                raise CollectionError("Audit verification unavailable")
            result["audit_status"] = status[0]
            result["audit"] = _rows(
                cur,
                """SELECT seq,model,record_id,op,actor_id,effective_actor,
                trace_id,at FROM xt_audit.event WHERE tenant_id=%s AND at>=%s AND at<=%s
                ORDER BY seq LIMIT 10001""",
                (tenant, since, until),
            )
            foreign = expectations.get("foreign")
            if foreign:
                foreign_alias = foreign["tenant"]
                if (
                    not isinstance(foreign_alias, str)
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", foreign_alias)
                    or foreign_alias == tenant_alias
                ):
                    raise CollectionError("Invalid foreign tenant")
                ref = foreign["ref"]
                if not isinstance(ref, str) or ref.count(":") != 1:
                    raise CollectionError("Invalid foreign reference")
                model, raw_id = ref.split(":")
                model = _identifier(model)
                try:
                    foreign_id = UUID(raw_id)
                except ValueError:
                    raise CollectionError("Invalid foreign reference") from None
                foreign_tenants = _rows(
                    cur, "SELECT seq,id FROM xt.tenant_by_alias(%s)", (foreign_alias,)
                )
                if len(foreign_tenants) != 1:
                    raise CollectionError("Foreign tenant cannot be resolved uniquely")
                foreign_seq = foreign_tenants[0]["seq"]
                foreign_tenant = foreign_tenants[0]["id"]
                if (
                    type(foreign_seq) is not int
                    or not 1 <= foreign_seq <= 9999999
                    or str(foreign_tenant) == str(tenant)
                ):
                    raise CollectionError("Invalid foreign tenant identity")
                foreign_records = _rows(
                    cur,
                    sql.SQL("SELECT id,revision FROM {} WHERE id=%s LIMIT 10001").format(
                        sql.Identifier(f"xt_t{foreign_seq}", model)
                    ),
                    (foreign_id,),
                )
                changes = _rows(
                    cur,
                    """SELECT count(*) AS changes FROM xt_audit.event
                    WHERE tenant_id=%s AND model=%s AND record_id=%s AND at>=%s AND at<=%s""",
                    (foreign_tenant, model, foreign_id, since, until),
                )
                if len(changes) != 1:
                    raise CollectionError("Foreign audit evidence unavailable")
                result["foreign"] = {
                    "tenant_id": foreign_tenant,
                    "tenant": foreign_alias,
                    "ref": ref,
                    "records": foreign_records,
                    "changes": changes[0]["changes"],
                }
            for key in ("operations", "movements", "decisions", "workflow", "stock_changes"):
                if len(result[key]) > _LIMIT:
                    raise CollectionError("Evidence row limit exceeded")
            return dict(_json(result))
    except psycopg.Error:
        raise CollectionError("Database evidence collection failed") from None
    finally:
        try:
            conn.rollback()
        except psycopg.Error:
            raise CollectionError("Database rollback failed") from None
        finally:
            conn.close()
