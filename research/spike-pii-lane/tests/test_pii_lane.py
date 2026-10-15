# SPDX-License-Identifier: Apache-2.0
"""Chứng minh làn PII qua MCP thật (Client in-process của SDK `mcp` 2.x), kiểm trên chuỗi JSON-RPC ra dây."""
import json

import pytest

from conftest import FREE_PII, REGISTRY, SEED, assert_no_raw
from xtpii.lane import TOKEN_RE
from xtpii.policy import TenantPolicy, principal_from_claims
from xtpii.store import EngineError

CLOUD = "5c1e7a0e-dcr-claude"


# ------------------------------------------------------------ tier ----
def test_tier_from_azp_fail_closed():
    assert REGISTRY.tier_of("xt-desk-agent") == "local"
    assert REGISTRY.tier_of("xt-desk") == "human"
    for azp in ("5c1e7a0e-dcr-claude", "chatgpt-dcr-77", "claude-code-loopback", "", None):
        assert REGISTRY.tier_of(azp) == "cloud"            # client DCR/không rõ -> cloud
    # client không tự nâng tier bằng claim do mình khai: chỉ registry phía server quyết định
    p = principal_from_claims({"azp": "goose-dcr", "sub": "u1", "sid": "s", "xt_tier": "local", "tenant_id": "t1"}, REGISTRY)
    assert p.tier == "cloud"


def test_agent_override_only_tightens():
    pol = TenantPolicy(agent_overrides={CLOUD: "full", "agent-strict": "local_only"})
    p = principal_from_claims({"azp": CLOUD, "sub": "u", "sid": "s"}, REGISTRY)
    assert pol.mode_for(p) == "masked"                      # override "full" bị bỏ qua
    p2 = principal_from_claims({"azp": "agent-strict", "sub": "u", "sid": "s"}, REGISTRY)
    assert pol.mode_for(p2) == "local_only"


# --------------------------------------------------------- egress ----
def test_cloud_get_record_only_tokens(x):
    res, wire, env = x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    assert not res.is_error
    assert_no_raw(wire)
    r = env["result"]["record"]
    assert r["ma_nv"] == "NV-0042" and r["phong_ban"] == "Kỹ thuật"   # pii=none giữ nguyên
    assert r["so_cccd"] == "⟨NV-0042.so_cccd⟩" and r["luong"] == "⟨NV-0042.luong⟩"
    assert r["_hint"]["ho_ten"] == "Nguyễn V*** A" and r["_hint"]["so_cccd"] == "********2345"
    assert "luong" not in r["_hint"] and "suc_khoe" not in r["_hint"]   # sensitive: không gợi ý
    assert res.structured_content == env                      # structuredContent cũng là bản đã chiếu
    print("\nCLOUD get_record ->", json.dumps(r, ensure_ascii=False))


def test_cloud_search_all_and_free_text_recognizer(x):
    res, wire, env = x.call("search_records", {"model": "nhan_vien"})
    assert_no_raw(wire)
    note = next(r for r in env["result"]["records"] if r["ma_nv"] == "NV-0044")["ghi_chu"]
    assert TOKEN_RE.search(note) and "PII-" in note
    print("\nghi_chu (pii=none) ->", note)


def test_local_tier_sees_full(x):
    x.as_client("xt-desk-agent")
    res, wire, env = x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    r = env["result"]["record"]
    assert r["luong"] == 25000000 and r["so_cccd"] == "001090012345" and r["suc_khoe"] == "Tiểu đường type 2"
    assert env.get("_pii") is None
    a = x.app.audit.rows()[-1]
    assert (a["tier"], a["mode"]) == ("local", "full") and "nhan_vien.luong:raw" in json.loads(a["egress_json"])


def test_local_only_mode_omits_sensitive(x):
    x.policy.agent_overrides[CLOUD] = "local_only"
    res, wire, env = x.call("get_record", {"model": "nhan_vien", "key": "NV-0043"})
    assert_no_raw(wire)
    r = env["result"]["record"]
    assert set(r["_omitted"]) == {"luong", "suc_khoe", "so_tai_khoan"} and "luong" not in r
    assert r["ho_ten"] == "⟨NV-0043.ho_ten⟩" and "_hint" not in r
    # không có token cho field bị bỏ -> không tham chiếu được
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0044", "patch": {"luong": "⟨NV-0043.luong⟩"}})
    assert env["error"]["code"] == "PII_TOKEN_UNKNOWN"


