# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E3: Poka-yoke. Ngày về < ngày đi -> đúng một mã `rule_violation`, thông điệp tiếng Việt theo nhãn
field, có `remediation`; gọi lại với dữ liệu đã sửa -> ok. Qua MCP thật, token Keycloak thật."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from xte2e.flows import A, REQUESTER, advance, dumps, err, key
from xte2e.report import Checks, Evidence

SCENARIO = "E3"
pytestmark = pytest.mark.e2e


def test_rule_violation_then_fixed(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    u = world.by_key(A, REQUESTER)
    start = date.today() + timedelta(days=5)
    bad_values = advance(
        2_000_000, trip_start=start.isoformat(), trip_end=(start - timedelta(days=1)).isoformat()
    )
    bad = world.call(
        u,
        "create_record",
        {"model_id": "advance_request", "values": bad_values, "idempotency_key": key("pk")},
    )
    ev.step("create_record ngày về < ngày đi", bad)
    e = err(bad)
    c.check(bad.get("ok") is False, "bị từ chối (ok=false)")
    c.check(
        e.get("code") == "rule_violation", f"đúng mã rule_violation (thực tế {e.get('code')!r})"
    )
    text = dumps(bad)
    c.check(
        "'Ngày về' phải sau hoặc bằng 'Ngày đi'" in text,
        "thông điệp tiếng Việt theo nhãn field 'Ngày về'/'Ngày đi'",
    )
    c.check(bool(e.get("remediation")), "có remediation")
    fields = {v.get("field") for v in (e.get("details") or {}).get("violations", [])}
    c.check(
        {"trip_start", "trip_end"} <= fields,
        f"chỉ ra field vi phạm: {sorted(f for f in fields if f)}",
    )
    good = world.call(
        u,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(
                2_000_000,
                trip_start=start.isoformat(),
                trip_end=(start + timedelta(days=1)).isoformat(),
            ),
            "idempotency_key": key("pk-ok"),
        },
    )
    ev.step("gọi lại với dữ liệu đã sửa", good)
    c.check(
        good.get("ok") is True and good["data"]["record"]["state"] == "draft",
        "dữ liệu đã sửa -> tạo nháp ok",
    )
    c.done()
