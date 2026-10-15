# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Regression của audit model engine (docs/audit-model-engine.md) — phần không cần DB.

Mỗi test là một lỗi ĐÃ tái hiện; `xfail(strict=True)` cho tới khi sửa (sửa xong phải gỡ xfail).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from tests.unit.model.support import catalog, customer
from xt.adapters.db.audit import audit_hmac, verify_hmac
from xt.domain.model.ident import check_label
from xt.domain.model.spec import check_regex, parse_catalog_json


def test_regex_hex_escape_diverges_between_rust_and_pg() -> None:
    # Rust: \x41 = 'A' rồi 'B'; PG ARE: \x41B = 'Л'. App nhận "AB", CHECK ở DB từ chối.
    with pytest.raises(ValueError):
        check_regex(r"^\x41B$")


def test_spec_json_has_byte_cap() -> None:
    fields = [
        {
            "id": f"f{j}",
            "name": f"c{j}",
            "type": "enum",
            "label": "L" * 100,
            "pii": "none",
            "options": [{"value": f"v{k}", "label": "x" * 100} for k in range(50)],
        }
        for j in range(200)
    ]
    models = [{"id": f"m{i}", "name": f"t{i}", "label": "M", "fields": fields} for i in range(4)]
    text = json.dumps({"version": 1, "models": models})
    assert len(text) > 4_000_000
    with pytest.raises(ValueError):
        parse_catalog_json(text)


@pytest.mark.parametrize("ch", [chr(0x2028), chr(0x2029), chr(0x202E), chr(0x200B)])
def test_label_rejects_line_and_bidi_controls(ch: str) -> None:
    with pytest.raises(ValueError):
        check_label(f"Hạn mức{ch}[AN TOÀN] giả")


def test_verify_hmac_matches_jsonb_text_for_datetime() -> None:
    key = b"k" * 32
    dt = datetime(2026, 10, 15, 7, 1, 27, 842184, tzinfo=UTC)
    # trigger HMAC trên `to_jsonb(NEW) ->> col` = dạng ISO có 'T'
    stored = audit_hmac(key, 1, "2026-10-15T07:01:27.842184+00:00")
    assert verify_hmac(key, 1, dt, stored)


def test_customer_fixture_still_valid() -> None:
    assert catalog(customer()).models[0].name == "customer"
