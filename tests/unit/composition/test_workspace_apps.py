# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""inspect_workspace công bố ứng dụng Tầng 3 đã cài (nhóm, trạng thái, giao diện, tool) để cổng
và ChatGPT dựng launcher; không thêm tool mới (G8 / U15)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from xt.application.model_engine.ports import DisabledApps
from xt.application.tools.context import ClientClass
from xt.contracts.tools import BY_NAME
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.spec import Catalog
from xt.entrypoints.composition.workspace import Workspace

TENANT = TenantRef(1, UUID(int=1))
KHO_APP = {
    "id": "kho-hang",
    "title": "Kho hàng (WMS)",
    "group": "hang-hoa",
    "depends_on": [],
    "ui": [{"kind": "module", "path": "/waretrack/"}, {"kind": "generic", "path": None}],
    "tools": ["search_records", "create_record"],
}


def _model(name: str) -> dict[str, Any]:
    return {
        "id": f"m_{name}",
        "name": name,
        "label": name,
        "fields": [{"id": f"f_{name}", "name": "title", "label": "Tiêu đề", "type": "text"}],
        "permissions": {"admin": {"actions": ["read"]}, "staff": {"actions": ["read"]}},
    }


def _rt(roles: tuple[str, ...], packs: list[dict[str, Any]]) -> Any:
    off = [p for p in packs if p["status"] == "disabled"]
    disabled = DisabledApps(
        models={m: p["pack"] for p in off for m in p["models"]},
        packs=frozenset(p["pack"] for p in off),
    )
    actor = ModelActor(TENANT, uuid4(), roles)
    caller = SimpleNamespace(
        person=SimpleNamespace(id=uuid4(), name="Lan"),
        is_agent=False,
        autonomy=3,
        client_id="desk",
        profile=SimpleNamespace(alias="acme", timezone="Asia/Ho_Chi_Minh", llm_egress="masked"),
        actor=actor,
    )
    cat = Catalog.model_validate({"version": 1, "models": [_model("stock_item"), _model("lead")]})
    return SimpleNamespace(
        caller=lambda ctx: caller,
        store=SimpleNamespace(
            bindings=lambda a: {},
            packs=lambda a: packs,
            definitions=lambda a: [],
            active_policy=lambda a: None,
        ),
        catalogs=SimpleNamespace(current=lambda a: (1, cat), disabled=lambda a: disabled),
    )


def _row(pack: str, status: str, app: dict[str, Any] | None, models: list[str]) -> dict[str, Any]:
    return {
        "pack": pack,
        "version": "0.2.0",
        "digest": "sha256:x",
        "installed_at": None,
        "status": status,
        "app": app,
        "models": models,
        "depends_on": [],
    }


def _inspect(roles: tuple[str, ...], packs: list[dict[str, Any]], include: list[str]) -> Any:
    settings = SimpleNamespace(
        confirmation_ttl=timedelta(minutes=10), max_aggregate_groups=10**6, available=lambda n: True
    )
    ws = Workspace(_rt(roles, packs), settings)  # type: ignore[arg-type]
    return ws.inspect(SimpleNamespace(client_class=ClientClass.HUMAN), {"include": include})  # type: ignore[arg-type]


def test_apps_section_lists_installed_apps_with_group_and_status() -> None:
    packs = [
        _row("kho-hang", "enabled", KHO_APP, ["stock_item"]),
        _row("de-xuat-phe-duyet", "enabled", None, ["proposal"]),  # gói không phải ứng dụng
    ]
    out = _inspect(("staff",), packs, ["apps"])
    assert out["apps"] == [
        {
            "id": "kho-hang",
            "pack": "kho-hang",
            "title": "Kho hàng (WMS)",
            "group": "hang-hoa",
            "status": "enabled",
            "version": "0.2.0",
            "ui": [{"kind": "module", "path": "/waretrack/"}, {"kind": "generic"}],
            "tools": ["search_records", "create_record"],
            "models": ["stock_item"],
        }
    ]
    schema = BY_NAME["inspect_workspace"].output_schema
    Draft202012Validator(schema).validate(out)


def test_apps_in_default_sections_and_include_enum() -> None:
    enum = BY_NAME["inspect_workspace"].input_schema["properties"]["include"]["items"]["enum"]
    assert "apps" in enum
    out = _inspect(("staff",), [_row("kho-hang", "enabled", KHO_APP, ["stock_item"])], [])
    assert [a["id"] for a in out["apps"]] == ["kho-hang"]


def test_disabled_app_hidden_from_models_and_launcher_except_admin() -> None:
    packs = [
        _row("kho-hang", "disabled", KHO_APP, ["stock_item"]),
        _row("crm", "enabled", {**KHO_APP, "id": "crm", "group": "trai-nghiem-thue-bao"}, ["lead"]),
    ]
    staff = _inspect(("staff",), packs, ["models", "apps"])
    assert [m["id"] for m in staff["models"]] == ["lead"]  # stock_item ẩn
    assert [a["id"] for a in staff["apps"]] == ["crm"]
    admin = _inspect(("admin",), packs, ["models", "apps"])
    assert [m["id"] for m in admin["models"]] == ["lead"]
    assert {a["id"]: a["status"] for a in admin["apps"]} == {
        "kho-hang": "disabled",
        "crm": "enabled",
    }


def test_me_includes_caller_effective_roles() -> None:
    out = _inspect(("staff", "admin", "staff"), [], ["me"])
    assert out["me"]["roles"] == ["admin", "staff"]
    Draft202012Validator(BY_NAME["inspect_workspace"].output_schema).validate(out)


@pytest.mark.parametrize("role", ["admin", "it", "director", "administrator", "tenant_admin"])
def test_disabled_app_launcher_exception_requires_literal_admin_role(role: str) -> None:
    packs = [_row("kho-hang", "disabled", KHO_APP, ["stock_item"])]
    out = _inspect((role,), packs, ["models", "apps"])
    assert "stock_item" not in {model["id"] for model in out["models"]}
    assert [(app["id"], app["status"]) for app in out["apps"]] == (
        [("kho-hang", "disabled")] if role == "admin" else []
    )


def test_limits_report_group_cap_bounded_by_hard_cap() -> None:
    out = _inspect(("staff",), [], ["limits"])
    assert out["limits"]["max_aggregate_groups"] == 5000
