# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""PEP: model của ứng dụng đang tắt ở tenant của người gọi trả `app_disabled` TRƯỚC `implicit_deny`
của IAM; model khác, tenant khác, lỗi tra cứu và admin đọc giữ nguyên quyết định IAM."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from xt.application.model_engine.ports import DisabledApps
from xt.application.tools.ports import APP_DISABLED, AccessCheck
from xt.entrypoints.composition.runtime import iam_person
from xt.entrypoints.composition.security import ToolAccessGuard

POLICY = {
    "version": "1",
    "tenant": "acme",
    "statements": [
        {
            "id": "read",
            "effect": "Allow",
            "subjects": ["role:employee", "role:admin"],
            "actions": ["record.read"],
            "resources": ["xrn:acme:*"],
        }
    ],
}


def guard(roles: tuple[str, ...], disabled: Any) -> tuple[ToolAccessGuard, Any]:
    person: Any = SimpleNamespace(
        id=uuid4(), roles=roles, active=True, unit_key=None, limit_amount=None
    )
    caller = SimpleNamespace(
        actor=SimpleNamespace(roles=roles),
        tenant=SimpleNamespace(id=uuid4()),
        person=person,
        alias="acme",
        iam=lambda: iam_person("acme", person),
    )
    store = SimpleNamespace(active_policy=lambda actor: (1, POLICY), person=lambda a, i: person)
    rt: Any = SimpleNamespace(
        store=store,
        caller=lambda ctx: caller,
        catalogs=SimpleNamespace(disabled=disabled),
        records=SimpleNamespace(get=lambda *a: (_ for _ in ()).throw(KeyError())),
    )
    ctx = SimpleNamespace(tenant="acme", now=datetime.now(UTC), confirmed_by=(), trace_id="t")
    return ToolAccessGuard(rt), ctx


def off(_actor: Any) -> DisabledApps:
    return DisabledApps(models={"stock_item": "kho-hang"}, packs=frozenset({"kho-hang"}))


def create(model: str) -> AccessCheck:
    return AccessCheck("create_record", "record.create", None, {"model_id": model, "values": {}})


def test_disabled_app_model_denies_with_app_disabled_before_iam() -> None:
    g, ctx = guard(("employee",), off)  # employee không có record.create -> IAM implicit_deny
    d = g.check(ctx, create("stock_item"))
    assert not d.allowed and d.reason == APP_DISABLED
    assert d.details == {"pack": "kho-hang", "model": "stock_item"}
    assert "kho-hang" in d.message


def test_other_models_keep_iam_denial_without_leaking() -> None:
    g, ctx = guard(("employee",), off)
    d = g.check(ctx, create("other_model"))
    assert not d.allowed and d.reason == "implicit_deny" and d.explain_ref


def test_no_disabled_apps_keeps_iam_denial() -> None:
    g, ctx = guard(("employee",), lambda _a: DisabledApps(models={}, packs=frozenset()))
    assert g.check(ctx, create("stock_item")).reason == "implicit_deny"


def test_lookup_failure_is_fail_closed_iam_denial() -> None:
    def boom(_a: Any) -> DisabledApps:
        raise RuntimeError("db down")

    g, ctx = guard(("employee",), boom)
    d = g.check(ctx, create("stock_item"))
    assert not d.allowed and d.reason == "implicit_deny"


def test_iam_allowed_is_untouched() -> None:
    g, ctx = guard(("employee",), off)
    assert g.check(ctx, AccessCheck("get_record", "record.read", f"stock_item:{uuid4()}")).allowed


def test_admin_read_keeps_iam_decision_but_admin_write_is_app_disabled() -> None:
    g, ctx = guard(("admin",), off)
    ref = f"stock_item:{uuid4()}"
    assert g.check(ctx, AccessCheck("get_record", "record.read", ref)).allowed
    d = g.check(ctx, AccessCheck("update_record", "record.update", ref))
    assert not d.allowed and d.reason == APP_DISABLED
