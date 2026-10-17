# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`record.aggregate` trong policy thật của từng gói (D-UNI-AGG-1): ai đã đọc/tìm được model thì
tổng hợp được đúng model đó; vai trò không có quyền đọc thì không; không rộng hơn quyền hiện có."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.iam.support import ROOT
from xt.domain.iam import AccessRequest, Actor, PolicyDocument, PolicyEvaluator

T = "binhminh"
PACKS = ROOT / "packages"
# Gói có statement `record.search` trên `*` (mẫu `wh.tools` của kho-hang): aggregate theo search.
BY_SEARCH = ("cham-cong", "crm", "tai-san", "de-xuat-phe-duyet")
# Gói chưa có `record.search`: aggregate đi theo `record.read` (trừ model PII).
BY_READ = ("giao-viec-sla", "nhan-su-vong-doi")
PII_MODELS = {"employee_pii"}


def _load(pack: str) -> tuple[PolicyEvaluator, list[str], list[str]]:
    doc = json.loads((PACKS / pack / "policies" / "iam.json").read_text())
    models = json.loads((PACKS / pack / "pack.json").read_text())["models"]
    roles = sorted(
        {
            s.removeprefix("role:")
            for st in doc["statements"]
            if st["effect"] == "Allow"
            for s in st["subjects"]
            if s.startswith("role:")
        }
    )
    return PolicyEvaluator(PolicyDocument.model_validate(doc)), models, roles


def _allowed(ev: PolicyEvaluator, role: str, action: str, model: str) -> bool:
    kind = "agent" if role == "agent" else "human"
    who = Actor(
        id=f"{role}-1",
        tenant=T,
        kind=kind,
        roles=(role,),
        department="KD",
        autonomy=1,
        behalf=None,
    )
    # Tài nguyên tổng hợp như `ToolAccessGuard._resource`: chủ sở hữu là chính người gọi.
    res: dict[str, Any] = {"owner": who.id, "creator": who.id, "department": "KD"}
    return ev.evaluate(AccessRequest(who, action, f"xrn:{T}:{model}:{model}", res, now=0)).allowed


@pytest.mark.parametrize("pack", BY_SEARCH + BY_READ)
def test_aggregate_mirrors_existing_access(pack: str) -> None:
    ev, models, roles = _load(pack)
    granted = 0
    for role in roles:
        for model in models:
            agg = _allowed(ev, role, "record.aggregate", model)
            if pack in BY_SEARCH:
                assert agg == _allowed(ev, role, "record.search", model), (role, model)
            else:
                read = _allowed(ev, role, "record.read", model) and model not in PII_MODELS
                assert agg == read, (role, model)
            granted += agg
    assert granted, pack


@pytest.mark.parametrize("pack", BY_SEARCH + BY_READ)
def test_role_without_read_cannot_aggregate(pack: str) -> None:
    ev, models, roles = _load(pack)
    for role in (*roles, "visitor", "warehouse_manager"):
        for model in models:
            if _allowed(ev, role, "record.read", model) or _allowed(
                ev, role, "record.search", model
            ):
                continue
            assert not _allowed(ev, role, "record.aggregate", model), (role, model)
    for model in models:
        assert not _allowed(ev, "visitor", "record.aggregate", model)


@pytest.mark.parametrize("pack", BY_SEARCH + BY_READ)
def test_every_reading_role_can_aggregate_something(pack: str) -> None:
    ev, models, roles = _load(pack)
    readers = [r for r in roles if any(_allowed(ev, r, "record.read", m) for m in models)]
    assert readers
    for role in readers:
        if pack == "nhan-su-vong-doi" and role == "hr_pii":
            continue  # chỉ đọc `employee_pii`: không tổng hợp dữ liệu cá nhân
        if pack == "de-xuat-phe-duyet" and role == "admin":
            continue  # admin chỉ đọc danh mục `proposal_type`, không có `record.search` như hôm nay
        assert any(_allowed(ev, role, "record.aggregate", m) for m in models), role


def test_pii_model_is_never_aggregated() -> None:
    ev, models, roles = _load("nhan-su-vong-doi")
    for role in roles:
        for model in PII_MODELS & set(models):
            assert not _allowed(ev, role, "record.aggregate", model)
    # model thường vẫn tổng hợp được: HR toàn quyền, nhân viên theo điều kiện chủ sở hữu.
    assert _allowed(ev, "hr", "record.aggregate", "lifecycle_case")
    assert _allowed(ev, "employee", "record.aggregate", "lifecycle_case")


def test_agent_denies_and_conditions_are_kept() -> None:
    ev, _, _ = _load("giao-viec-sla")
    doc = json.loads((PACKS / "giao-viec-sla" / "policies" / "iam.json").read_text())
    agg = [s for s in doc["statements"] if "record.aggregate" in s["actions"]]
    reads = {s["id"]: s for s in doc["statements"] if "record.read" in s["actions"]}
    assert agg
    for s in agg:
        src = reads[s["id"].removesuffix("-agg")]
        assert s["effect"] == "Allow"
        assert s["subjects"] == src["subjects"]
        assert s["conditions"] == src["conditions"]
        assert set(s["resources"]) <= set(src["resources"])
    # Deny hiện có (agent không duyệt/xóa) không đổi và không đụng record.aggregate.
    assert not any(
        s["effect"] == "Deny" and "record.aggregate" in s["actions"] for s in doc["statements"]
    )
    assert _allowed(ev, "agent", "record.aggregate", "task")
