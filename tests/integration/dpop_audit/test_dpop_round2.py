# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit DPoP vòng 2 (docs/audit-dpop.md "Vòng 2") trên PG17 thật: replay giữa hai TIẾN TRÌNH OS
thật và ngay sau restart, đua đồng thời, DB lỗi/treo, quyền SQL của `xt_identity`, đua purge."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import psycopg
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import StackBase
from tests.unit.identity.dpop_client import DpopKey
from xt.adapters.db.connection import Database
from xt.adapters.db.dpop_replay import PgDpopReplayStore

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
HTU = "https://xt.example.test/mcp"
TOKEN = "at-value"

# Một "tiến trình xt-api": pool riêng, verifier riêng, kiểm một proof rồi thoát.
WORKER = """
import json, sys
from xt.adapters.db.connection import Database
from xt.adapters.db.dpop_replay import PgDpopReplayStore
from xt.adapters.identity import DpopError, DpopSettings, DpopVerifier
dsn, proof, jkt = sys.argv[1:4]
db = Database(dsn, max_size=1)
v = DpopVerifier(DpopSettings(origins=("https://xt.example.test",), replay_store="postgres"),
                 PgDpopReplayStore(db))
try:
    v.verify([proof], htm="POST", htu={"https://xt.example.test/mcp"}, bound_to="at-value",
             expected_jkt=jkt)
    print(json.dumps("ok"))
except DpopError as e:
    print(json.dumps(e.reason))
finally:
    db.close()
"""


