# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""kho-hang pack loads as one valid catalog and upgrades additively."""

from __future__ import annotations

import copy
from pathlib import Path

from xt.domain.model.plan import RiskClass, diff_catalogs
from xt.domain.model.spec import Catalog
from xt.entrypoints.composition.packs import load_pack

PACK = Path(__file__).resolve().parents[3] / "packages" / "kho-hang"


def catalog(models):
    return Catalog.model_validate({"version": 1, "models": models})


def test_pack_catalog_valid_with_reversal_kind():
    current = catalog(load_pack(PACK).models)
    request = current.model("stock_request").by_name()
    assert request["reverses"].ref == "stock_request" and not request["reverses"].required
    assert {o.value for o in request["kind"].options or []} == {"receive", "issue", "reversal"}


def test_ledger_rows_reference_their_warehouse():
    current = catalog(load_pack(PACK).models)
    for name in ("stock_balance", "stock_movement"):
        field = current.model(name).by_name()["warehouse"]
        # Not `required`: a required ref has no default and blocks migration of filled tables.
        assert field.ref == "warehouse" and not field.required


def test_stock_level_is_read_only_ledger_view():
    pack = load_pack(PACK)
    level = catalog(pack.models).model("stock_level").by_name()
    assert level["last_movement"].ref == "stock_movement"
    for name in ("on_hand_milli", "reorder_level_milli", "below_reorder"):
        assert level[name].managed_by == "workflow"
    policy = pack.policies[0]
    statements = {s["id"]: s for s in policy["statements"]}
    resource = f"xrn:{policy['tenant']}:stock_level:*"
    for sid in ("wh.read", "wh.deny-ledger-write", "wh.deny-delete"):
        assert resource in statements[sid]["resources"], sid


def test_upgrade_from_previous_release_is_safe():
    models = load_pack(PACK).models
    old = [copy.deepcopy(m) for m in models if m["name"] != "stock_level"]
    for m in old:
        if m["name"] == "stock_request":
            m["fields"] = [f for f in m["fields"] if f["name"] != "reverses"]
            kind = next(f for f in m["fields"] if f["name"] == "kind")
            kind["options"] = [o for o in kind["options"] if o["value"] != "reversal"]
        if m["name"] in ("stock_balance", "stock_movement"):
            m["fields"] = [f for f in m["fields"] if f["name"] != "warehouse"]
    changes = diff_catalogs(catalog(old), catalog(models))
    assert changes and all(c.cls is RiskClass.SAFE for c in changes), changes
