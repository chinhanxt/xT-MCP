# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPA-02: cache phát lại DPoP dùng chung trên PG17 thật (`xt_identity.dpop_jti`, migration 0013).
Hai "tiến trình" (hai pool riêng) và một lần restart (pool mới) thấy cùng tập jti; dọn mục hết hạn
qua job relay của worker; xt_app không đụng bảng trực tiếp; DB lỗi -> fail-closed 503."""

from __future__ import annotations

import time
from types import SimpleNamespace

import psycopg
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.fixtures import StackBase
from tests.unit.identity.dpop_client import DpopKey
from xt.adapters.db.connection import Database
from xt.adapters.db.dpop_replay import PgDpopReplayStore
from xt.adapters.identity import DpopError, DpopSettings, DpopVerifier
from xt.domain.identity import IdentityProviderUnavailableError
from xt.entrypoints.composition.worker import WorkflowJobs

pytestmark = pytest.mark.integration
ORIGIN = "https://xt.example.test"
HTU = f"{ORIGIN}/mcp"
TOKEN = "at-value"


def verifier(db: Database) -> DpopVerifier:
    s = DpopSettings(origins=(ORIGIN,), replay_store="postgres")
    return DpopVerifier(s, PgDpopReplayStore(db))


def check(v: DpopVerifier, proof: str, key: DpopKey) -> str:
    return v.verify([proof], htm="POST", htu={HTU}, bound_to=TOKEN, expected_jkt=key.jkt)


def test_replay_shared_across_processes_and_restart(
    stack_base: StackBase, pg: pgfixture.Pg
) -> None:
    key = DpopKey()
    a, b = Database(pg.app_dsn, max_size=2), Database(pg.app_dsn, max_size=2)
    try:
        p = key.proof("POST", HTU, token=TOKEN)
        assert check(verifier(a), p, key) == key.jkt
        with pytest.raises(DpopError) as ei:  # worker/replica thứ hai
            check(verifier(b), p, key)
        assert ei.value.reason == "dpop_replay"
        a.close()
        c = Database(pg.app_dsn, max_size=2)  # "restart" tiến trình A
        try:
            with pytest.raises(DpopError):
                check(verifier(c), p, key)
            # jti trùng nhưng khóa khác: không đụng nhau (khóa (jkt, jti))
            other = DpopKey()
            same_jti = key.proof("POST", HTU, token=TOKEN, jti="shared-jti")
            assert check(verifier(c), same_jti, key)
            assert check(
                verifier(b), other.proof("POST", HTU, token=TOKEN, jti="shared-jti"), other
            )
        finally:
            c.close()
    finally:
        b.close()


def test_purge_and_expired_slot_reuse(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    db = Database(pg.app_dsn, max_size=2)
    try:
        store = PgDpopReplayStore(db)
        past = int(time.time()) - 5
        assert store.consume("dpop|jkt-purge|j1", past)
        future = int(time.time()) + 60
        assert store.consume("dpop|jkt-purge|j1", future)  # mục cũ hết hạn: thay được
        assert not store.consume("dpop|jkt-purge|j1", future)
        assert store.consume("dpop|jkt-purge|j|with|pipes", int(time.time()) + 60)
        assert store.consume("dpop|jkt-purge|j2", past)
        jobs = SimpleNamespace(rt=SimpleNamespace(app_db=db))
        assert WorkflowJobs.purge_dpop_jti(jobs) >= 1  # type: ignore[arg-type]
        with pg.su() as su:
            rows = su.execute(
                "SELECT jti FROM xt_identity.dpop_jti WHERE jkt = 'jkt-purge' ORDER BY jti"
            ).fetchall()
        assert [r[0] for r in rows] == ["j1", "j|with|pipes"]  # j2 (hết hạn) đã dọn
    finally:
        db.close()


def test_app_role_cannot_touch_table_directly(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    with pg.app() as conn:
        for sql in (
            "SELECT * FROM xt_identity.dpop_jti",
            "DELETE FROM xt_identity.dpop_jti",
            "INSERT INTO xt_identity.dpop_jti VALUES ('a', 'b', now() + interval '1 day')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)
            conn.rollback()


def test_store_failure_is_fail_closed(stack_base: StackBase, pg: pgfixture.Pg) -> None:
    db = Database(pg.app_dsn, max_size=1)
    v = verifier(db)
    db.close()
    key = DpopKey()
    with pytest.raises(IdentityProviderUnavailableError):
        check(v, key.proof("POST", HTU, token=TOKEN), key)
