# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Notification metadata and schemas are byte-independent equality checks against §6.4."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from xt.contracts.tools import BY_NAME, CORE_10, input_issues
from xt.contracts.tools.notify import NOTIFY


def test_notify_matches_full_documented_contract() -> None:
    source = Path("docs/tool-contracts-v1.md").read_text()
    section = source.split("### 6.4. `notify`", 1)[1]
    documented = json.loads(section.split("```json\n", 1)[1].split("```", 1)[0])
    assert NOTIFY.doc_view() == documented
    assert BY_NAME["notify"] is NOTIFY
    assert "notify" not in CORE_10


def test_notify_examples_and_sample_validate() -> None:
    Draft202012Validator.check_schema(NOTIFY.input_schema)
    Draft202012Validator.check_schema(NOTIFY.output_schema)
    for example in NOTIFY.examples:
        assert not input_issues(NOTIFY, example)
    Draft202012Validator(NOTIFY.output_schema).validate(NOTIFY.sample_output)
    assert NOTIFY.annotations.as_mcp() == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }


@pytest.mark.parametrize(
    "extra", [{"channel": "telegram"}, {"chat_id": 42}, {"approved": True}, {"confirm_url": "x"}]
)
def test_notify_rejects_transport_or_confirmation_input(extra: dict[str, object]) -> None:
    assert input_issues(NOTIFY, {**NOTIFY.examples[0], **extra})
