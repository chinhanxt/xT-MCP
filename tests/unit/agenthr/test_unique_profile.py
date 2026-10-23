# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Một (client_id, người phụ trách) chỉ có một hồ sơ chưa `da_nghi` (D-AGENTHR-1): từ chối tạo
trùng, cổng chặn kín khi dữ liệu cũ đã trùng, nghỉ một hồ sơ trùng không xóa agent_grant."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from xt.application.agenthr.gate import DUPLICATE_PROFILE, Profile, duplicate_block
from xt.application.tools.errors import ToolError
from xt.entrypoints.composition.agenthr import AgentHr

PERSON = uuid4()
CLIENT = "chatgpt"


class Records:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def search(self, _ctx: object, _model: str, where: dict[str, Any], *_a: object) -> Any:
        want = {c["field"]: c["value"] for c in where["and"]}
        return {"items": [r for r in self.rows if all(r.get(k) == v for k, v in want.items())]}


class Store:
    def __init__(self) -> None:
        self.deleted: list[tuple[UUID, str]] = []
        self.locked: list[tuple[str, UUID]] = []

    def delete_agent_grant(self, _actor: object, person: UUID, client: str) -> bool:
        self.deleted.append((person, client))
        return True

    def lock_agent_pair(self, _actor: object, client: str, person: UUID) -> None:
        self.locked.append((client, person))


def row(state: str, **kw: Any) -> dict[str, Any]:
    return {"id": str(uuid4()), "client_id": CLIENT, "nguoi_phu_trach": str(PERSON),
            "trang_thai": state, **kw}  # fmt: skip


def make(rows: list[dict[str, Any]]) -> tuple[AgentHr, Store]:
    store = Store()
    models = [SimpleNamespace(name=n) for n in ("ho_so_agent", "jd_agent")]
    rt = SimpleNamespace(
        records=Records(rows),
        store=store,
        catalogs=SimpleNamespace(
            current=lambda _a: (1, SimpleNamespace(models=models)),
            disabled=lambda _a: SimpleNamespace(models=set()),
        ),
    )
    return AgentHr(rt), store  # type: ignore[arg-type]


def caller() -> Any:
    return SimpleNamespace(
        is_agent=True, client_id=CLIENT, person=SimpleNamespace(id=PERSON), actor="a",
        workflow=lambda: "wf",
    )  # fmt: skip


def new_values() -> dict[str, Any]:
    return {"client_id": CLIENT, "nguoi_phu_trach": f"person:{PERSON}", "ten": "x"}


def test_duplicate_block_needs_two_live_profiles() -> None:
    assert duplicate_block([Profile("a", "dang_lam")]) is None
    b = duplicate_block([Profile("a", "dang_lam"), Profile("b", "tam_dung")])
    assert b is not None and b.reason == DUPLICATE_PROFILE
    assert b.details["profiles"] == "a,b" and b.details["count"] == 2


@pytest.mark.parametrize("state", ["cho_duyet", "dang_lam", "tam_dung"])
def test_create_rejected_while_another_profile_is_not_terminated(state: str) -> None:
    hr, _ = make([row(state)])
    with pytest.raises(ToolError) as e:
        hr.before_write(caller(), "ho_so_agent", None, new_values())
    assert e.value.details["reason"] == DUPLICATE_PROFILE
    assert e.value.remediation and "da_nghi" in e.value.remediation


def test_create_allowed_after_termination_or_for_another_pair() -> None:
    hr, _ = make([row("da_nghi"), row("cho_duyet", client_id="claude")])
    out = hr.before_write(caller(), "ho_so_agent", None, new_values())
    assert out["ten"] == "x"


def test_after_write_rechecks_under_pair_lock() -> None:
    mine = row("cho_duyet")
    hr, store = make([mine])
    hr.after_write(caller(), "ho_so_agent", mine, created=True)
    assert store.locked == [(CLIENT, PERSON)]
    hr, store = make([mine, row("dang_lam")])
    with pytest.raises(ToolError) as e:
        hr.after_write(caller(), "ho_so_agent", mine, created=True)
    assert e.value.details["reason"] == DUPLICATE_PROFILE


def test_resolve_fails_closed_on_legacy_duplicates() -> None:
    suspended, working = row("tam_dung"), row("dang_lam")
    hr, _ = make([suspended, working])
    _p, _row, dup = hr.profile(caller()) or (None, None, None)
    assert dup is not None and dup.reason == DUPLICATE_PROFILE
    assert {suspended["id"], working["id"]} == set(dup.details["profiles"].split(","))


def test_resolve_ignores_terminated_profiles() -> None:
    old, live = row("da_nghi"), row("dang_lam")
    hr, _ = make([old, live])  # da_nghi đứng trước: vẫn chọn hồ sơ chưa nghỉ
    found = hr.profile(caller())
    assert found is not None and found[0].id == live["id"] and found[2] is None
    hr, _ = make([old])
    found = hr.profile(caller())
    assert found is not None and found[0].status == "da_nghi" and found[2] is None


def test_terminating_one_of_two_legacy_duplicates_keeps_the_grant() -> None:
    gone, other = row("da_nghi"), row("tam_dung")
    hr, store = make([gone, other])
    assert hr.release_grant(caller(), gone) is False and store.deleted == []
    hr, store = make([gone, row("da_nghi")])
    assert hr.release_grant(caller(), gone) is True and store.deleted == [(PERSON, CLIENT)]
