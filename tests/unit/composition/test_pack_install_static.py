# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Every DB-free check of `xt pack install` passes on the real `packages/*` folders.

Guards the install plan without PostgreSQL: a pack that would print "KHÔNG CÀI ĐƯỢC" fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xt.entrypoints.composition.packs import binding_problems, load_pack, static_problems

ROOT = Path(__file__).resolve().parents[3]
PACKS = sorted(p.parent for p in (ROOT / "packages").glob("*/pack.json"))


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_pack_installs_on_fresh_tenant_without_problems(pack: Path) -> None:
    loaded = load_pack(pack)
    deps = [load_pack(ROOT / "packages" / d) for d in loaded.manifest.depends_on]
    assert static_problems(loaded, "acme", deps) == []


def test_kho_hang_binding_passes_record_id_as_process_input() -> None:
    loaded = load_pack(ROOT / "packages" / "kho-hang")
    assert static_problems(loaded, "acme", []) == []
    binding = loaded.manifest.bindings["stock_request"]
    assert binding.inputs["request_id"].field_ == "id"


def test_unknown_binding_field_still_rejected() -> None:
    loaded = load_pack(ROOT / "packages" / "kho-hang")
    raw = loaded.manifest.model_dump(by_alias=True)
    raw["bindings"]["stock_request"]["inputs"]["request_id"] = {"field": "nope"}
    manifest = type(loaded.manifest).model_validate(raw)
    from xt.domain.model.spec import Catalog

    catalog = Catalog.model_validate({"version": 1, "models": loaded.models})
    problems, _ = binding_problems(
        manifest, {m.name: m for m in catalog.models}, {"stock_approval"}
    )
    assert problems == ["ràng buộc stock_request: input request_id lấy trường lạ 'nope'"]
