# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Manifest ứng dụng Tầng 3 (khối `app` của pack.json, D-UNI-T3-1 / G8): kiểm chặt, kho-hang là
ứng dụng nhóm hang-hoa; digest cài của gói không có `app` không đổi."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from xt.adapters.db.integration import AdminError
from xt.entrypoints.composition.packs import PackManifest, load_pack

ROOT = Path(__file__).resolve().parents[3]
PACKS = sorted(p.parent for p in (ROOT / "packages").glob("*/pack.json"))

BASE: dict[str, Any] = {
    "format": "xt-pack/1",
    "pack": "crm",
    "version": "0.1.0",
    "title": "CRM nhẹ",
    "depends_on": ["de-xuat-phe-duyet"],
    "models": ["customer"],
}
APP: dict[str, Any] = {
    "id": "crm",
    "title": "Khách hàng",
    "group": "trai-nghiem-thue-bao",
    "depends_on": ["de-xuat-phe-duyet"],
    "ui": [{"kind": "generic"}, {"kind": "module", "path": "/apps/crm/"}],
    "tools": ["search_records", "get_record", "create_record"],
}


def manifest(**app: Any) -> PackManifest:
    raw = copy.deepcopy(BASE)
    raw["app"] = {**copy.deepcopy(APP), **app}
    return PackManifest.model_validate(raw)


def test_valid_app_block() -> None:
    m = manifest()
    assert m.app is not None
    assert m.app.group == "trai-nghiem-thue-bao"
    assert [u.kind for u in m.app.ui] == ["generic", "module"]
    assert m.app.ui[1].path == "/apps/crm/"


def test_app_is_optional() -> None:
    assert PackManifest.model_validate(BASE).app is None


@pytest.mark.parametrize(
    "bad",
    [
        {"group": "ban-le"},  # ngoài 4 nhóm
        {"id": "khac"},  # id phải trùng tên gói
        {"depends_on": ["kho-hang"]},  # phụ thuộc ứng dụng phải nằm trong depends_on của gói
        {"tools": ["drop_database"]},  # tool không có trong catalog
        {"tools": ["get_record", "get_record"]},  # trùng
        {"tools": []},
        {"ui": []},
        {"ui": [{"kind": "generic", "path": "/x/"}]},  # generic không có path
        {"ui": [{"kind": "module"}]},  # module cần path
        {"ui": [{"kind": "module", "path": "https://evil.example/"}]},
        {"ui": [{"kind": "module", "path": "/apps/../admin/"}]},
        {"ui": [{"kind": "module", "path": "/apps/crm"}]},  # phải kết thúc bằng /
        {"ui": [{"kind": "generic"}, {"kind": "generic"}]},  # trùng loại
        {"ui": [{"kind": "iframe", "path": "/x/"}]},
        {"title": ""},
        {"extra": 1},  # khóa lạ
    ],
)
def test_invalid_app_block_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        manifest(**bad)


def test_load_pack_reports_bad_app_block(tmp_path: Path) -> None:
    raw = {**BASE, "models": [], "app": {**APP, "group": "x"}}
    (tmp_path / "pack.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(AdminError, match="manifest không hợp lệ"):
        load_pack(tmp_path)


@pytest.mark.parametrize("pack", PACKS, ids=lambda p: p.name)
def test_every_shipped_pack_manifest_validates(pack: Path) -> None:
    load_pack(pack)


def test_kho_hang_is_hang_hoa_app_with_waretrack_module() -> None:
    m = load_pack(ROOT / "packages" / "kho-hang").manifest
    assert m.app is not None
    assert m.app.id == "kho-hang"
    assert m.app.group == "hang-hoa"
    ui = {u.kind: u.path for u in m.app.ui}
    assert ui == {"generic": None, "module": "/waretrack/"}
    assert {"search_records", "create_record", "submit_request"} <= set(m.app.tools)


def test_manifest_dump_omits_absent_app() -> None:
    """Digest cài (`InstallPlan.digest`) băm bản dump manifest: gói không có `app` giữ digest cũ."""
    dumped = PackManifest.model_validate(BASE).install_view()
    assert "app" not in dumped
    assert manifest().install_view()["app"]["group"] == "trai-nghiem-thue-bao"
