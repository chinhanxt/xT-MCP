# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit lớp MCP vòng 3 (docs/archive/audit-mcp.md, mục "Vòng 3"): phần còn hở của R2-M1 trên làn PII
thật. Đã sửa; giữ làm hồi quy."""

from __future__ import annotations

from typing import Any

import pytest

from tests.unit.tools.audit.test_audit_mcp_r2 import CCCD, _real_lane
from tests.unit.tools.fakes import agent_ctx
from xt.application.tools.context import ClientClass

ACCT = "9704225566778899"


def _scrub(details: dict[str, Any]) -> str:
    ctx = agent_ctx("sub-mai", client_class=ClientClass.CLOUD)
    error = {"code": "rule_violation", "message": "x", "details": details}
    return repr(_real_lane().scrub_error(ctx, "update_record", error))


def test_known_pii_value_in_free_text_is_scrubbed() -> None:
    """Số tài khoản (field `pii` của advance_request) nằm trong thông điệp tự do, không gắn khóa
    field: chỉ nguồn "giá trị đã biết" bắt được. Phản hồi tác giả vòng 2 nói đã có nguồn này."""
    out = _scrub({"message": f"Số tài khoản {ACCT} đã được dùng cho đề nghị khác"})
    assert ACCT not in out


@pytest.mark.parametrize(
    "details",
    [
        {"value": 912345678.0},
        {"value": 79123456789.0},
        {"message": "CCCD 079_123_456_789"},
        {"message": "CCCD 079/123/456/789"},
    ],
)
def test_real_lane_scrubs_float_and_other_separators(details: dict[str, Any]) -> None:
    out = _scrub(details)
    for raw in (CCCD, "912345678", "79123456789", "079_123_456_789", "079/123/456/789"):
        assert raw not in out


def test_composition_transaction_failure_log_has_no_pii(caplog: pytest.LogCaptureFixture) -> None:
    """R2-L3 sửa ở dispatcher/adapter MCP, nhưng mọi lời gọi thật đi qua `ScopedDispatcher.scoped`;
    lỗi lúc COMMIT (vd Postgres `Failing row contains (…)`) thoát ra ngoài dispatcher và bị log
    nguyên message + traceback."""
    from types import SimpleNamespace

    from tests.unit.tools.fakes import SETTINGS, make_world
    from xt.entrypoints.composition.runtime import ScopedDispatcher

    def boom(caller: Any, actor: Any, body: Any) -> Any:
        raise RuntimeError(f'DETAIL: Failing row contains (so_cccd {CCCD}, "postgres://u:p@h")')

    rt: Any = SimpleNamespace(resolve=lambda actor, trace: object(), run_as=boom)
    d = ScopedDispatcher(rt, make_world().services, SETTINGS)
    with caplog.at_level("DEBUG"):
        out = d.call("get_record", {"ref": "employee:x"}, agent_ctx("sub-mai"))
    assert out.envelope["error"]["code"] == "internal_error"
    logged = caplog.text + "".join(r.exc_text or "" for r in caplog.records)
    assert CCCD not in logged and "postgres://u:p@h" not in logged
