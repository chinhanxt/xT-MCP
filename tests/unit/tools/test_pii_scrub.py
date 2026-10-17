# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# ruff: noqa: RUF001  (mẫu cố ý chứa NBSP, chữ số fullwidth của audit)
"""Lưới PII cho envelope lỗi: mọi mẫu của audit R2-M1 bị che; tiền, revision, khóa giao thức
và continuation giữ nguyên (R2-M2)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from xt.application.tools.pii_scrub import ErrorScrubber, normalize

CCCD = "079123456789"
MASK = "[PII:x]"


def scrub(error: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return ErrorScrubber(lambda v: MASK, **kw).error(error)


@pytest.mark.parametrize(
    "details",
    [
        {"message": f"so_cccd:{CCCD} đã tồn tại"},
        {"message": f"id-{CCCD}"},
        {"message": f"so_cccd_{CCCD}"},
        {"message": f"[{CCCD}]"},
        {"message": f"a/{CCCD}"},
        {"message": "CCCD 079-123-456-789"},
        {"message": "CCCD 079.123.456.789"},
        {"message": "CCCD 079 123 456 789"},
        {"message": "CCCD 0791​23456789"},
        {"message": "CCCD ０７９123456789"},
        {"by_value": {CCCD: "duplicate"}},
        {"value": 912345678},
        {"value": 79123456789},
        {"message": "SĐT 0912345678"},
        {"message": "SĐT +84 912 345 678"},
        {"message": "Key (so_dien_thoai)=(0912.345.678) already exists"},
        {"message": "mail mai%40acme.vn"},
        {"message": "mail mai@acme.vn"},
    ],
)
def test_auditor_echo_forms_are_masked(details: dict[str, Any]) -> None:
    out = repr(scrub({"code": "rule_violation", "message": "x", "details": details}))
    digits = re.sub(r"[^0-9]", "", out)
    for raw in ("123456789", "912345678", "345678"):
        assert raw not in digits
    assert "acme.vn" not in out and MASK in out


def test_field_classification_masks_values_of_pii_fields() -> None:
    err = {
        "code": "rule_violation",
        "message": "Trùng giá trị",
        "details": {
            "violations": [
                {"field": "full_name", "rule": "unique", "message": "Lê Văn Phúc đã tồn tại"},
                {"field": "amount", "rule": "max", "message": "Tối đa 30000000"},
            ],
            "input": {"full_name": "Lê Văn Phúc", "amount": 150000000},
        },
    }
    out = scrub(err, pii_fields={"full_name"})
    v = out["details"]["violations"]
    assert v[0]["message"] == MASK and v[0]["field"] == "full_name"
    assert v[1]["message"] == "Tối đa 30000000"
    assert out["details"]["input"] == {"full_name": MASK, "amount": 150000000}


def test_known_values_are_masked_anywhere() -> None:
    out = scrub(
        {"message": "Nguyễn Thị Mai đã được giao", "details": {}}, known_values=["Nguyễn Thị Mai"]
    )
    assert "Nguyễn Thị Mai" not in repr(out)


@pytest.mark.parametrize(
    "message",
    [
        "Số tiền 150000000 vượt hạn mức 100000000 mỗi lần",
        "Số tiền 300000000 vượt hạn mức",
        "revision 1234567890 đã cũ",
        "trace tr_0123456789abcdef",
        "sha256:0791234567890791234567890791234567890791234567890791234567890791",
    ],
)
def test_business_numbers_and_ids_are_kept(message: str) -> None:
    assert scrub({"message": message, "details": {}})["message"] == normalize(message)


def test_protocol_fields_and_numeric_business_keys_are_kept() -> None:
    err = {
        "code": "revision_conflict",
        "retryable": False,
        "explain_ref": "explain:079123456789",
        "message": "changed",
        "details": {"current_revision": 912345678, "amount": 912345678, "limit_bytes": 3e5},
    }
    out = scrub(err)
    assert out == err


@pytest.mark.parametrize("field", [f"f{CCCD}", "phone0912345678", "acct1234567890123456"])
@pytest.mark.parametrize("detail_key", ["field", "fields", "valid_fields", "writable_fields"])
def test_pii_in_field_identifiers_is_masked(field: str, detail_key: str) -> None:
    value: Any = field if detail_key == "field" else [field, "task_no", "f12"]
    out = scrub({"message": f"Field '{field}' is not writable.", "details": {detail_key: value}})
    assert field not in repr(out)
    assert MASK in repr(out)
    if detail_key != "field":
        assert out["details"][detail_key][1:] == ["task_no", "f12"]


def test_pii_in_mapping_keys_is_masked_without_changing_business_values() -> None:
    out = scrub({"message": "Unknown fields.", "details": {f"f{CCCD}": 150000000}})
    assert CCCD not in repr(out)
    assert list(out["details"].values()) == [150000000]
