# SPDX-License-Identifier: Apache-2.0
"""Hồi quy trường hợp biên của làn PII: tier cloud không bao giờ đưa giá trị thật trong raw_corpus() ra dây."""
import json
import unicodedata

import pytest

from conftest import SEED, assert_no_raw
from xtpii.lane import ToolShape

NV42 = SEED[0]


def _read42(x):
    res, wire, env = x.call("get_record", {"model": "nhan_vien", "key": "NV-0042"})
    assert not res.is_error
    assert_no_raw(wire)


# ------------------------------------------------- biến thể giá trị lỗi ----
@pytest.mark.parametrize("variant", [
    pytest.param(lambda s: unicodedata.normalize("NFD", s), id="nfd"),
    pytest.param(str.upper, id="upper"),
    pytest.param(lambda s: unicodedata.normalize("NFD", s.swapcase()), id="nfd-swapcase"),
])
def test_error_echo_unicode_and_case_variants(x, variant):
    _read42(x)
    val = variant(NV42["ho_ten"])
    assert val != NV42["ho_ten"]
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"luong": val}})
    assert res.is_error and env["error"]["code"] == "VALIDATION_FAILED"
    assert_no_raw(wire)
    assert val not in wire and val.lower() not in wire.lower()


# ------------------------------------------------------------- token ----
def test_two_adjacent_tokens_in_one_string(x):
    _read42(x)
    # thiếu ma_hd -> pydantic "Field required ... input_value={...}" echo cả dict đã detokenize
    res, wire, env = x.call("create_record", {"model": "hop_dong", "data": {
        "cccd_nguoi_ky": "⟨NV-0042.so_dien_thoai⟩⟨NV-0042.so_cccd⟩"}})
    assert res.is_error and env["error"]["code"] == "VALIDATION_FAILED"
    assert_no_raw(wire)


@pytest.mark.parametrize("bad", ["⟨NV-0042.SO_CCCD⟩", "⟨NV-0042.so_cccd", "⟨.so_cccd⟩", "⟨NV-0042..so_cccd⟩",
                                 "<NV-0042.so_cccd>", "⟨NV 0042.so_cccd⟩"])
def test_malformed_token_passes_literally_without_raw(x, bad):
    _read42(x)
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043", "patch": {"luong": bad}})
    assert res.is_error
    assert_no_raw(wire)
    assert x.db("nhan_vien", "ma_nv", "NV-0043")["luong"] == SEED[1]["luong"]


def test_well_formed_token_never_issued(x):
    _read42(x)
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0042",
                                              "patch": {"so_cccd": "⟨NV-0043.so_cccd⟩"}})
    assert env["error"]["code"] == "PII_TOKEN_UNKNOWN"
    assert_no_raw(wire)
    assert x.db("nhan_vien", "ma_nv", "NV-0042")["so_cccd"] == NV42["so_cccd"]


# ------------------------------------------------------- filter/sort ----
# Đã sửa: field basic chỉ cho lọc eq khi mode != full (TenantPolicy.basic_filter_ops)
def test_contains_on_basic_field_is_digit_oracle(x):
    known = ""
    for _ in range(len(NV42["so_cccd"])):
        for d in "0123456789":
            res, wire, env = x.call("search_records", {"model": "nhan_vien", "filter": {
                "ma_nv": "NV-0042", "so_cccd": {"op": "contains", "value": known + d}}})
            assert_no_raw(wire)
            if not res.is_error and len(env["result"]["records"]) == 1:
                known += d
                break
        else:
            break
    assert known != NV42["so_cccd"], f"suy ra được CCCD qua {len(known)} chữ số"


def test_sensitive_filter_token_with_non_eq_op(x):
    _read42(x)
    for op in ("gt", "lt", "gte", "lte", "contains", "ne", "EQ"):
        res, wire, env = x.call("search_records", {"model": "nhan_vien", "filter": {
            "luong": {"op": op, "value": "⟨NV-0042.luong⟩"}}})
        assert env["error"]["code"] == "PII_FILTER_DENIED", (op, env)
        assert_no_raw(wire)


def test_sort_sensitive_without_dash(x):
    _read42(x)
    for s in (["luong"], ["--luong"], ["phong_ban", "suc_khoe"], ["so_tai_khoan"]):
        res, wire, env = x.call("search_records", {"model": "nhan_vien", "sort": s})
        assert env["error"]["code"] == "PII_SORT_DENIED", (s, env)
        assert_no_raw(wire)


