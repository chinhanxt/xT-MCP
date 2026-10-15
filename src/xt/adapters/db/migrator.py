# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Migration executor: probe dữ liệu, diễn tập, apply 2 pha theo từng tenant, worker pha 2.

- Pha 1 (một transaction/tenant): advisory lock `xt_meta:catalog:<seq>`, `lock_timeout`,
  `statement_timeout`; LẬP LẠI kế hoạch trên dữ liệu hiện tại, `check_gate` (plan_hash, BLOCKED,
  xác nhận từng mã DESTRUCTIVE); chạy DDL với CHECK/FK `NOT VALID`; ghi `catalog_version`;
  enqueue job pha 2 trong CÙNG transaction (commit pha 1 ⇒ chắc chắn có job).
- Pha 2 (worker): theo (tenant, version, ord); `post` trong transaction ngắn, `concurrent` ở
  autocommit; `lock_timeout` > `deadlock_timeout` (autovacuum chặn VALIDATE, SMT §2); lỗi thì
  retry, quá `max_attempts` thì `dead`; job sau của cùng tenant/version bị giữ lại; index
  INVALID còn sót bị xóa trước khi tạo lại.
- Kết nối dùng role `xt_migrator` (BYPASSRLS để đếm vi phạm), autocommit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from xt.adapters.db.compiler import (
    PG_TYPE,
    CompiledStep,
    Layout,
    Step,
    check_expr,
    compile_plan,
    lit,
)
from xt.adapters.db.connection import AuditKey, set_audit_hmacs
from xt.adapters.db.postgrest import notify_postgrest
from xt.adapters.db.records import translate
from xt.application.model_engine.ports import ApplyOutcome, JobStats
from xt.domain.model.audit import audit_hmac
from xt.domain.model.checks import checks_by_name
from xt.domain.model.context import MAX_MODEL_TABLES_PER_DB, TenantRef
from xt.domain.model.errors import ModelError
from xt.domain.model.plan import (
    CheckViolations,
    Duplicates,
    NullCount,
    Plan,
    PlanRejected,
    Probe,
    ProbeResult,
    RiskClass,
    RowCount,
    build_plan,
    check_gate,
    diff_catalogs,
    probes,
)
from xt.domain.model.spec import Catalog, FieldSpec

Connect = Callable[[], psycopg.Connection[Any]]
SAMPLE = 3


class Prober:
    """Đếm trên dữ liệu HIỆN TẠI (tên bảng/cột cũ) của một tenant."""

    def __init__(
        self, conn: psycopg.Connection[Any], lay: Layout, old: Catalog | None, new: Catalog
    ):
        self.conn, self.lay = conn, lay
        self.old = old.by_id() if old else {}
        self.new = new.by_id()

    def _run(self, q: sql.Composable) -> ProbeResult:
        row = self.conn.execute(
            sql.SQL("SELECT count(*), (array_agg(id::text ORDER BY id))[1:{}] FROM ({}) v").format(
                sql.Literal(SAMPLE), q
            )
        ).fetchone()
        assert row is not None
        return ProbeResult(int(row[0]), tuple(row[1] or ()))

    def run(self, p: Probe) -> ProbeResult:
        o = self.old.get(p.model_id)
        if o is None:  # model mới: chưa có dữ liệu
            return ProbeResult(0)
        t = self.lay.table(o.name)
        if isinstance(p, RowCount):
            return self._run(sql.SQL("SELECT id FROM {}").format(t))
        if isinstance(p, NullCount):
            col = sql.Identifier(o.by_id()[p.field_id].name)
            return self._run(sql.SQL("SELECT id FROM {} WHERE {} IS NULL").format(t, col))
        if isinstance(p, Duplicates):
            ofs = o.by_id()
            cols = [sql.Identifier(ofs[x].name) for x in p.field_ids]
            nn = sql.SQL(" AND ").join(sql.SQL("{} IS NOT NULL").format(c) for c in cols)
            return self._run(
                sql.SQL(
                    "SELECT id FROM (SELECT id, count(*) OVER (PARTITION BY tenant_id, {}) n"
                    " FROM {} WHERE {}) d WHERE n > 1"
                ).format(sql.SQL(", ").join(cols), t, nn)
            )
        assert isinstance(p, CheckViolations)
        n = self.new[p.model_id]
        c = checks_by_name(n)[p.check_name]
        old_by_id = o.by_id()

        def resolve(f: FieldSpec) -> sql.Composable:
            if f.id in old_by_id:
                return sql.Identifier(old_by_id[f.id].name)
            if f.default is not None:
                return sql.SQL("{}::{}").format(lit(f, f.default), sql.SQL(_pg_type(f)))
            return sql.SQL("NULL::{}").format(sql.SQL(_pg_type(f)))

        expr = check_expr(n, c, resolve)
        return self._run(sql.SQL("SELECT id FROM {} WHERE ({}) IS FALSE").format(t, expr))


