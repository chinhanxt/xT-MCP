# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`mapping.json` của seed: từ vựng tệp HR -> từ vựng gói; vai trò/phòng lạ phải lỗi rõ."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xt.adapters.db.integration import AdminError
from xt.entrypoints.composition.org_seed import org_from_hr

ROOT = Path(__file__).resolve().parents[3]


def _hr() -> dict:
    return {
        "departments": [
            {"id": "D01", "name": "BGD", "parent_id": None},
            {"id": "D02", "name": "Kế toán", "parent_id": "D01"},
        ],
        "positions": [],
        "employees": [
            {"id": "A", "full_name": "A", "department_id": "D01", "roles": ["director"]},
            {"id": "B", "full_name": "B", "department_id": "D02", "roles": ["dept_head"]},
            {"id": "C", "full_name": "C", "department_id": "D02", "roles": ["it"]},
        ],
    }


MAP = {
    "departments": {"D01": "BGD", "D02": "KT"},
    "roles": {"director": ["director"], "dept_head": ["dept_head"], "it": ["it_admin"],
              "dept_head@D02": ["chief_accountant"]},
}  # fmt: skip


def test_mapping_translates_roles_and_units() -> None:
    out = org_from_hr(_hr(), mapping=MAP)
    people = {p["key"]: p for p in out["people"]}
    assert people["C"]["roles"] == ["it_admin"] and people["C"]["unit"] == "KT"
    assert people["B"]["roles"] == ["dept_head", "chief_accountant"]
    units = {u["key"]: u for u in out["units"]}
    assert units["KT"]["head"] == "B" and units["BGD"]["head"] is None


def test_unknown_role_and_department_fail_clearly() -> None:
    bad = {"departments": {"D01": "BGD"}, "roles": {"director": ["director"]}}
    with pytest.raises(AdminError) as e:
        org_from_hr(_hr(), mapping=bad)
    msg = str(e.value)
    assert "phòng 'D02'" in msg and "vai trò 'it'" in msg and "vai trò 'dept_head'" in msg


def test_malformed_mapping_rejected() -> None:
    m = {**MAP, "roles": {**MAP["roles"], "it": "it_admin", "x@D99": ["y"]}}
    with pytest.raises(AdminError) as e:
        org_from_hr(_hr(), mapping=m)
    assert "danh sách" in str(e.value) and "D99" in str(e.value)


@pytest.mark.parametrize("seed", ["binh-minh", "hoa-mai"])
def test_seed_mappings_cover_seed_data(seed: str) -> None:
    hr = json.loads((ROOT / "seed" / seed / "org.json").read_text(encoding="utf-8"))
    mp = json.loads((ROOT / "seed" / seed / "mapping.json").read_text(encoding="utf-8"))
    out = org_from_hr(hr, mapping=mp, base_roles=["employee"])
    pairs = zip(out["units"], hr["departments"], strict=True)
    assert all(u["key"] == mp["departments"][d["id"]] for u, d in pairs)
    assert not {"it", "accountant"} & {r for p in out["people"] for r in p["roles"]}