def test_sort_unknown_field_names(x):
    _read42(x)
    res, wire, env = x.call("search_records", {"model": "nhan_vien",
                                               "sort": ["khong_ton_tai", "LUONG", "luong ", "-0912345678"]})
    assert_no_raw(wire)


def test_negative_limit(x):
    res, wire, env = x.call("search_records", {"model": "nhan_vien", "limit": -1})
    assert_no_raw(wire)
    if not res.is_error:
        assert all(r["so_cccd"].startswith("⟨") for r in env["result"]["records"])


# ------------------------------------------------------------ key ----
def test_key_is_token(x):
    _read42(x)
    for tok in ("⟨NV-0042.so_cccd⟩", "⟨NV-0042.ho_ten⟩", "⟨NV-0042.luong⟩"):
        res, wire, env = x.call("get_record", {"model": "nhan_vien", "key": tok})
        assert res.is_error, env
        assert_no_raw(wire)


def test_patch_key_field_to_token(x):
    _read42(x)
    res, wire, env = x.call("update_record", {"model": "nhan_vien", "key": "NV-0043",
                                              "patch": {"ma_nv": "⟨NV-0042.so_dien_thoai⟩"}})
    assert env["error"]["code"] == "PII_DOWNGRADE"
    assert_no_raw(wire)


def test_key_field_value_equals_raw_pii_leaks_via_token_root(x):
    # ma_nv (pii=none) mang giá trị trùng SĐT thật của người khác; ma_nv là gốc token ⟨ma_nv.field⟩
    x.conn.execute('UPDATE nhan_vien SET ma_nv=? WHERE ma_nv=?', (NV42["so_dien_thoai"], "NV-0044"))
    res, wire, env = x.call("search_records", {"model": "nhan_vien"})
    assert_no_raw(wire)


# ------------------------------------------------- create field lạ ----
@pytest.mark.parametrize("val", ["⟨NV-0042.so_cccd⟩", "⟨NV-0042.luong⟩", NV42["ho_ten"], NV42["dia_chi"]],
                         ids=["token-basic", "token-sensitive", "raw-name", "raw-address"])
def test_create_unknown_field_extra_forbidden(x, val):
    _read42(x)
    res, wire, env = x.call("create_record", {"model": "hop_dong", "data": {"ma_hd": "HD-X", "khong_co": val}})
    assert res.is_error, env
    assert_no_raw(wire)


# ------------------------------------------------ lương trong ghi chú ----
@pytest.mark.parametrize("note", ["Lương 25.000.000 đ", "Lương 25.000.000đ", "Lương 25000000VND", "Lương:25,000,000/tháng"])
def test_salary_formats_in_free_note(x, note):
    x.app.store.update("nhan_vien", "NV-0043", {"ghi_chu": note})
    res, wire, env = x.call("search_records", {"model": "nhan_vien"})
    assert not res.is_error
    assert_no_raw(wire)


# Đã sửa: _variants sinh thêm dạng viết tắt triệu cho số tiền
def test_salary_shorthand_in_free_note(x):
    x.app.store.update("nhan_vien", "NV-0043", {"ghi_chu": "Lương anh An 25tr, chị Ngọc 18,5 triệu"})
    res, wire, env = x.call("search_records", {"model": "nhan_vien"})
    assert_no_raw(wire)
    assert "25tr" not in wire and "18,5 triệu" not in wire


# ---------------------------------------------- arg ngoài schema ----
def test_deeply_nested_token_in_non_schema_arg(x):
    _read42(x)

    def echo(**kw):
        raise RuntimeError(json.dumps(kw, ensure_ascii=False))
    for tok in ("⟨NV-0042.so_cccd⟩", "⟨NV-0042.luong⟩"):
        args = {"opts": {"a": {"b": [{"c": {"d": [f"x {tok} y"]}}]}}}
        env, is_err = x.app.lane.call(x.p, "echo", ToolShape(model_arg=None), echo, args)
        assert is_err and env["error"]["code"] == "PII_DOWNGRADE", env
        assert_no_raw(json.dumps(env, ensure_ascii=False))
