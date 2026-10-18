# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời ứng dụng Tầng 3 theo tenant (G8 / U15): đã cài -> tắt -> bật lại, qua cùng luồng
kế hoạch + mã xác nhận + người duyệt khác người đề xuất như `xt pack install`; chặn phụ thuộc;
mỗi lần đổi là một UPDATE `pack_install` (trigger audit meta ghi vào hash-chain)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from xt.adapters.db.integration import AdminError
from xt.domain.model.context import TenantRef
from xt.entrypoints import admin
from xt.entrypoints.composition import pack_lifecycle as lc

T = TenantRef(1, uuid4())


def row(
    pack: str, status: str = "enabled", deps: list[str] | None = None, **kw: Any
) -> dict[str, Any]:
    return {
        "pack": pack,
        "version": "0.1.0",
        "status": status,
        "models": kw.get("models", [f"{pack.replace('-', '_')}_m"]),
        "depends_on": deps if deps is not None else [],
        **{k: v for k, v in kw.items() if k != "models"},
    }


def plan(rows: list[dict[str, Any]], pack: str, action: lc.Action) -> lc.LifecyclePlan:
    return lc.plan_lifecycle(rows, tenant=T, alias="acme", pack=pack, action=action)


def test_disable_then_enable_plans() -> None:
    p = plan([row("kho-hang")], "kho-hang", "disable")
    assert p.problems == [] and (p.from_status, p.to_status) == ("enabled", "disabled")
    assert p.digest.startswith("sha256:") and p.digest in p.render_vi()
    q = plan([row("kho-hang", "disabled")], "kho-hang", "enable")
    assert q.problems == [] and q.to_status == "enabled"


def test_dependency_guard_on_disable() -> None:
    rows = [row("de-xuat-phe-duyet"), row("crm", deps=["de-xuat-phe-duyet"])]
    p = plan(rows, "de-xuat-phe-duyet", "disable")
    assert any("crm" in x for x in p.problems)
    rows[1]["status"] = "disabled"  # gói phụ thuộc đã tắt thì tắt được
    assert plan(rows, "de-xuat-phe-duyet", "disable").problems == []


def test_dependency_guard_on_enable() -> None:
    rows = [row("de-xuat-phe-duyet", "disabled"), row("crm", "disabled", ["de-xuat-phe-duyet"])]
    assert any("de-xuat-phe-duyet" in x for x in plan(rows, "crm", "enable").problems)


@pytest.mark.parametrize(
    ("rows", "pack", "action", "needle"),
    [
        ([], "crm", "disable", "chưa cài"),
        ([row("crm", "disabled")], "crm", "disable", "đã ở trạng thái"),
        ([row("crm", models=None)], "crm", "disable", "cài lại"),  # cài trước migration 0018
        ([row("crm"), {**row("old"), "depends_on": None}], "crm", "disable", "old"),
    ],
)
def test_plan_problems_fail_closed(
    rows: list[dict[str, Any]], pack: str, action: Any, needle: str
) -> None:
    p = plan(rows, pack, action)
    assert any(needle in x for x in p.problems), p.problems
    assert "KHÔNG" in p.render_vi()


def test_digest_binds_tenant_action_and_state() -> None:
    base = plan([row("crm"), row("hr")], "crm", "disable").digest
    other_t = lc.plan_lifecycle(
        [row("crm"), row("hr")], tenant=TenantRef(2, uuid4()), alias="b", pack="crm",
        action="disable",
    ).digest  # fmt: skip
    changed = plan([row("crm"), row("hr", "disabled")], "crm", "disable").digest
    assert len({base, other_t, changed}) == 3


# ------------------------------------------------------------------------- apply ----
class FakeConn:
    def __init__(self) -> None:
        self.sql: list[tuple[str, Any]] = []

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield

    def execute(self, q: str, params: Any = None) -> Any:
        self.sql.append((q, params))
        return self

    def __enter__(self) -> FakeConn:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"rows": [row("crm")], "conn": FakeConn(), "saved": []}
    monkeypatch.setattr(lc, "connect", lambda dsn: state["conn"])
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: state["rows"])
    monkeypatch.setattr(lc, "list_app_incidents", lambda *args: [])

    def save(conn: Any, tenant: TenantRef, **kw: Any) -> None:
        state["saved"].append((tenant, kw))
        for r in state["rows"]:
            if r["pack"] == kw["pack"]:
                r["status"] = kw["status"]

    monkeypatch.setattr(lc, "set_pack_status", save)
    return state


def apply(action: lc.Action, confirm: str, by: str = "user:mai") -> lc.LifecyclePlan:
    return lc.apply_lifecycle(
        "dsn", tenant=T, alias="acme", pack="crm", action=action, confirm=confirm,
        proposed_by="agent:a", approved_by=by,
    )  # fmt: skip


def test_apply_requires_separate_approver_and_matching_digest(db: dict[str, Any]) -> None:
    d = plan(db["rows"], "crm", "disable").digest
    with pytest.raises(AdminError, match="tách nhiệm vụ"):
        apply("disable", d, by="agent:a")
    with pytest.raises(AdminError, match="mã xác nhận"):
        apply("disable", "sha256:" + "0" * 64)
    assert db["saved"] == []


def test_apply_disable_then_enable_records_status_change(db: dict[str, Any]) -> None:
    apply("disable", plan(db["rows"], "crm", "disable").digest)
    apply("enable", plan(db["rows"], "crm", "enable").digest)
    statuses = [kw["status"] for _, kw in db["saved"]]
    assert statuses == ["disabled", "enabled"]
    for tenant, kw in db["saved"]:
        assert tenant == T
        assert (kw["proposed_by"], kw["approved_by"]) == ("agent:a", "user:mai")
        assert kw["digest"].startswith("sha256:")
    lock = db["conn"].sql[0]
    assert "pg_advisory_xact_lock" in lock[0] and lock[1] == (f"xt:pack:{T.id}",)


def test_apply_rejects_when_plan_has_problems(db: dict[str, Any]) -> None:
    db["rows"].append(row("hr", deps=["crm"]))
    d = plan(db["rows"], "crm", "disable").digest
    with pytest.raises(AdminError, match="hr"):
        apply("disable", d)


# --------------------------------------------------------------------------- CLI ----
def test_cli_plan_then_confirm(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    rows = [row("crm")]
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(admin, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(admin, "tenant_of_alias", lambda conn, alias: T)
    monkeypatch.setattr(lc, "connect", lambda dsn: FakeConn())
    monkeypatch.setattr(lc, "pack_rows", lambda conn, tenant: rows)
    monkeypatch.setattr(lc, "set_pack_status", lambda conn, tenant, **kw: calls.append(kw))
    env = {"XT_DB_MIGRATOR_URL": "postgresql://x"}
    argv = ["pack", "disable", "--tenant", "acme", "crm", "--proposed-by", "agent:a",
            "--approved-by", "user:mai"]  # fmt: skip
    assert admin.run(argv, env) == 0
    out = capsys.readouterr().out
    digest = next(w for w in out.split() if w.startswith("sha256:"))
    assert calls == [] and "CHƯA áp" in out
    assert admin.run([*argv, "--confirm", digest], env) == 0
    assert [c["status"] for c in calls] == ["disabled"]
    assert "đã tắt crm" in capsys.readouterr().out