# ---------------------------------------------- token quay lại tool ----
def test_tokens_roundtrip_into_db(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    res, wire, env = x.call("create_record", {"model": "hop_dong", "data": {
        "ma_hd": "HD-2026-001", "ma_nv": "NV-0042", "cccd_nguoi_ky": "⟨NV-0042.so_cccd⟩",
        "sdt_lien_he": "⟨NV-0042.so_dien_thoai⟩", "muc_luong": "⟨NV-0042.luong⟩", "ngay_ky": "2026-10-15",
        "ghi_chu": "Ký tại văn phòng"}})
    assert not res.is_error, env
    assert_no_raw(wire)
    row = x.db("hop_dong", "ma_hd", "HD-2026-001")
    assert (row["cccd_nguoi_ky"], row["sdt_lien_he"], row["muc_luong"]) == ("001090012345", "0912345678", 25000000)
    # cập nhật bản ghi khác bằng token
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0044",
                                              "patch": {"so_dien_thoai": "⟨NV-0042.so_dien_thoai⟩"}})
    assert not res.is_error and x.db("nhan_vien", "ma_nv", "NV-0044")["so_dien_thoai"] == "0912345678"
    assert_no_raw(wire)
    print("\nDB hop_dong ->", {k: row[k] for k in ("cccd_nguoi_ky", "sdt_lien_he", "muc_luong")})
    print("LLM thấy     ->", {k: env["result"]["record"][k] for k in ("so_dien_thoai",)})


def test_free_token_roundtrip(x):
    _, _, env = x.call("get_record", {"model": "nhan_vien", "key": "NV-0044"})
    tok = TOKEN_RE.search(env["result"]["record"]["ghi_chu"]).group(0)      # ⟨PII-1.phone⟩
    res, wire, env = x.call("create_record", {"model": "hop_dong", "data": {"ma_hd": "HD-2", "sdt_lien_he": tok}})
    assert not res.is_error and x.db("hop_dong", "ma_hd", "HD-2")["sdt_lien_he"] == "0978 111 222"
    assert_no_raw(wire)


def test_embedded_token_in_text_same_level(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    # pii basic -> basic: cho phép chèn token vào giữa chuỗi
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043",
                                              "patch": {"dia_chi": "Gửi nhờ: ⟨NV-0042.dia_chi⟩"}})
    assert not res.is_error and x.db("nhan_vien", "ma_nv", "NV-0043")["dia_chi"] == "Gửi nhờ: 12 Lý Thường Kiệt, Hoàn Kiếm, Hà Nội"
    assert_no_raw(wire)


# -------------------------------------------------------- lỗi echo ----
def test_validation_error_echo_is_scrubbed(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    # chứng minh engine THẬT SỰ echo input trong lỗi
    with pytest.raises(EngineError) as ei:
        x.app.store.update("nhan_vien", "NV-0043", {"luong": SEED[0]["dia_chi"]})
    assert SEED[0]["dia_chi"] in ei.value.message
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"luong": "⟨NV-0042.dia_chi⟩"}})
    assert res.is_error and env["error"]["code"] == "VALIDATION_FAILED"
    assert "⟨NV-0042.dia_chi⟩" in env["error"]["message"]
    assert_no_raw(wire)
    print("\nlỗi validate ra LLM ->", env["error"]["message"].replace("\n", " | ")[:200])


def test_unique_violation_echo_is_scrubbed(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"so_cccd": "⟨NV-0042.so_cccd⟩"}})
    assert env["error"]["code"] == "DUPLICATE" and "⟨NV-0042.so_cccd⟩" in env["error"]["message"]
    assert_no_raw(wire)
    print("\nlỗi unique ra LLM ->", env["error"]["message"])


def test_engine_error_with_raw_literal_and_token(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    # op lạ trên field basic: làn chặn trước khi engine kịp echo giá trị đã detokenize
    res, wire, env = x.call("search_records", {"model": "nhan_vien",
                                               "filter": {"so_dien_thoai": {"op": "regex", "value": "⟨NV-0042.so_dien_thoai⟩"}}})
    assert env["error"]["code"] == "PII_FILTER_DENIED"
    assert_no_raw(wire)
    # agent tự gõ số thật vào filter field pii=none với op sai: engine echo repr(value), bộ nhận dạng che
    res, wire, env = x.call("search_records", {"model": "nhan_vien", "filter": {"phong_ban": {"op": "like", "value": "0978111222"}}})
    assert env["error"]["code"] == "BAD_FILTER" and "⟨PII-" in env["error"]["message"]
    assert_no_raw(wire)
    print("\nlỗi filter ra LLM ->", env["error"]["message"])


def test_unexpected_exception_scrubbed(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})

    def boom(**kw):
        raise RuntimeError(f"không ghi được {kw['v']} vào sổ")
    from xtpii.lane import ToolShape
    env, is_err = x.app.lane.call(x.p, "boom", ToolShape(model_arg=None), boom, {"v": "⟨NV-0042.suc_khoe⟩"})
    # arg ngoài schema = mức none -> token sensitive bị từ chối trước khi tới engine
    assert env["error"]["code"] == "PII_DOWNGRADE"
    env, is_err = x.app.lane.call(x.p, "boom", ToolShape(model_arg=None), boom, {"v": "SĐT 0912345678"})
    assert env["error"]["code"] == "INTERNAL" and "0912345678" not in json.dumps(env, ensure_ascii=False)


