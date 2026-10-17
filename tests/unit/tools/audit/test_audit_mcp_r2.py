# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit lớp MCP vòng 2 (docs/audit-mcp.md, mục "Vòng 2"). Đã sửa; giữ làm hồi quy."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from tests.unit.tools.fakes import agent_ctx, later, make_dispatcher, make_world
from xt.adapters.mcp.strict_body import BodyRejection, inspect_body
from xt.application.tools import ConfirmationService
from xt.application.tools.context import ClientClass
from xt.application.tools.ports import PendingStatus
from xt.contracts.tools import BY_NAME, input_issues

CCCD = "079123456789"


class _VaultStub:
    def pii_put(self, actor: Any, token: str, value: str) -> None:
        pass

    def pii_egress(self, actor: Any, **kw: Any) -> None:
        pass


def _real_lane() -> Any:
    """Làn PII thật của composition; Runtime giả chỉ cấp profile tenant và vault rỗng. Import
    muộn: lỗi import của composition hiện là FAILED (raises=AssertionError), không bị nuốt."""
    from xt.entrypoints.composition.pii import PostgresPiiLane

    caller = SimpleNamespace(
        tenant=SimpleNamespace(id="t1"),
        profile=SimpleNamespace(pii_epoch=1, llm_egress="masked"),
        actor="a",
    )
    rt: Any = SimpleNamespace(caller=lambda ctx: caller, store=_VaultStub())
    return PostgresPiiLane(rt, b"k" * 32)


def _dup_body(n: int) -> bytes:
    pairs = ",".join(f'"k{i}":0,"k{i}":0' for i in range(n))
    return (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_record",'
        '"arguments":{' + pairs + "}}}"
    ).encode()


# ---- R2-H1: quét khóa trùng O(n²) chạy trên event loop -----------------------------------------


def test_duplicate_key_scan_is_linear() -> None:
    body = _dup_body(15_000)  # ~0,3 MB; body 4 MiB chứa ~190k khóa trùng
    t0 = time.perf_counter()
    inspect_body(body)
    assert time.perf_counter() - t0 < 0.3


# ---- R2-L1: lone surrogate + khóa trùng -> UnicodeEncodeError (HTTP 500 + traceback) ----------


def test_duplicate_keys_with_lone_surrogate_do_not_raise() -> None:
    body = (
        b'{"jsonrpc":"2.0","id":"\\udfff","method":"tools/call","params":{"name":"get_record",'
        b'"arguments":{"ref":"a:b","ref":"a:c"}}}'
    )
    out = inspect_body(body)
    assert out is None or isinstance(out, bytes | BodyRejection)


# ---- R2-M1: lưới lỗi của làn PII thật bỏ sót dạng echo thường gặp ----------------------------


@pytest.mark.parametrize(
    "details",
    [
        {"message": f"so_cccd:{CCCD} đã tồn tại"},
        {"message": f"id-{CCCD}"},
        {"message": f"so_cccd_{CCCD}"},
        {"message": "CCCD 079-123-456-789"},
        {"by_value": {CCCD: "duplicate"}},
        {"value": 912345678},
    ],
)
def test_real_lane_scrubs_common_echo_forms(details: dict[str, Any]) -> None:
    ctx = agent_ctx("sub-mai", client_class=ClientClass.CLOUD)
    error = {"code": "rule_violation", "message": "x", "details": details}
    out = repr(_real_lane().scrub_error(ctx, "update_record", error))
    for raw in (CCCD, "079-123-456-789", "912345678"):
        assert raw not in out


# ---- R2-M2: lưới lỗi làm hỏng continuation và che số tiền ------------------------------------


def test_continuation_stays_replayable_for_cloud_client() -> None:
    w = make_world()
    w.services.pii.scrub_error = _real_lane().scrub_error  # type: ignore[method-assign]
    d = make_dispatcher(w)
    args = {
        "model_id": "employee",
        "values": {"full_name": "Lê Văn Phúc, SĐT 0912345678"},
        "idempotency_key": "emp-r2-audit-1",
    }
    first = d.call("create_record", args, agent_ctx("sub-mai")).envelope["error"]
    assert first["code"] == "confirmation_required"
    cont = first["continuation"]
    again = d.call(cont["tool"], cont["arguments"], agent_ctx("sub-mai")).envelope
    assert again["ok"] or again["error"]["details"].get("reason") != "masked_value"


def test_confirmation_id_survives_real_pii_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    w = make_world()
    w.services.pii.scrub_error = _real_lane().scrub_error  # type: ignore[method-assign]
    confirmation_id = "cf_0912345678abcdefabcdefabcdefabcd"
    new_id = w.services.ids.new_id
    monkeypatch.setattr(
        w.services.ids,
        "new_id",
        lambda prefix: confirmation_id if prefix == "cf" else new_id(prefix),
    )
    d = make_dispatcher(w)
    args = {
        "model_id": "employee",
        "values": {"full_name": "Lê Văn Phúc, SĐT 0912345678"},
        "idempotency_key": "emp-issue-73",
    }
    first = d.call("create_record", args, agent_ctx("sub-mai")).envelope["error"]
    assert first["code"] == "confirmation_required"
    details = first["details"]
    assert details["confirmation_id"] == confirmation_id
    assert details["confirm_url"] == f"https://xt.example.test/desk/confirm/{confirmation_id}"
    confirmed = ConfirmationService(d).confirm(
        details["confirmation_id"], "sub-mai", later(1), "tr_issue_73"
    )
    assert confirmed.status is PendingStatus.CONFIRMED
    replay = d.call("create_record", args, agent_ctx("sub-mai", now=later(2))).envelope
    assert replay["ok"] and replay["meta"]["receipt"]["replayed"]


def test_scrub_keeps_money_amounts() -> None:
    ctx = agent_ctx("sub-mai", client_class=ClientClass.CLOUD)
    msg = "Số tiền 150000000 vượt hạn mức 100000000 mỗi lần"
    out = _real_lane().scrub_error(ctx, "create_record", {"message": msg, "details": {}})
    assert out["message"] == msg


# ---- R2-L2: `\d` của Python khớp chữ số Unicode (ECMA-262 chỉ ASCII) -------------------------


@pytest.mark.parametrize(
    "due",
    [
        "\uff12\uff10\uff12\uff16-\uff11\uff10-\uff11\uff16",
        "\u0662\u0660\u0662\u0666-\u0661\u0660-\u0661\u0666T09:00Z",
    ],
)
def test_date_pattern_is_ascii_only(due: str) -> None:
    args = {"title": "x", "assignee": "me", "due_at": due, "idempotency_key": "task-r2-0001"}
    assert input_issues(BY_NAME["create_task"], args) != []
