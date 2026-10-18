# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-UNI-AGENT-1 mở rộng: agent ghi record thường (không phải phiếu) thay người có quyền.

Agent chỉ có quyền của người được đại diện: người không có vai trò ghi vẫn bị chặn qua agent.
"""

from __future__ import annotations

import pytest

from tests.unit.iam.test_agent_on_behalf import _agent, _guard, _person, _req
from xt.domain.iam import Actor, Reason

# gói -> (model record, vai trò được ghi, vai trò không được ghi)
CASES = [
    ("crm", "khach_hang", "sales", "employee"),
    ("crm", "co_hoi", "sales_manager", "warehouse_manager"),
    ("crm", "cham_soc", "sales", "technician"),
    ("cham-cong", "du_an", "project_manager", "employee"),
    ("cham-cong", "cham_cong", "employee", "sales"),
    ("kho-hang", "warehouse", "warehouse_manager", "employee"),
    ("kho-hang", "warehouse_location", "warehouse_manager", "employee"),
    ("kho-hang", "stock_item", "admin", "sales"),
]


def _people(writer: str, other: str) -> dict[str, Actor]:
    return {"w": _person("w", writer), "o": _person("o", other)}


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("action", ["record.create", "record.update"])
@pytest.mark.parametrize(("pack", "model", "writer", "other"), CASES)
def test_agent_writes_record_only_for_authorized_person(
    pack: str, model: str, writer: str, other: str, action: str, merged: bool
) -> None:
    guard = _guard(None if merged else pack, _people(writer, other))
    ok = guard.evaluate(_req(_agent("w"), action, model, owner="w"))
    assert ok.allowed, ok
    no = guard.evaluate(_req(_agent("o"), action, model, owner="o"))
    assert not no.allowed
    assert no.reason is Reason.BEHALF_PERMISSION


@pytest.mark.parametrize(("pack", "model", "writer", "other"), CASES)
def test_agent_never_deletes_records(pack: str, model: str, writer: str, other: str) -> None:
    guard = _guard(pack, _people(writer, other))
    assert not guard.evaluate(_req(_agent("w"), "record.delete", model, owner="w")).allowed


def test_tai_san_agent_asset_write_stays_denied() -> None:
    guard = _guard("tai-san", {"f": _person("f", "facility_manager")})
    d = guard.evaluate(_req(_agent("f"), "record.create", "tai_san", owner="f"))
    assert not d.allowed and d.reason is Reason.EXPLICIT_DENY
