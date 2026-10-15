# SPDX-License-Identifier: Apache-2.0
"""What each engine can say natively about a decision (before xT's shared diagnostics)."""

from dataclasses import replace
import json
from backends import BACKENDS
from contracts import Request
from cases import document, statement, condition

DOC = document(
    statement("own", conditions=[condition("resource.owner", "eq", {"ref": "subject.id"})]),
    statement("staff-update", actions=["record.update"]),
    statement(
        "no-hr",
        effect="Deny",
        subjects=["*"],
        conditions=[condition("resource.department", "ne", "sales")],
    ),
    statement("limit", conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})]),
)
CASES = {
    "allow (3 allows match)": Request(),
    "explicit deny (no-hr)": Request(resource={**Request().resource, "department": "hr"}),
    "implicit deny (amount>limit, other owner)": replace(
        Request(),
        action="record.read",
        resource={**Request().resource, "owner": "bob", "amount": 5001},
    ),
}
for label, req in CASES.items():
    print(f"### {label}")
    for name, cls in BACKENDS.items():
        b = cls(DOC)
        d = b.evaluate(req)
        print(
            f"  {name:6} allowed={d.allowed!s:5} reason={d.reason:13} statements={list(d.statements)} native={d.trace}"
        )
    why = BACKENDS["mini"](DOC).explain(req)
    failing = [
        {
            "id": t["id"],
            "failed": [k for k, v in t["predicates"].items() if not v],
            "conditions": [c for c in t["conditions"] if not c["passed"]],
        }
        for t in why["trace"]
        if not t["matched"]
    ]
    print("  shared explain() -> not matched:", json.dumps(failing, ensure_ascii=False))
