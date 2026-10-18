# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Plan and apply boundaries for app-disabled incident recovery."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from tests.unit.composition.test_pack_lifecycle import FakeConn, T, row
from xt.adapters.db.integration import AdminError
from xt.entrypoints import admin
from xt.entrypoints.composition import pack_lifecycle as lc

INCIDENT = {
    "instance": "waiting-instance",
    "incident": str(uuid4()),
    "model": "crm_m",
    "reason": "app_disabled",
    "pack": "crm",
    "replay_digest": "sha256:approved-replay",
}


def test_enable_dry_run_lists_incidents_and_binds_them_to_confirmation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = FakeConn()
    monkeypatch.setattr(admin, "connect", lambda dsn: conn)
    monkeypatch.setattr(admin, "tenant_of_alias", lambda conn, alias: T)
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: [row("crm", "disabled")])
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [INCIDENT])
    argv = ["pack", "enable", "--tenant", "acme", "crm", "--proposed-by", "a",
            "--approved-by", "b"]  # fmt: skip
    assert admin.run(argv, {"XT_DB_MIGRATOR_URL": "unused"}) == 0
    output = capsys.readouterr().out
    assert "waiting-instance" in output and INCIDENT["incident"] in output
    assert "eligible=1" in output and "CHƯA áp" in output
    assert conn.sql == []
    with_incidents = lc.plan_lifecycle(
        [row("crm", "disabled")],
        tenant=T,
        alias="acme",
        pack="crm",
        action="enable",
        incidents=[INCIDENT],
    )
    without = lc.plan_lifecycle(
        [row("crm", "disabled")], tenant=T, alias="acme", pack="crm", action="enable"
    )
    assert with_incidents.digest != without.digest


def test_enable_rechecks_incidents_and_recovers_after_status_commit_under_session_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConn()
    rows = [row("crm", "disabled")]
    monkeypatch.setattr(lc, "connect", lambda dsn: conn)
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: rows)
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [INCIDENT])
    plan = lc.read_lifecycle_plan(conn, tenant=T, alias="acme", pack="crm", action="enable")
    saved: list[str] = []
    monkeypatch.setattr(lc, "set_pack_status", lambda *a, **kw: saved.append(kw["status"]))

    def recover(*args: Any, **kwargs: Any) -> dict[str, int]:
        assert saved == ["enabled"]
        assert "pg_advisory_lock(" in conn.sql[0][0]
        assert not any("pg_advisory_unlock" in q for q, _ in conn.sql)
        assert kwargs["tenant"] == T and kwargs["pack"] == "crm"
        assert kwargs["incidents"] == [INCIDENT]
        return {"retried": 1, "failed": 0, "skipped": 0}

    monkeypatch.setattr(lc, "recover_app_incidents", recover)
    out = lc.apply_lifecycle(
        "migrator",
        tenant=T,
        alias="acme",
        pack="crm",
        action="enable",
        confirm=plan.digest,
        proposed_by="a",
        approved_by="b",
        app_dsn="app",
    )
    assert (out.retried, out.failed, out.skipped) == (1, 0, 0)
    assert "pg_advisory_unlock" in conn.sql[-1][0]


def test_incident_drift_or_missing_app_connection_cannot_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConn()
    rows = [row("crm", "disabled")]
    monkeypatch.setattr(lc, "connect", lambda dsn: conn)
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: rows)
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [])
    old = lc.read_lifecycle_plan(conn, tenant=T, alias="acme", pack="crm", action="enable")
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [INCIDENT])
    saved: list[Any] = []
    monkeypatch.setattr(lc, "set_pack_status", lambda *a, **kw: saved.append(kw))
    common: Any = dict(
        tenant=T, alias="acme", pack="crm", action="enable", proposed_by="a", approved_by="b"
    )
    with pytest.raises(AdminError, match="mã xác nhận"):
        lc.apply_lifecycle("m", confirm=old.digest, app_dsn="app", **common)
    current = lc.read_lifecycle_plan(conn, tenant=T, alias="acme", pack="crm", action="enable")
    with pytest.raises(AdminError, match="XT_DB_APP_URL"):
        lc.apply_lifecycle("m", confirm=current.digest, **common)
    assert saved == []
    assert "pg_advisory_unlock" in conn.sql[-1][0]


def test_enabled_pack_can_plan_retry_without_another_status_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = FakeConn()
    rows = [row("crm", status_digest="sha256:previous")]
    monkeypatch.setattr(lc, "connect", lambda dsn: conn)
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: rows)
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [])
    monkeypatch.setattr(lc, "set_pack_status", lambda *a, **kw: pytest.fail("duplicate UPDATE"))
    plan = lc.read_lifecycle_plan(conn, tenant=T, alias="acme", pack="crm", action="enable")
    assert not plan.problems
    for digest in (plan.digest, "sha256:previous"):
        out = lc.apply_lifecycle(
            "m",
            tenant=T,
            alias="acme",
            pack="crm",
            action="enable",
            confirm=digest,
            proposed_by="a",
            approved_by="b",
        )
        assert (out.retried, out.failed, out.skipped) == (0, 0, 0)
