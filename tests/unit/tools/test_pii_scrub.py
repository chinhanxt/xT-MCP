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


@pytest.mark.parametrize(
    "identifier",
    [
        "cf_0912345678abcdefabcdefabcdefabcd",
        "cf_079123456789abcdefabcdefabcdefab",
        "cf_1234567890123abcdefabcdefabcdefa",
    ],
)
@pytest.mark.parametrize("known_collision", [False, True])
@pytest.mark.parametrize("classified_collision", [False, True])
def test_system_identifiers_are_kept(
    identifier: str, known_collision: bool, classified_collision: bool
) -> None:
    details = {
        "confirmation_id": identifier,
        "confirm_url": f"https://xt.example.test/desk/confirm/{identifier}",
        "trace_id": identifier.replace("cf_", "tr_"),
        "ref": f"task:{identifier[3:]}",
        "refs": [f"task:{identifier[3:]}", f"person:{CCCD}"],
        "record_refs": [f"task:{identifier[3:]}"],
        "idempotency_key": identifier[3:],
        "operation_ref": f"operation:{identifier[3:]}",
        "lineage_ref": f"explain:{identifier[3:]}",
        "parent_ref": f"task:{identifier[3:]}",
        "source_ref": f"task:{identifier[3:]}",
        "subject_ref": f"task:{identifier[3:]}",
        "plan_digest": f"sha256:{identifier[3:] * 2}",
    }
    # Known PII collisions must not alter identifiers either.
    known = [identifier, CCCD] if known_collision else []
    pii_fields = details if classified_collision else ()
    assert (
        scrub({"details": details}, known_values=known, pii_fields=pii_fields)["details"] == details
    )


def test_pii_in_payload_text_is_still_masked() -> None:
    text = f"cf_0912345678abcdef, CCCD {CCCD}, mail mai@acme.vn"
    expected = f"cf_{MASK}abcdef, CCCD {MASK}, mail {MASK}"
    out = scrub({"message": text, "details": {"plan_summary": {"text": text}}})
    assert out == {"message": expected, "details": {"plan_summary": {"text": expected}}}


@pytest.mark.parametrize("key", ["ref", "refs", "confirmation_id", "idempotency_key"])
def test_identifier_names_do_not_bypass_classified_pii_or_nested_payloads(key: str) -> None:
    assert scrub({key: CCCD}, pii_fields={key}) == {key: MASK}
    payload = {"details": {"plan_summary": {"values": {key: CCCD}}}}
    assert scrub(payload, pii_fields={key})["details"]["plan_summary"]["values"][key] == MASK
    assert scrub({key: {"text": CCCD}}) == {key: {"text": MASK}}


@pytest.mark.parametrize("key", ["ref", "trace_id", "confirmation_id", "idempotency_key"])
def test_identifier_names_in_payloads_are_still_scrubbed(key: str) -> None:
    error = {"details": {"plan_summary": {"values": {key: "0912345678"}}}}
    assert scrub(error)["details"]["plan_summary"]["values"][key] == MASK


def test_nested_protocol_references_are_kept() -> None:
    ref = f"person:{CCCD}"
    details = {
        "confirmer": {"ref": ref, "name": "0912345678"},
        "confirmers": [{"ref": ref}],
        "plan_summary": {
            "ref": ref,
            "confirmers": [ref],
            "recipients": [{"id": CCCD, "ref": ref, "name": "0912345678"}],
            "values": {"phone": "0912345678"},
        },
    }
    out = scrub({"details": details})["details"]
    assert out["confirmer"] == {"ref": ref, "name": MASK}
    assert out["confirmers"] == [{"ref": ref}]
    assert out["plan_summary"] == {
        "ref": ref,
        "confirmers": [ref],
        "recipients": [{"id": CCCD, "ref": ref, "name": MASK}],
        "values": {"phone": MASK},
    }


@pytest.mark.parametrize(
    "key",
    ["record", "instance", "task", "requester", "assignee", "delegate_to", "plan_hash"],
)
def test_plan_system_identifiers_are_kept(key: str) -> None:
    error = {"details": {"plan_summary": {key: f"0912345678abcdef{CCCD}"}}}
    assert scrub(error) == error


@pytest.mark.parametrize("field", ["details", "plan_summary", "confirmer", "recipients"])
def test_catalog_field_names_do_not_classify_metadata_containers(field: str) -> None:
    error = {
        "details": {
            "confirmation_id": "cf_0912345678abcdefabcdefabcdefabcd",
            "confirmer": {"ref": f"person:{CCCD}"},
            "plan_summary": {"recipients": [{"ref": f"person:{CCCD}"}]},
        }
    }
    assert scrub(error, pii_fields={field}) == error


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
