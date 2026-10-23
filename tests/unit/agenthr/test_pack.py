# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Gói `nhan-su-ai` 0.1.0: đúng hợp đồng dữ liệu AgentHR (tên model/trường/enum cố định), IAM thật
(chỉ người IT/quản lý ghi; `role:agent` bị Deny ghi mọi model của gói, trừ client Desk giao với
quyền người dùng), seed 6 vai trò trỏ model có thật của các gói khác."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.unit.iam.test_agent_on_behalf import _guard
from xt.application.agenthr.gate import (
    PACK_MODELS,
    JobDescription,
    parse_capabilities,
    parse_days,
    parse_window,
)
from xt.domain.iam import AccessRequest, Actor, Reason
from xt.entrypoints.composition.packs import load_pack, static_problems

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages"
PACK = PACKAGES / "nhan-su-ai"
T = "binhminh"

# Hợp đồng dữ liệu (agenthr-contract.md): model -> {trường: kiểu xT}
CONTRACT: dict[str, dict[str, str]] = {
    "vai_tro_agent": {"ma": "text", "ten": "text", "mo_ta": "text", "nang_luc": "json",
                      "han_muc_viec_ngay": "number", "han_muc_gia_tri": "money",
                      "khung_gio": "text", "ngay_lam": "text"},
    "ho_so_agent": {"ten": "text", "client_id": "text", "nguoi_phu_trach": "user",
                    "bao_cao_cho": "user", "vai_tro": "ref", "trang_thai": "enum",
                    "ly_do_tam_dung": "enum", "hieu_luc_den": "date", "so_viec_hom_nay": "number"},
    "jd_agent": {"ho_so": "ref", "nang_luc": "json", "muc_tu_chu": "number",
                 "han_muc_viec_ngay": "number", "han_muc_gia_tri": "money", "khung_gio": "text",
                 "ngay_lam": "text", "canh_bao": "json", "state": "enum"},
    "giay_phep_hanh_dong": {"ho_so": "ref", "hanh_dong": "text", "dieu_kien": "text",
                            "tu_ngay": "date"},
    "su_co_agent": {"ho_so": "ref", "loai": "enum", "chi_tiet": "text", "trang_thai": "enum",
                    "xu_ly": "enum"},
}  # fmt: skip
ENUMS = {
    ("ho_so_agent", "trang_thai"): ["cho_duyet", "dang_lam", "tam_dung", "da_nghi"],
    ("ho_so_agent", "ly_do_tam_dung"): ["thu_cong", "han_muc", "su_co"],
    ("su_co_agent", "loai"): ["vuot_han_muc", "vuot_gia_tri", "ngoai_gio", "ngoai_jd",
                              "giu_nhap", "tam_dung"],
    ("su_co_agent", "trang_thai"): ["mo", "da_xu_ly"],
    ("su_co_agent", "xu_ly"): ["bo_qua", "nang_han_muc", "giu_tam_dung"],
    ("jd_agent", "state"): ["draft", "in_review", "returned", "approved", "rejected", "withdrawn"],
}  # fmt: skip


def _models() -> dict[str, dict[str, Any]]:
    return {m["name"]: m for m in load_pack(PACK).models}


def test_manifest_and_static_install() -> None:
    loaded = load_pack(PACK)
    m = loaded.manifest
    assert (m.pack, m.version) == ("nhan-su-ai", "0.1.1")
    assert m.depends_on == ["de-xuat-phe-duyet", "nhan-su-vong-doi"]
    assert set(m.models) == PACK_MODELS
    b = m.bindings["jd_agent"]
    assert (b.primitive, b.process, b.inputs["jd_id"].field_) == ("request", "duyet_jd_agent", "id")
    deps = [load_pack(PACKAGES / d) for d in m.depends_on]
    assert static_problems(loaded, "acme", deps) == []


@pytest.mark.parametrize("model", sorted(CONTRACT))
def test_models_match_the_data_contract(model: str) -> None:
    fields = {f["name"]: f for f in _models()[model]["fields"]}
    for name, kind in CONTRACT[model].items():
        assert name in fields, (model, name)
        assert fields[name]["type"] == kind, (model, name)
    for (m, f), values in ENUMS.items():
        if m == model:
            assert [o["value"] for o in fields[f]["options"]] == values


def test_core_managed_fields_are_not_user_writable() -> None:
    ms = _models()
    by = {m: {f["name"]: f for f in ms[m]["fields"]} for m in ms}
    assert by["ho_so_agent"]["so_viec_hom_nay"]["managed_by"] == "workflow"
    assert by["ho_so_agent"]["jd_hieu_luc"]["managed_by"] == "workflow"
    assert by["jd_agent"]["canh_bao"]["managed_by"] == "workflow"
    assert by["jd_agent"]["state"]["managed_by"] == "workflow"
    assert "managed_by" not in by["ho_so_agent"]["trang_thai"]  # Desk đình chỉ/cho nghỉ
    assert by["ho_so_agent"]["trang_thai"]["default"] == "cho_duyet"


# ---- IAM thật -----------------------------------------------------------------------------
PEOPLE = {
    "it": Actor(id="it", tenant=T, roles=("it_admin", "employee"), department="IT", limit=0),
    "head": Actor(id="head", tenant=T, roles=("dept_head", "employee"), department="KHO", limit=0),
    "other": Actor(id="other", tenant=T, roles=("dept_head",), department="KD", limit=0),
    "emp": Actor(id="emp", tenant=T, roles=("employee",), department="KHO", limit=0),
}
WRITES = ("record.create", "record.update", "request.submit", "request.decide")


