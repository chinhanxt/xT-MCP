# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E6: agent đề xuất thêm field `ma_du_an` -> plan tiếng Việt [AN TOÀN] -> agent không tự apply ->
người duyệt qua /desk/confirm (TOTP) -> worker pha 2 -> cột có trong information_schema -> tạo bản ghi
với field mới. Xóa field có dữ liệu mà chỉ 1 người duyệt -> không được áp."""

from __future__ import annotations

import time
from typing import Any

import pytest
from xte2e import compose
from xte2e.flows import (
    ACCOUNTANT,
    DEPT_HEAD,
    DEPT_HEAD_ACCT,
    DIRECTOR,
    REQUESTER,
    A,
    advance,
    dumps,
    err,
    key,
)
from xte2e.report import Checks, Evidence

SCENARIO = "E6"
pytestmark = pytest.mark.e2e
FIELD = "ma_du_an"
RATIONALE = "Kế toán cần gắn mỗi tạm ứng với mã dự án để đối chiếu chi phí theo dự án."


def _ref(data: dict[str, Any]) -> str | None:
    for k in ("request", "proposal"):
        v = data.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and v.get("ref"):
            return str(v["ref"])
    return data.get("ref")


def _column_exists(schema: str) -> bool:
    return (
        compose.psql(
            f"SELECT count(*) FROM information_schema.columns WHERE table_schema = '{schema}'"
            f" AND table_name = 'advance_request' AND column_name = '{FIELD}'"
        )
        == "1"
    )


def _find_approver(world, ref: str, skip: set[str]) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    for k in (DIRECTOR, DEPT_HEAD_ACCT, DEPT_HEAD, ACCOUNTANT):
        if k in skip:
            continue
        u = world.by_key(A, k)
        inbox = world.call(u, "get_inbox", {})
        if any(i.get("ref") == ref for i in (inbox.get("data") or {}).get("items", [])):
            return u
    return None


def _decide(world, ev: Evidence, u: dict[str, Any], ref: str) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    got = world.call(u, "get_record", {"ref": ref})
    rev = ((got.get("data") or {}).get("record") or {}).get("revision")
    args = {
        "ref": ref,
        "decision": "approve",
        "idempotency_key": key("mc-dec"),
        **({"expected_revision": rev} if rev is not None else {}),
    }
    p = world.call(u, "decide_request", args)
    ev.step(f"decide_request ({u['username']}, agent)", p)
    url = (err(p).get("details") or {}).get("confirm_url")
    if not url:
        return p
    ev.step("xác nhận /desk/confirm", world.confirm(url, u))
    return world.call(u, "decide_request", args)


def test_add_field_apply(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    global FIELD
    c = Checks(ev)
    schema = world.tenants[A]["schema"]
    if _column_exists(schema):  # chạy lại trên stack giữ lại (E2E_KEEP=1): field mới cho lần này
        FIELD = f"ma_du_an_{key('f')[-8:]}"
    proposer = world.by_key(A, DEPT_HEAD)
    ops = [
        {
            "op": "add_field",
            "field": {"id": FIELD, "label": "Mã dự án", "type": "string", "required": False},
        }
    ]
    dry = world.call(
        proposer,
        "propose_model_change",
        {
            "model_id": "advance_request",
            "operations": ops,
            "dry_run": True,
            "rationale": RATIONALE,
            "idempotency_key": key("mc-dry"),
        },
    )
    ev.step("propose_model_change dry_run", dry)
    c.check(dry.get("ok") is True, f"dry-run ok ({err(dry).get('code')})")
    c.check("AN TOÀN" in dumps(dry) or "safe" in dumps(dry).lower(), "plan phân loại an toàn")
    filed = world.call(
        proposer,
        "propose_model_change",
        {
            "model_id": "advance_request",
            "operations": ops,
            "rationale": RATIONALE,
            "dry_run": False,
            "idempotency_key": key("mc"),
        },
    )
    ev.step("propose_model_change dry_run=false", filed)
    assert filed.get("ok"), f"gửi đề xuất: {dumps(filed)[:500]}"
    data = filed.get("data") or {}
    ref = _ref(data)
    c.check(
        bool(ref) and str(ref).startswith("model_proposal:"), f"đề xuất là Request duyệt ({ref})"
    )
    c.check(not _column_exists(schema), "chưa có cột trước khi người duyệt")
    if not ref:
        c.done()
    own = world.call(
        proposer,
        "decide_request",
        {
            "ref": ref,
            "decision": "approve",
            "expected_revision": 1,
            "idempotency_key": key("mc-self"),
        },
    )
    ev.step("người đề xuất tự duyệt", own)
    c.check(
        own.get("ok") is False
        and err(own).get("code") in ("access_denied", "confirmation_required"),
        f"người đề xuất không tự áp được ({err(own).get('code')})",
    )
    approver = _find_approver(world, ref, {DEPT_HEAD})
    ev.step("người duyệt thấy đề xuất trong inbox", approver["username"] if approver else None)
    # Policy mdl.approve: giám đốc được request.decide trên model_proposal:* (dù inbox có liệt kê hay không).
    approver = approver or world.by_key(A, DIRECTOR)
    done = _decide(world, ev, approver, ref)
    ev.step("sau khi người duyệt xác nhận", done)
    c.check(done.get("ok") is True, f"duyệt đề xuất ok ({err(done).get('code')})")
    state = ""
    for _ in range(
        80
    ):  # worker: relay (chu kỳ XT_WORKFLOW_RELAY_SECONDS) -> model.apply (pha 1 + 2)
        st = world.call(approver, "get_record", {"ref": ref})
        state = str(
            ((st.get("data") or {}).get("record") or {}).get("state")
            or (st.get("data") or {}).get("state")
        )
        if state in ("applied", "failed") and _column_exists(schema):
            break
        time.sleep(3)
    ev.step("trạng thái đề xuất sau worker", state)
    relay = compose.psql(
        "SELECT j.status, left(coalesce(r.last_error, ''), 200) FROM xt_queue.requests r"
        " JOIN procrastinate_jobs j ON j.id = r.job_id WHERE r.idempotency_key LIKE 'workflow.relay:%'"
        f" AND r.tenant_id = '{world.tenants[A]['id']}' ORDER BY j.id DESC LIMIT 1"
    )
    ev.step("chuỗi relay (outbox model_change_request -> job model.apply)", relay)
    c.check(state == "applied", f"worker áp đề xuất (state={state}; relay: {relay!r})")
    c.check(_column_exists(schema), f"information_schema có cột {schema}.advance_request.{FIELD}")
    rec = world.call(
        world.by_key(A, REQUESTER),
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(1_500_000, **{FIELD: "DA-2026-01"}),
            "idempotency_key": key("mc-rec"),
        },
    )
    ev.step("tạo bản ghi với field mới", rec)
    c.check(
        rec.get("ok") is True
        and (rec.get("data") or {}).get("record", {}).get("values", {}).get(FIELD) == "DA-2026-01",
        "bản ghi mới có ma_du_an",
    )
    c.done()


def test_remove_field_needs_two_approvers(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    c = Checks(ev)
    schema = world.tenants[A]["schema"]
    if not _column_exists(schema):
        ev.gap(
            "W2 áp đề xuất model (decide -> approve_and_apply -> pha 2)",
            f"cột {FIELD} chưa được áp ở test trước",
        )
    proposer = world.by_key(A, DEPT_HEAD)
    ops = [{"op": "remove_field", "field_id": FIELD}]
    dry = world.call(
        proposer,
        "propose_model_change",
        {
            "model_id": "advance_request",
            "operations": ops,
            "dry_run": True,
            "rationale": RATIONALE,
            "idempotency_key": key("mc-dry"),
        },
    )
    ev.step("dry-run xóa field có dữ liệu", dry)
    c.check(
        "PHÁ HỦY" in dumps(dry) or "destructive" in dumps(dry).lower(), "plan phân loại PHÁ HỦY"
    )
    filed = world.call(
        proposer,
        "propose_model_change",
        {
            "model_id": "advance_request",
            "operations": ops,
            "rationale": RATIONALE,
            "dry_run": False,
            "idempotency_key": key("mc-rm"),
        },
    )
    ev.step("gửi đề xuất xóa", filed)
    data = filed.get("data") or {}
    ref = _ref(data)
    first = (_find_approver(world, ref, {DEPT_HEAD}) or world.by_key(A, DIRECTOR)) if ref else None
    if first:
        one = _decide(world, ev, first, ref)
        ev.step("một người duyệt", one)
    st = world.call(first, "get_record", {"ref": ref}) if ref and first else {}
    state = str(((st.get("data") or {}).get("record") or {}).get("state"))
    time.sleep(5)
    c.check(_column_exists(schema), "chỉ 1 người duyệt -> cột vẫn còn (không áp PHÁ HỦY)")
    c.check(
        state in ("pending_approval", "awaiting_second_approver"),
        f"đề xuất chờ người duyệt thứ hai ({state})",
    )
    c.done()
