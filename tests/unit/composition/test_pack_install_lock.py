# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Install must recheck enabled dependencies after waiting for the tenant pack lock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from xt.adapters.db.integration import AdminError
from xt.domain.model.context import TenantRef
from xt.domain.model.spec import Catalog
from xt.entrypoints import admin
from xt.entrypoints.composition import packs

ROOT = Path(__file__).resolve().parents[3]
DEPENDENCY = "de-xuat-phe-duyet"


class InstallBoundary:
    def __init__(self, *, disable_on_lock: bool) -> None:
        self.tenant = TenantRef(1, uuid4())
        self.enabled = {DEPENDENCY}
        self.disable_on_lock = disable_on_lock
        self.locked = False
        self.in_transaction = False
        self.events: list[tuple[str, bool]] = []
        self.lock_params: Any = None

    def __enter__(self) -> InstallBoundary:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.in_transaction = True
        try:
            yield
        finally:
            self.in_transaction = False
            self.locked = False

    def execute(self, query: str, params: Any = None) -> Any:
        if "pg_advisory_xact_lock" in query:
            assert self.in_transaction
            self.locked = True
            self.lock_params = params
            self.events.append(("lock", self.locked))
            if self.disable_on_lock:
                # Another administrator disabled it after admin.run's initial read,
                # while install was waiting for the shared tenant pack lock.
                self.enabled.clear()
            return None
        assert "SELECT pack FROM xt_meta.pack_install" in query
        assert "status = 'enabled'" in query
        assert params == (self.tenant.id,)
        self.events.append(("dependencies", self.locked))
        return SimpleNamespace(fetchall=lambda: [(p,) for p in self.enabled])

    def record_write(self, event: str) -> None:
        self.events.append((event, self.locked))


def _install(monkeypatch: pytest.MonkeyPatch, boundary: InstallBoundary) -> packs.InstallPlan:
    loaded = packs.load_pack(ROOT / "packages" / "kho-hang")
    loaded = replace(
        loaded, manifest=loaded.manifest.model_copy(update={"depends_on": [DEPENDENCY]})
    )
    plan = packs.InstallPlan(
        pack=loaded,
        tenant=boundary.tenant,
        alias="acme",
        catalog=Catalog.model_validate({"version": 1, "models": loaded.models}),
        model_plan_hash="model-plan",
        model_plan_text="create models",
        destructive=[],
        blocked=[],
        processes=[],
        policy=None,
        bindings=[],
    )
    monkeypatch.setattr(admin, "connect", lambda dsn: boundary)
    monkeypatch.setattr(admin, "tenant_of_alias", lambda conn, alias: boundary.tenant)
    monkeypatch.setattr(packs, "connect", lambda dsn: boundary)
    monkeypatch.setattr(packs, "load_pack", lambda path: loaded)
    monkeypatch.setattr(packs, "plan_install", lambda *args, **kwargs: plan)
    migrator = SimpleNamespace(
        current=lambda tenant: None,
        apply=lambda *args, **kwargs: boundary.record_write("ddl"),
        run_jobs=lambda seq: SimpleNamespace(failed=0, dead=0),
    )
    monkeypatch.setattr(packs, "PgMigrator", lambda *args, **kwargs: migrator)
    monkeypatch.setattr(packs, "save_pack", lambda *args, **kwargs: boundary.record_write("save"))
    assert (
        admin.run(
            [
                "pack",
                "install",
                "--tenant",
                "acme",
                "packages/kho-hang",
                "--proposed-by",
                "agent:a",
                "--approved-by",
                "user:mai",
                "--confirm",
                plan.digest,
            ],
            {"XT_DB_MIGRATOR_URL": "postgresql://unused"},
        )
        == 0
    )
    return plan


def test_install_rejects_dependency_disabled_while_waiting_for_pack_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = InstallBoundary(disable_on_lock=True)
    with pytest.raises(AdminError, match=DEPENDENCY):
        _install(monkeypatch, boundary)
    assert boundary.events == [("dependencies", False), ("lock", True), ("dependencies", True)]
    assert boundary.lock_params == (f"xt:pack:{boundary.tenant.id}",)
    assert not boundary.locked and not boundary.in_transaction


def test_install_holds_pack_lock_from_dependency_recheck_through_all_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = InstallBoundary(disable_on_lock=False)
    _install(monkeypatch, boundary)
    assert boundary.events == [
        ("dependencies", False),
        ("lock", True),
        ("dependencies", True),
        ("ddl", True),
        ("save", True),
    ]
    assert boundary.lock_params == (f"xt:pack:{boundary.tenant.id}",)
    assert not boundary.locked and not boundary.in_transaction
