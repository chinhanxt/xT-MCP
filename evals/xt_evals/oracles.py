# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Oracle: đáp án đúng tính thẳng từ fixture (không qua tool), để scenario không hard-code số."""
from __future__ import annotations

from collections import Counter
from decimal import Decimal

from .mock.world import World


def compute(seed: str = "binhminh") -> dict:
    w = World(seed)
    adv = [r for r in w.records.values() if r["model_id"] == "advance_request"]
    emp = [r for r in w.records.values() if r["model_id"] == "employee"]
    tasks = [r for r in w.records.values() if r["model_id"] == "task"]

    def tot(states, month=None):
        return sum(Decimal(r["values"]["amount"]) for r in adv if r["state"] in states and (month is None or r["created_at"].startswith(month)))
    org = World(seed if "org" in seed else "binhminh+org")
    org_emp = [r for r in org.records.values() if r["model_id"] == "employee" and r["id"].startswith("org-")]
    return {
        "org_active_by_dept": dict(Counter(r["values"]["department"] for r in org_emp if r["state"] != "left")),
        "org_total": len(org_emp),
        "adv_by_state": dict(Counter(r["state"] for r in adv)),
        "adv_approved_sep": int(tot({"approved"}, "2026-09")),
        "adv_approved_paid_sep": int(tot({"approved", "paid"}, "2026-09")),
        "adv_approved_all": int(tot({"approved"})),
        "tasks_by_state_kd": dict(Counter(r["state"] for r in tasks if r["values"]["department"] == "KD")),
        "headcount_by_dept": dict(Counter(r["values"]["department"] for r in emp)),
        "adv_paid_avg": int(sum(Decimal(r["values"]["amount"]) for r in adv if r["state"] == "paid") / max(1, sum(1 for r in adv if r["state"] == "paid"))),
        "lifecycle_processing": sum(1 for r in w.records.values() if r["model_id"] == "lifecycle_case" and r["state"] == "processing"),
        "tasks_blocked": sum(1 for r in tasks if r["state"] == "blocked"),
        "overdue_task_refs": [r["ref"] for r in tasks if w.sla_status(r) == "breached"],
    }