def agent(client: str, behalf: str) -> Actor:
    return Actor(id=f"agent:{client}", tenant=T, kind="agent", roles=("agent",),
                 department="KHO", limit=0, autonomy=3, behalf=behalf)  # fmt: skip


def req(who: Actor, action: str, model: str, owner: str = "head") -> AccessRequest:
    rid = model if action == "record.create" else "42"
    res = {"owner": owner, "creator": owner, "department": "KHO"}
    return AccessRequest(who, action, f"xrn:{T}:{model}:{rid}", res, now=100)


@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("model", sorted(PACK_MODELS))
@pytest.mark.parametrize("action", WRITES)
def test_external_agent_denied_every_write_even_for_it(
    model: str, action: str, merged: bool
) -> None:
    g = _guard(None if merged else "nhan-su-ai", PEOPLE)
    for client in ("chatgpt", "claude"):
        d = g.evaluate(req(agent(client, "it"), action, model, owner="emp"))
        assert not d.allowed and d.reason is Reason.EXPLICIT_DENY, (client, model, action)


@pytest.mark.parametrize("merged", [False, True])
def test_external_agent_may_read_its_pack(merged: bool) -> None:
    g = _guard(None if merged else "nhan-su-ai", PEOPLE)
    assert g.evaluate(req(agent("chatgpt", "it"), "record.read", "jd_agent")).allowed


@pytest.mark.parametrize("merged", [False, True])
def test_desk_client_writes_only_with_the_person_rights(merged: bool) -> None:
    g = _guard(None if merged else "nhan-su-ai", PEOPLE)
    desk = "xt-desk-agent"
    assert g.evaluate(req(agent(desk, "it"), "record.create", "ho_so_agent")).allowed
    assert g.evaluate(req(agent(desk, "head"), "record.update", "ho_so_agent")).allowed
    no = g.evaluate(req(agent(desk, "emp"), "record.update", "ho_so_agent"))
    assert not no.allowed and no.reason is Reason.BEHALF_PERMISSION
    # IT quyết định JD qua Desk (dispatcher còn human_only + OTP); không ai xóa
    assert g.evaluate(req(agent(desk, "it"), "request.decide", "jd_agent", owner="head")).allowed
    assert not g.evaluate(req(agent(desk, "it"), "record.delete", "ho_so_agent")).allowed
    approve = g.evaluate(req(agent(desk, "it"), "record.approve", "jd_agent"))
    assert not approve.allowed


@pytest.mark.parametrize("merged", [False, True])
def test_humans_it_and_managers_write(merged: bool) -> None:
    g = _guard(None if merged else "nhan-su-ai", PEOPLE)
    for model in sorted(PACK_MODELS):
        assert g.evaluate(req(PEOPLE["it"], "record.update", model)).allowed, model
        assert not g.evaluate(req(PEOPLE["emp"], "record.update", model, owner="emp")).allowed
        assert not g.evaluate(req(PEOPLE["it"], "record.delete", model)).allowed
    for model in ("ho_so_agent", "jd_agent", "giay_phep_hanh_dong", "su_co_agent"):
        assert g.evaluate(req(PEOPLE["head"], "record.create", model)).allowed
        assert not g.evaluate(req(PEOPLE["other"], "record.create", model)).allowed
    assert not g.evaluate(req(PEOPLE["head"], "record.create", "vai_tro_agent")).allowed
    # IT duyệt JD; người lập không tự duyệt; trưởng phòng không duyệt JD
    assert g.evaluate(req(PEOPLE["it"], "request.decide", "jd_agent", owner="head")).allowed
    selfd = g.evaluate(req(PEOPLE["it"], "request.decide", "jd_agent", owner="it"))
    assert not selfd.allowed and selfd.reason is Reason.SELF_APPROVAL
    assert not g.evaluate(req(PEOPLE["head"], "request.decide", "jd_agent", owner="emp")).allowed


# ---- seed 6 vai trò -------------------------------------------------------------------------
def _all_models() -> set[str]:
    return {json.loads(p.read_text())["name"] for p in PACKAGES.glob("*/models/*.json")}


def test_seed_six_vietnamese_roles_on_real_models() -> None:
    seed = json.loads((PACK / "seed" / "01_vai_tro_agent.json").read_text(encoding="utf-8"))
    assert seed["model"] == "vai_tro_agent"
    names = [r["ten"] for r in seed["records"]]
    assert names == [
        "Trợ lý kho",
        "Trợ lý kinh doanh",
        "Chăm sóc khách hàng",
        "Đề xuất mua",
        "Thư ký (chỉ đọc)",
        "Trợ lý nhân sự (PII che)",
    ]
    real = _all_models()
    fields = {f["name"] for f in _models()["vai_tro_agent"]["fields"]}
    for r in seed["records"]:
        assert set(r) - {"_key"} <= fields, r["ma"]
        flat = {"model", "read", "create", "update", "submit"}
        assert all(flat <= set(c) and "thao_tac" not in c for c in r["nang_luc"]), r["ma"]
        caps = parse_capabilities(r["nang_luc"])
        assert set(caps) <= real, (r["ma"], set(caps) - real)
        assert not set(caps) & PACK_MODELS
        parse_window(r["khung_gio"])
        parse_days(r["ngay_lam"])
        jd = JobDescription.from_row({"id": r["ma"], **r})
        assert 0 <= jd.autonomy <= 3
    by = {r["ma"]: parse_capabilities(r["nang_luc"]) for r in seed["records"]}
    assert all(ops == {"read"} for ops in by["thu_ky_chi_doc"].values())
    assert by["tro_ly_nhan_su"]["employee_pii"] == {"read"}