def _pg_type(f: FieldSpec) -> str:
    return PG_TYPE[f.type]


def load_current(conn: psycopg.Connection[Any], tenant_seq: int) -> Catalog | None:
    row = conn.execute(
        "SELECT spec FROM xt_meta.catalog_version WHERE tenant_seq = %s ORDER BY version DESC"
        " LIMIT 1",
        (tenant_seq,),
    ).fetchone()
    return Catalog.model_validate(row[0]) if row else None


def _exec(conn: psycopg.Connection[Any], st: Step) -> None:
    if st.role:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(st.role)))
        try:
            conn.execute(st.sql)
        finally:
            if conn.info.transaction_status != psycopg.pq.TransactionStatus.INERROR:
                conn.execute("RESET ROLE")
    else:
        conn.execute(st.sql)


class PgMigrator:
    def __init__(
        self,
        connect: Connect,
        *,
        lock_timeout: str = "3s",
        post_lock_timeout: str = "5s",
        statement_timeout: str = "60s",
        max_attempts: int = 3,
        max_tables: int = MAX_MODEL_TABLES_PER_DB,
        plan_statement_timeout: str = "5s",
        backfill_batch: int = 1000,
        audit_key: AuditKey | None = None,
    ) -> None:
        self.connect = connect
        self.plan_statement_timeout = plan_statement_timeout
        self.backfill_batch = backfill_batch
        self.audit_key = audit_key
        self.lock_timeout = lock_timeout
        self.post_lock_timeout = post_lock_timeout
        self.statement_timeout = statement_timeout
        self.max_attempts = max_attempts
        self.max_tables = max_tables

    @contextmanager
    def _conn(self) -> Iterator[psycopg.Connection[Any]]:
        conn = self.connect()
        try:
            if not conn.autocommit:
                raise RuntimeError("kết nối migrator phải autocommit=True")
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------ đọc ----
    def current(self, tenant: TenantRef) -> Catalog | None:
        with self._conn() as conn:
            return load_current(conn, tenant.seq)

    def version_distribution(self) -> dict[int, list[int]]:
        """version → [tenant_seq]; tenant chưa có catalog nằm ở version 0. Lệch là hợp lệ."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT t.seq, coalesce(max(c.version), 0) FROM xt_meta.tenant t"
                " LEFT JOIN xt_meta.catalog_version c ON c.tenant_seq = t.seq GROUP BY t.seq"
                " ORDER BY 1"
            ).fetchall()
        out: dict[int, list[int]] = {}
        for seq, ver in rows:
            out.setdefault(int(ver), []).append(int(seq))
        return out

    def _readers(self, conn: psycopg.Connection[Any], tenant_seq: int) -> list[str]:
        rows = conn.execute(
            "SELECT l.login FROM xt_meta.login_tenant l JOIN xt_meta.tenant t ON t.id = l.tenant_id"
            " WHERE t.seq = %s ORDER BY 1",
            (tenant_seq,),
        ).fetchall()
        return [str(r[0]) for r in rows]

    def _plan_on(
        self, conn: psycopg.Connection[Any], tenant: TenantRef, old: Catalog | None, new: Catalog
    ) -> Plan:
        lay = Layout(tenant.seq)
        prober = Prober(conn, lay, old, new)
        changes = diff_catalogs(old, new)
        results = {cid: prober.run(p) for cid, p in probes(changes)}
        try:
            plan = build_plan(old, new, results)
        except ValueError as e:  # version không nối tiếp: catalog đã đổi kể từ lúc đề xuất
            raise PlanRejected(
                "plan_changed",
                f"Catalog của đơn vị đã đổi kể từ lúc lập đề xuất ({e}) — cần lập lại kế hoạch",
                {"current_version": old.version if old else 0},
            ) from e
        added = sum(1 for c in plan.changes if c.kind == "add_model")
        if added:
            row = conn.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE c.relkind = 'r' AND n.nspname LIKE 'xt\\_t%'"
            ).fetchone()
            existing = int(row[0]) if row else 0
            if existing + added > self.max_tables:
                raise ModelError(
                    "resource_constraint",
                    f"Vượt trần {self.max_tables} bảng model mỗi CSDL (đang có {existing}, thêm"
                    f" {added}) — cần thêm CSDL cho tenant mới",
                    details={"existing": existing, "adding": added, "limit": self.max_tables},
                )
        return plan

    def plan(self, tenant: TenantRef, new: Catalog, *, rehearse: bool) -> Plan:
        try:
            return self._plan(tenant, new, rehearse)
        except psycopg.Error as e:
            raise translate(e) from e

    def _plan(self, tenant: TenantRef, new: Catalog, rehearse: bool) -> Plan:
        with self._conn() as conn:
            # dry-run do agent gọi được: mọi truy vấn probe/diễn tập có trần thời gian (AUD-02)
            conn.execute(
                sql.SQL("SET statement_timeout = {}").format(
                    sql.Literal(self.plan_statement_timeout)
                )
            )
            old = load_current(conn, tenant.seq)
            p = self._plan_on(conn, tenant, old, new)
            if rehearse:
                self._rehearse(conn, tenant, p, old, new)
            return p

    def _rehearse(
        self,
        conn: psycopg.Connection[Any],
        tenant: TenantRef,
        p: Plan,
        old: Catalog | None,
        new: Catalog,
    ) -> None:
        """Chạy thử các bước DDL 'tx' trong transaction rồi ROLLBACK; lỗi gốc ghi vào thao tác.
        KHÔNG chạy bước sửa dữ liệu (backfill nằm ở pha 'backfill', số dòng lấy từ probe)."""
        by_id = {c.id: c for c in p.changes}
        lay = Layout(tenant.seq)
        steps = [
            s
            for s in compile_plan(lay, p, old, new, self._readers(conn, tenant.seq))
            if s.step.phase == "tx" and not s.step.data
        ]
        try:
            with conn.transaction(force_rollback=True):
                conn.execute(
                    sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(self.lock_timeout))
                )
                for cs in steps:
                    try:
                        with conn.transaction():
                            _exec(conn, cs.step)
                    except psycopg.errors.LockNotAvailable:
                        raise  # khóa bận không phải lỗi của kế hoạch: báo Busy, không BLOCKED
                    except psycopg.Error as e:
                        c = by_id.get(cs.change_id)
                        if c is None:  # bước view: gán cho thay đổi đầu tiên của model
                            mid = cs.change_id.split(":", 1)[1]
                            c = next((x for x in p.changes if x.model_id == mid), None)
                        if c is not None and c.rehearsal_error is None:
                            c.rehearsal_error = (e.diag.message_primary or str(e))[:200]
                            c.cls = RiskClass.BLOCKED
        except psycopg.errors.LockNotAvailable:
            raise
        except psycopg.Error:
            pass

    # ---------------------------------------------------------------- apply ----
    def apply(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        *,
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome:
        try:
            return self._apply(tenant, new, approved_hash, confirm, proposed_by, approved_by)
        except psycopg.Error as e:
            raise translate(e) from e

    def _apply(
        self,
        tenant: TenantRef,
        new: Catalog,
        approved_hash: str,
        confirm: set[str],
        proposed_by: str,
        approved_by: str,
    ) -> ApplyOutcome:
        with self._conn() as conn, conn.transaction():
            conn.execute(
                sql.SQL("SET LOCAL lock_timeout = {}").format(sql.Literal(self.lock_timeout))
            )
            conn.execute(
                sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(self.statement_timeout)
                )
            )
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('xt_meta:catalog:' || %s, 0))",
                (str(tenant.seq),),
            )
            self._require_post_done(conn, tenant.seq)
            old = load_current(conn, tenant.seq)
            p = self._plan_on(conn, tenant, old, new)  # lập lại trên dữ liệu hiện tại
            check_gate(p, approved_hash, confirm)
            lay = Layout(tenant.seq)
            steps = compile_plan(lay, p, old, new, self._readers(conn, tenant.seq))
            tx = [s for s in steps if s.step.phase == "tx"]
            post = [
                s for ph in ("backfill", "post", "concurrent") for s in steps if s.step.phase == ph
            ]
            for cs in tx:
                _exec(conn, cs.step)
            spec = new.dump()
            conn.execute(
                "INSERT INTO xt_meta.catalog_version (tenant_seq, version, spec, spec_hash, plan,"
                " plan_hash, proposed_by, approved_by, post_state) VALUES"
                " (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    tenant.seq, new.version, Jsonb(spec), new.digest(), Jsonb(p.to_json()), p.hash,
                    proposed_by, approved_by, "pending" if post else "validated",
                ),
            )  # fmt: skip
            self._enqueue(conn, tenant.seq, new.version, post)
            notify_postgrest(conn)
            conn.execute("SELECT pg_notify('xt_catalog', %s)", (f"{tenant.seq}:{new.version}",))
        return ApplyOutcome(tenant.seq, new.version, p.hash, len(tx), len(post))

    @staticmethod
    def _require_post_done(conn: psycopg.Connection[Any], seq: int) -> None:
        """Catalog phải bằng trạng thái CSDL: còn BẤT KỲ job pha 2 nào (mọi version) chưa xong
        thì không áp version mới (R2-02)."""
        row = conn.execute(
            "SELECT min(version), count(*) FILTER (WHERE status = 'dead'), count(*)"
            " FROM xt_meta.apply_job WHERE tenant_seq = %s AND status <> 'done'",
            (seq,),
        ).fetchone()
        if row is None or not row[2]:
            return
        dead = int(row[1])
        raise ModelError(
            "precondition_failed",
            (
                f"Phiên bản v{row[0]} còn bước hậu kỳ thất bại (ràng buộc/index chưa xác thực):"
                " catalog và CSDL đang lệch — sửa dữ liệu rồi gọi retry_failed trước khi áp"
                " phiên bản mới"
            )
            if dead
            else f"Phiên bản v{row[0]} còn {row[2]} bước hậu kỳ đang chờ worker; chạy xong mới áp"
            " phiên bản mới",
            details={"reason": "post_failed" if dead else "post_pending", "version": int(row[0])},
        )

    def _enqueue(
        self, conn: psycopg.Connection[Any], seq: int, version: int, post: list[CompiledStep]
    ) -> None:
        for i, cs in enumerate(post):
            st = cs.step
            hm: dict[str, dict[str, str]] | None = None
            if st.audit_new:
                if self.audit_key is None:
                    raise ModelError(
                        "precondition_failed",
                        "Backfill field sensitive cần khóa HMAC audit (AuditKey) ở migrator",
                        details={"reason": "audit_key_missing"},
                    )
                k = self.audit_key
                hm = {"new": {c: audit_hmac(k.key, k.kid, v) for c, v in st.audit_new}}
            conn.execute(
                "INSERT INTO xt_meta.apply_job (tenant_seq, version, ord, phase, change_id, sql,"
                " index_schema, index_name, audit_hmac) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    seq, version, i, st.phase, cs.change_id, st.text(),
                    st.index[0] if st.index else None, st.index[1] if st.index else None,
                    Jsonb(hm) if hm else None,
                ),
            )  # fmt: skip

    # --------------------------------------------------------------- pha 2 ----
    def run_jobs(self, tenant_seq: int | None = None) -> JobStats:
        """Chạy job pha 2. Mỗi tenant do đúng MỘT worker xử lý tại một thời điểm: claim bằng
        advisory lock phiên theo tenant (`pg_try_advisory_lock`), worker khác bỏ qua tenant đó
        (R2-03). Trong một tenant, job chạy theo (version, ord); lỗi thì dừng tenant đó."""
        stats = JobStats()
        with self._conn() as conn:
            seqs = [
                int(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT tenant_seq FROM xt_meta.apply_job"
                    " WHERE status IN ('pending', 'failed')"
                    " AND (%s::int IS NULL OR tenant_seq = %s)"
                    " ORDER BY 1",
                    (tenant_seq, tenant_seq),
                )
            ]
            for seq in seqs:
                got = conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended('xt_meta:jobs:' || %s, 0))",
                    (str(seq),),
                ).fetchone()
                if not got or not got[0]:
                    continue  # worker khác đang giữ tenant này
                try:
                    self._run_tenant(conn, seq, stats)
                finally:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtextextended('xt_meta:jobs:' || %s, 0))",
                        (str(seq),),
                    )
        return stats

    def _run_tenant(self, conn: psycopg.Connection[Any], seq: int, stats: JobStats) -> None:
        jobs = conn.execute(
            "SELECT id, version, phase, sql, attempts, index_schema, index_name, audit_hmac"
            " FROM xt_meta.apply_job WHERE tenant_seq = %s AND status IN ('pending', 'failed')"
            " ORDER BY version, ord",
            (seq,),
        ).fetchall()
        touched: set[int] = set()
        for jid, ver, phase, q, att, ix_schema, ix_name, hm in jobs:
            touched.add(int(ver))
            try:
                self._run_one(conn, int(jid), str(phase), str(q), ix_schema, ix_name, hm)
                conn.execute(
                    "UPDATE xt_meta.apply_job SET status = 'done', attempts = attempts + 1,"
                    " last_error = NULL, updated_at = now() WHERE id = %s",
                    (jid,),
                )
                stats.done += 1
            except psycopg.Error as e:
                status = "dead" if int(att) + 1 >= self.max_attempts else "failed"
                conn.execute(
                    "UPDATE xt_meta.apply_job SET status = %s, attempts = attempts + 1,"
                    " last_error = %s, updated_at = now() WHERE id = %s",
                    (status, (e.diag.message_primary or str(e))[:300], jid),
                )
                if status == "dead":
                    stats.dead += 1
                    if ix_schema and ix_name:  # không để index INVALID nửa vời
                        self._drop_invalid(conn, str(ix_schema), str(ix_name))
                else:
                    stats.failed += 1
                break  # thứ tự có ý nghĩa: dừng tenant này
        for ver in sorted(touched):
            row = conn.execute(
                "SELECT count(*) FILTER (WHERE status <> 'done'),"
                " count(*) FILTER (WHERE status = 'dead')"
                " FROM xt_meta.apply_job WHERE tenant_seq = %s AND version = %s",
                (seq, ver),
            ).fetchone()
            assert row is not None
            state = "validated" if row[0] == 0 else "failed" if row[1] else "pending"
            if state != "validated":
                stats.pending_versions.append((seq, ver))
            conn.execute(
                "UPDATE xt_meta.catalog_version SET post_state = %s"
                " WHERE tenant_seq = %s AND version = %s",
                (state, seq, ver),
            )

    def _run_one(
        self,
        conn: psycopg.Connection[Any],
        jid: int,
        phase: str,
        q: str,
        ix_schema: str | None,
        ix_name: str | None,
        audit_hmacs: Any = None,
    ) -> None:
        lt = sql.Literal(self.post_lock_timeout)
        if phase == "backfill":
            # theo lô, mỗi lô một transaction ngắn; tiến độ ghi vào apply_job.progress
            while True:
                with conn.transaction():
                    conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(lt))
                    set_audit_hmacs(conn, audit_hmacs)
                    n = conn.execute(q.encode(), prepare=False).rowcount
                    conn.execute(
                        "UPDATE xt_meta.apply_job SET progress = progress + %s, updated_at = now()"
                        " WHERE id = %s",
                        (max(n, 0), jid),
                    )
                if n <= 0:
                    return
        if phase == "post":
            with conn.transaction():
                conn.execute(sql.SQL("SET LOCAL lock_timeout = {}").format(lt))
                conn.execute(q.encode())
            return
        conn.execute(sql.SQL("SET lock_timeout = {}").format(lt))
        try:
            if ix_schema and ix_name:
                self._drop_invalid(conn, ix_schema, ix_name)
            conn.execute(q.encode())
        finally:
            conn.execute("RESET lock_timeout")

    @staticmethod
    def _drop_invalid(conn: psycopg.Connection[Any], schema: str, name: str) -> None:
        bad = conn.execute(
            "SELECT NOT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relname = %s",
            (schema, name),
        ).fetchone()
        if bad and bad[0]:
            conn.execute(
                sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(sql.Identifier(schema, name))
            )

    # ------------------------------------------------- trạng thái CSDL ↔ catalog ----
    @staticmethod
    def _post_state(conn: psycopg.Connection[Any], seq: int) -> tuple[int, str] | None:
        row = conn.execute(
            "SELECT version, post_state FROM xt_meta.catalog_version WHERE tenant_seq = %s"
            " ORDER BY version DESC LIMIT 1",
            (seq,),
        ).fetchone()
        return (int(row[0]), str(row[1])) if row else None

    def post_state(self, tenant: TenantRef) -> tuple[int, str] | None:
        """(version mới nhất, validated|pending|failed) — failed = catalog và CSDL đang lệch."""
        with self._conn() as conn:
            return self._post_state(conn, tenant.seq)

    def retry_failed(self, tenant: TenantRef) -> JobStats:
        """Sau khi đã sửa dữ liệu: đưa job dead/failed của tenant về pending rồi chạy lại."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE xt_meta.apply_job SET status = 'pending', attempts = 0, last_error = NULL"
                " WHERE tenant_seq = %s AND status IN ('dead', 'failed')",
                (tenant.seq,),
            )
        return self.run_jobs(tenant.seq)

    def reconcile(self) -> dict[int, str]:
        """Chạy lúc khởi động worker: đối chiếu trạng thái thật của CSDL với catalog. Version mới
        nhất còn index INVALID / constraint NOT VALID / job dead mà không còn job chờ → failed."""
        out: dict[int, str] = {}
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (tenant_seq) tenant_seq, version, post_state"
                " FROM xt_meta.catalog_version ORDER BY tenant_seq, version DESC"
            ).fetchall()
            for seq, ver, state in rows:
                schema = f"xt_t{int(seq)}"
                r = conn.execute(
                    "SELECT (SELECT count(*) FROM pg_index i JOIN pg_class c ON c.oid ="
                    " i.indexrelid"
                    "  JOIN pg_namespace n ON n.oid = c.relnamespace"
                    "  WHERE n.nspname = %s AND NOT i.indisvalid),"
                    " (SELECT count(*) FROM pg_constraint k JOIN pg_namespace n"
                    "  ON n.oid = k.connamespace WHERE n.nspname = %s AND NOT k.convalidated),"
                    # mọi version của tenant, không chỉ version mới nhất (R2-02)
                    " (SELECT count(*) FROM xt_meta.apply_job WHERE tenant_seq = %s"
                    "  AND status IN ('pending', 'failed')),"
                    " (SELECT count(*) FROM xt_meta.apply_job WHERE tenant_seq = %s"
                    "  AND status = 'dead')",
                    (schema, schema, seq, seq),
                ).fetchone()
                assert r is not None
                invalid, not_valid, waiting, dead = (int(x) for x in r)
                if dead:
                    new_state = "failed"
                elif waiting:
                    new_state = "pending"
                elif invalid or not_valid:
                    new_state = "failed"
                else:
                    new_state = "validated"
                if new_state != state:
                    conn.execute(
                        "UPDATE xt_meta.catalog_version SET post_state = %s"
                        " WHERE tenant_seq = %s AND version = %s",
                        (new_state, seq, ver),
                    )
                out[int(seq)] = new_state
        return out