def _process(dsn: str, proof: str, jkt: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", WORKER, dsn, proof, jkt],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return str(json.loads(out.stdout.strip().splitlines()[-1]))


def test_replay_across_two_os_processes_and_after_restart(
    stack_base: StackBase, pg: pgfixture.Pg
) -> None:
    key = DpopKey()
    p = key.proof("POST", HTU, token=TOKEN)
    assert _process(pg.app_dsn, p, key.jkt) == "ok"  # tiến trình A
    assert _process(pg.app_dsn, p, key.jkt) == "dpop_replay"  # tiến trình B
    assert _process(pg.app_dsn, p, key.jkt) == "dpop_replay"  # A "restart" (tiến trình mới)


def test_concurrent_consume_from_separate_pools_exactly_one_wins(
    stack_base: StackBase, pg: pgfixture.Pg
) -> None:
    pools = [Database(pg.app_dsn, max_size=1) for _ in range(6)]
    try:
        stores = [PgDpopReplayStore(d) for d in pools]
        exp = int(time.time()) + 120
        for i in range(25):
            key = f"dpop|race-jkt|race-{i}"
            barrier = threading.Barrier(len(stores))
            wins: list[bool] = []
            lock = threading.Lock()

            def go(s: PgDpopReplayStore, k: str = key, b: threading.Barrier = barrier) -> None:
                b.wait()
                r = s.consume(k, exp)
                with lock:
                    wins.append(r)

            ts = [threading.Thread(target=go, args=(s,)) for s in stores]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            assert wins.count(True) == 1, (i, wins)
    finally:
        for d in pools:
            d.close()


def test_sql_privileges_on_xt_identity(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    with pg.su() as su:
        roles = [
            r[0]
            for r in su.execute(
                "SELECT rolname FROM pg_roles WHERE NOT rolsuper AND rolname NOT LIKE 'pg\\_%'"
            ).fetchall()
        ]
        table_rw: list[str] = []
        callers: list[str] = []
        for role in roles:
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                if su.execute(
                    "SELECT has_table_privilege(%s, 'xt_identity.dpop_jti', %s)", (role, priv)
                ).fetchone()[0]:  # type: ignore[index]
                    table_rw.append(f"{role}:{priv}")
            for fn in ("dpop_jti_consume(text,text,timestamptz)", "dpop_jti_purge(integer)"):
                if su.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, f"xt_identity.{fn}")
                ).fetchone()[0]:  # type: ignore[index]
                    callers.append(f"{role}:{fn.split('(')[0]}")
        owner = su.execute(
            "SELECT tableowner FROM pg_tables WHERE schemaname='xt_identity' AND tablename='dpop_jti'"
        ).fetchone()[0]  # type: ignore[index]
        member = su.execute("SELECT pg_has_role('xt_app', %s, 'MEMBER')", (owner,)).fetchone()[0]  # type: ignore[index]
    assert not member, "xt_app là thành viên của chủ bảng"
    assert all(r.split(":")[0] == owner for r in table_rw), table_rw
    assert {c.split(":")[0] for c in callers} <= {"xt_app", owner}, callers
    with pg.app() as app:
        for sql in (
            "SELECT count(*) FROM xt_identity.dpop_jti",
            "DELETE FROM xt_identity.dpop_jti",
            "TRUNCATE xt_identity.dpop_jti",
            "UPDATE xt_identity.dpop_jti SET expires_at = now()",
            "CREATE TABLE xt_identity.x (a int)",
            "CREATE OR REPLACE FUNCTION xt_identity.dpop_jti_purge(p_limit integer)"
            " RETURNS integer LANGUAGE sql AS 'SELECT 0'",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                app.execute(sql)
        # xt_app gọi purge: chỉ xóa mục hết hạn, không xóa được mục còn hạn của ai.
        assert app.execute(
            "SELECT xt_identity.dpop_jti_consume('priv-jkt', 'live', now() + interval '1 hour')"
        ).fetchone()[0]  # type: ignore[index]
        app.execute("SELECT xt_identity.dpop_jti_purge(1000000)")
        assert not app.execute(
            "SELECT xt_identity.dpop_jti_consume('priv-jkt', 'live', now() + interval '1 hour')"
        ).fetchone()[0]  # type: ignore[index]


def test_purge_racing_slot_reuse_never_drops_live_entry(
    stack_base: StackBase, pg: pgfixture.Pg
) -> None:
    a, b = Database(pg.app_dsn, max_size=1), Database(pg.app_dsn, max_size=1)
    try:
        sa, sb = PgDpopReplayStore(a), PgDpopReplayStore(b)
        past, future = int(time.time()) - 5, int(time.time()) + 300
        keys = [f"dpop|purge-race|k{i}" for i in range(150)]
        for k in keys:
            assert sa.consume(k, past)
        stop = threading.Event()

        def purger() -> None:
            while not stop.is_set():
                sb.purge(5)

        t = threading.Thread(target=purger)
        t.start()
        try:
            won = [k for k in keys if sa.consume(k, future)]
        finally:
            stop.set()
            t.join()
        assert won == keys  # mục hết hạn luôn thay được, dù purge chạy song song
        sb.purge()
        assert not any(sa.consume(k, future) for k in keys)  # mục còn hạn không bị purge
    finally:
        a.close()
        b.close()


def test_db_unreachable_fails_closed_503_quickly(stack_base: StackBase) -> None:
    """DB không tới được (cổng đóng): phải fail-closed (không fail-open) — và đo thời gian."""
    from xt.adapters.identity import DpopSettings, DpopVerifier
    from xt.domain.identity import IdentityProviderUnavailableError

    db = Database("host=127.0.0.1 port=1 dbname=xt user=xt_app connect_timeout=2", max_size=1)
    try:
        v = DpopVerifier(
            DpopSettings(origins=("https://xt.example.test",), replay_store="postgres"),
            PgDpopReplayStore(db),
        )
        key = DpopKey()
        t0 = time.monotonic()
        with pytest.raises(IdentityProviderUnavailableError):
            v.verify(
                [key.proof("POST", HTU, token=TOKEN)],
                htm="POST",
                htu={HTU},
                bound_to=TOKEN,
                expected_jkt=key.jkt,
            )
        elapsed = time.monotonic() - t0
    finally:
        db.close()
    print(f"DB unreachable -> 503 sau {elapsed:.1f}s")
    assert elapsed < 40


@pytest.mark.xfail(
    strict=True,
    reason="DPA-07: statement_timeout_ms=3000 của pool jti không được áp (chỉ tenant_tx đặt) -> "
    "consume treo vô hạn khi bảng bị khóa; pool timeout 30 s khi DB mất",
)
def test_consume_bounded_when_table_locked(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    from xt.entrypoints.serve import _dpop_verifier

    v, db = _dpop_verifier(
        {"XT_DPOP_REPLAY_STORE": "postgres", "XT_DB_APP_URL": pg.app_dsn, "XT_DPOP_ORIGINS": ""}
    )
    result: list[object] = []
    try:
        with pg.su() as su, su.transaction():
            su.execute("LOCK TABLE xt_identity.dpop_jti IN ACCESS EXCLUSIVE MODE")
            store = v._replay

            def go() -> None:
                try:
                    result.append(store.consume("dpop|lock-jkt|j", int(time.time()) + 60))
                except Exception as e:
                    result.append(e)

            t = threading.Thread(target=go, daemon=True)
            t.start()
            t.join(8)  # statement_timeout 3 s đã cấu hình + biên
            hung = t.is_alive()
        t.join(10)
    finally:
        db.close()
    assert not hung, "consume vẫn treo sau 8 s dù statement_timeout_ms=3000"
