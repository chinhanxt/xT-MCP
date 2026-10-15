# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# ruff: noqa: E501
"""The v1.0 internal notification contract, matching tool-contracts-v1 §6.4."""

from xt.contracts.tools.contract import Effects, ToolContract
from xt.contracts.tools.schema_parts import IDEM, NAMED, PERSON, inp, out, ref, s

NOTIFY = ToolContract(
    name="notify",
    since="1.0",
    description="Send an internal reminder/notice to colleagues (xT inbox + their configured channel such as Telegram). Never reaches people outside the organization. VD: 'Nhắc anh B nộp hoàn ứng'",
    max_autonomy="L2",
    effects=Effects(mutates_records=True, notifies_internal=True, idempotent=True),
    iam=("notify.send",),
    errors=("invalid_request", "not_found", "access_denied", "confirmation_required", "rate_limited", "idempotency_conflict"),
    input_schema=inp(
        {
            "recipients": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": {"type": "string", "pattern": PERSON},
                "description": "Person refs.",
            },
            "message": s("Short message (Vietnamese ok).", minLength=1, maxLength=1000),
            "subject_ref": ref("Item the message is about; recipients get a link."),
            "idempotency_key": IDEM,
        },
        ["recipients", "message", "idempotency_key"],
    ),
    output_schema=out(
        {
            "notifications": {
                "type": "array",
                "items": out(
                    {
                        "ref": {"type": "string"},
                        "recipient": NAMED,
                        "status": {"enum": ["queued", "sent", "failed"]},
                    },
                    ["ref", "recipient", "status"],
                ),
            }
        },
        ["notifications"],
    ),
    examples=(
        {
            "recipients": ["person:b"],
            "message": "Nhắc anh nộp hoàn ứng TU-2026-0042 trước 30/11",
            "subject_ref": "advance_request:tu-2026-0042",
            "idempotency_key": "ntf-hoanung-0042",
        },
    ),
    sample_output={
        "notifications": [
            {
                "ref": "notification:01jd5k",
                "recipient": {"ref": "person:b", "name": "B"},
                "status": "queued",
            }
        ]
    },
)
