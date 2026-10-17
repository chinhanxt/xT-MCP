# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Safe op failures survive engine wrapping while failed steps remain retryable."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from tests.unit.process.support import ENGINE, Services
from tests.unit.process.test_service_and_hardening import _service
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.entrypoints.composition.pack_ops import OpInputError
from xt.entrypoints.composition.process import ProcessToolAdapter, StepPlan


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("role_not_allowed", "Vai trò không nằm trong danh sách được phép cấp."),
        (
            "self_approval",
            "Người duyệt phải khác người đề nghị, người được cấp quyền và người tạo bản ghi cấp"
            " quyền.",
        ),
        ("person_not_found", "Không thể xác minh người tham gia cấp quyền trong tổ chức."),
        ("unknown_reason", None),
        (None, None),
        ("internal", None),
    ],
)
def test_commit_step_safe_error_and_retry(
    monkeypatch: pytest.MonkeyPatch, reason: str | None, message: str | None
) -> None:
    services = Services()
    original = services["post_to_ledger"]

    def fail(amount: int, requester: str) -> None:
        if reason == "internal":
            raise RuntimeError("role_not_allowed: secret database details")
        raise OpInputError("secret database details", reason=reason)

    services["post_to_ledger"] = fail
    svc, store = _service(services)
    started = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    iid, tid = started["instance"], started["tasks"][0]["task_id"]
    records = Mock()
    records.get.return_value = {"record": {"revision": 4, "state": "open"}}
    rt: Any = SimpleNamespace(records=records)
    adapter = ProcessToolAdapter(rt, cursor_key=b"test", engine=ENGINE)
    monkeypatch.setattr(adapter, "pc", lambda caller: SimpleNamespace(service=lambda: svc))
    incident = Mock()
    monkeypatch.setattr(adapter, "_incident", incident)
    caller: Any = SimpleNamespace(workflow=lambda: None)
    ctx: Any = None
    plan = StepPlan(
        "actor", "lifecycle_task:t1", "lifecycle_task", uuid4(), 4, iid, tid,
        "it", "IT", "approve", "done", "state", {}, None, "NV-0005",
    )  # fmt: skip

    with pytest.raises(ToolError) as caught:
        adapter._commit_step(ctx, caller, plan)

    err = caught.value
    if message is not None:
        assert err.code == ErrorCode.PRECONDITION_FAILED
        assert err.message == message
        assert err.details == {"reason": reason, "message": message}
    else:
        assert err.code == ErrorCode.INTERNAL_ERROR
        assert err.message == "Quy trình gặp sự cố; quản trị đã được ghi nhận."
        assert err.details == {}
    assert "secret" not in str(err.to_error())
    incident.assert_called_once()
    records.workflow_write.assert_not_called()
    failed = svc.get(iid)
    assert failed["tasks"][0]["task_id"] == tid
    assert failed["status"] == "running" and failed["history"] == []
    assert store.records[iid].state["meta"]["incidents"]

    services["post_to_ledger"] = original
    assert svc.act(iid, tid, "NV-0005", "approve")["status"] == "completed"
    assert services.ledger == [("NV-0006", 1)]