# --------------------------------------------------------- chặn rò ----
def test_downgrade_into_lower_field_rejected(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    for patch in ({"ghi_chu": "⟨NV-0042.luong⟩"}, {"ghi_chu": "Lương cũ: ⟨NV-0042.luong⟩"},
                  {"so_dien_thoai": "⟨NV-0042.suc_khoe⟩"}):
        res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": patch})
        assert env["error"]["code"] == "PII_DOWNGRADE", env
        assert_no_raw(wire)
    assert x.db("nhan_vien", "ma_nv", "NV-0043")["ghi_chu"] is None


def test_filter_and_sort_oracle_denied(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    for args in ({"filter": {"luong": {"op": "gt", "value": 20000000}}}, {"filter": {"luong": 25000000}},
                 {"filter": {"suc_khoe": {"op": "contains", "value": "gan"}}}, {"sort": ["-luong"]}):
        res, wire, env = x.call("search_records", {"model": "nhan_vien", **args})
        assert env["error"]["code"] in ("PII_FILTER_DENIED", "PII_SORT_DENIED"), env
    res, wire, env = x.call("search_records", {"model": "nhan_vien", "filter": {"luong": "⟨NV-0042.luong⟩"}})
    assert [r["ma_nv"] for r in env["result"]["records"]] == ["NV-0042"]
    assert_no_raw(wire)


def test_token_from_other_session_rejected(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    x.as_client(CLOUD, sid="sid-B")
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"so_cccd": "⟨NV-0042.so_cccd⟩"}})
    assert env["error"]["code"] == "PII_TOKEN_UNKNOWN"
    assert_no_raw(wire)
    x.as_client("chatgpt-dcr-77", sid="sid-A")            # cùng sid nhưng khác client -> phiên khác
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"so_cccd": "⟨NV-0042.so_cccd⟩"}})
    assert env["error"]["code"] == "PII_TOKEN_UNKNOWN"


# ----------------------------------------------------------- audit ----
def test_audit_records_egress_without_raw(x):
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"luong": "⟨NV-0042.dia_chi⟩"}})
    x.call("search_records", {"model": "nhan_vien", "sort": ["luong"]})
    x.as_client("xt-desk-agent")
    x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    rows = x.app.audit.rows()
    assert [(r["tool"], r["tier"], r["mode"], r["outcome"]) for r in rows] == [
        ("get_record", "cloud", "masked", "ok"), ("update_record", "cloud", "masked", "VALIDATION_FAILED"),
        ("search_records", "cloud", "masked", "PII_SORT_DENIED"), ("get_record", "local", "full", "ok")]
    eg = json.loads(rows[0]["egress_json"])
    assert eg["nhan_vien.luong:token"] == 1 and eg["nhan_vien.ho_ten:token_hint"] == 1
    assert json.loads(rows[1]["detok_json"]) == ["⟨NV-0042.dia_chi⟩"] and rows[1]["scrubbed"] >= 1
    assert all(r["prev_hash"] == p["entry_hash"] for p, r in zip(rows, rows[1:]))
    assert_no_raw(json.dumps(rows, ensure_ascii=False))
    for r in rows:
        print("\nAUDIT", {k: r[k] for k in ("seq", "azp", "tier", "mode", "tool", "outcome", "egress_json", "detok_json", "scrubbed", "hits_json")})


def test_tools_list_visibility(x):
    import asyncio
    from mcp import Client

    async def go():
        async with Client(x.app.mcp) as c:
            return await c.list_tools()
    tools = {t.name: t for t in asyncio.run(go()).tools}
    assert tools["pii_reveal_ticket"].meta["ui"]["visibility"] == ["app"]
    assert tools["get_record"].meta["ui"]["resourceUri"] == "ui://xt/pii-card.html"
