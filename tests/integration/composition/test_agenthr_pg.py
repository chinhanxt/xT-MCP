# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""AgentHR (D-AGENTHR-1) trên Postgres 17 thật + dispatcher MCP thật (xt_app, RLS, audit trigger):

- tenant `agenthrx`: cài de-xuat-phe-duyet 0.3.0 + nhan-su-vong-doi + nhan-su-ai 0.1.0; trưởng phòng
  tuyển ChatGPT của Dũng qua client Desk, IT duyệt JD bằng decide_request + xác nhận (OTP ở Desk),
  op `apply_agent_jd` ghi `xt_meta.agent_grant`; cổng chặn: chưa duyệt, ngoài JD, vượt giá trị, giữ
  nháp chờ giấy phép, chạm hạn mức ngày -> tự tạm dừng, ngoài giờ, cho nghỉ -> xóa agent_grant;
  mỗi lần chặn có dòng audit `agenthr.block`; hash-chain verify OK.
- tenant `agenthrleg` (không cài nhan-su-ai) và agent không có hồ sơ: hành vi cũ, không đụng bảng
  AgentHR.
- `approval_ticket`: create + submit + decide (human_only, OTP qua trang xác nhận), không `amount`.
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.integration.composition.fixtures import PACK, ROOT, StackBase, run_admin
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT
from xt.adapters.db.integration import connect, person_id, tenant_of_alias
from xt.application.tools import ConfirmationService
from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.domain.identity import PrincipalKind
from xt.domain.model.context import TenantRef
from xt.entrypoints.composition.runtime import Runtime
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration

DESK = "xt-desk-agent"
HCM = ZoneInfo("Asia/Ho_Chi_Minh")
HR_PACK = ROOT / "packages" / "nhan-su-vong-doi"
AI_PACK = ROOT / "packages" / "nhan-su-ai"
DEPTS = [("BGD", "Ban Giám đốc"), ("IT", "Công nghệ"), ("KD", "Kinh doanh")]
PEOPLE = [
    ("NV-0001", "Giám Đốc", "BGD", None, ["director", "admin"]),
    ("NV-0002", "Trưởng Kinh Doanh", "KD", "NV-0001", ["dept_head"]),
    ("NV-0003", "Quản Trị IT", "IT", "NV-0001", ["it_admin"]),
    ("NV-0004", "Dũng", "KD", "NV-0002", []),
    ("NV-0005", "Trâm", "KD", "NV-0002", []),
    ("NV-0006", "Hà", "KD", "NV-0002", []),
]


@dataclass
class Hr:
    rt: Runtime
    dispatcher: Any
    tenant: TenantRef
    alias: str

    def sub(self, key: str) -> str:
        return f"kc-{self.alias}-{key}"

    def pid(self, key: str) -> str:
        return str(person_id(self.tenant.id, key))

    def actor(self, key: str, client: str = AGENT_CLIENT) -> Actor:
        return Actor(self.alias, self.sub(key), PrincipalKind.AGENT, client, self.sub(key))

    def call(self, key: str, tool: str, args: dict[str, Any], client: str = AGENT_CLIENT) -> Any:
        ctx = CallContext(
            self.actor(key, client), ClientClass.CLOUD, f"hr-{uuid.uuid4().hex[:8]}",
            datetime.now(UTC),
        )  # fmt: skip
        out: dict[str, Any] = self.dispatcher.call(tool, args, ctx).envelope
        return out

    def desk(self, key: str, tool: str, args: dict[str, Any]) -> Any:
        return self.call(key, tool, args, DESK)

    def confirm(self, pending: dict[str, Any], by: str, client: str = DESK) -> Any:
        assert pending["error"]["code"] == "confirmation_required", pending
        cid = pending["error"]["details"]["confirmation_id"]
        actor, trace = self.actor(by, client), f"hr-{uuid.uuid4().hex[:8]}"
        svc = ConfirmationService(self.dispatcher, DESK_CLIENT)
        return self.rt.run_as(
            self.rt.resolve(actor, trace),
            actor,
            lambda: svc.confirm(cid, self.sub(by), datetime.now(UTC), trace),
        )

    def sql(self, query: str, *params: Any) -> list[dict[str, Any]]:
        look = Runtime.lookup_actor(self.tenant)
        with self.rt.uow.tenant_tx(look) as cur:
            return [dict(r) for r in cur.execute(query, params).fetchall()]

    def rows(self, model: str, where: dict[str, Any]) -> list[dict[str, Any]]:
        caller = self.rt.resolve(self.actor("NV-0001", DESK), "hr-read")

        def search() -> list[dict[str, Any]]:
            out = self.rt.records.search(caller.workflow(), model, where, ("created_at",), 100, 0)
            return [dict(r) for r in out["items"]]

        return list(self.rt.run_as(caller, self.actor("NV-0001", DESK), search))

    def record(self, ref: str) -> dict[str, Any]:
        model, rid = ref.split(":", 1)
        caller = self.rt.resolve(self.actor("NV-0001", DESK), "hr-read")

        def get() -> dict[str, Any]:
            return dict(self.rt.records.get(caller.workflow(), model, uuid.UUID(rid))["record"])

        return dict(self.rt.run_as(caller, self.actor("NV-0001", DESK), get))


def _key() -> str:
    return uuid.uuid4().hex


def _ok(out: dict[str, Any]) -> dict[str, Any]:
    assert out.get("ok") is True, out
    data: dict[str, Any] = out["data"]
    return data


def _denied(out: dict[str, Any], reason: str) -> dict[str, Any]:
    assert out.get("ok") is False, out
    err: dict[str, Any] = out["error"]
    assert err["details"].get("reason") == reason, err
    return err


def _install(env: dict[str, str], alias: str, packs: list[Any], tmp: Any) -> TenantRef:
    rc, out = run_admin(
        ["tenant", "create", "--alias", alias, "--name", f"AgentHR {alias}",
         "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    org = {
        "departments": [{"id": k, "name": n, "parent_id": None} for k, n in DEPTS],
        "positions": [],
        "employees": [
            {"id": k, "full_name": n, "email": f"{k.lower()}@{alias}.example", "department_id": u,
             "position_id": None, "manager_id": m, "active": True, "roles": r}
            for k, n, u, m, r in PEOPLE
        ],
    }  # fmt: skip
    (tmp / "org.json").write_text(json.dumps(org, ensure_ascii=False), encoding="utf-8")
    # Desk (client agent) cho mọi người; ChatGPT (it-agent) có sẵn (L1, trước AgentHR) cho IT và
    # Dũng; Trâm được cấp khi IT duyệt JD (apply_agent_jd).
    grants = [
        {"person": k, "client_id": DESK, "autonomy": 1, "roles": ["agent"]} for k, *_ in PEOPLE
    ]
    grants += [
        {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
        for k in ("NV-0003", "NV-0004")
    ]
    links = {"subjects": {k: f"kc-{alias}-{k}" for k, *_ in PEOPLE}, "agent_grants": grants}
    (tmp / "links.json").write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", alias, str(tmp / "org.json"), "--links",
         str(tmp / "links.json"), "--base-roles", "employee"],
        env,
    )  # fmt: skip
    assert rc == 0, out
    who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
    for pack in packs:
        common = ["pack", "install", "--tenant", alias, str(pack)]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0 and "KHÔNG CÀI ĐƯỢC" not in plan, plan
        digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, done = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, done
    with connect(env["XT_DB_MIGRATOR_URL"]) as c:
        return tenant_of_alias(c, alias)


@pytest.fixture(scope="module")
def hr(
    stack_base: StackBase, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Hr, Hr]]:
    env = stack_base.env
    sfx = uuid.uuid4().hex[:6]
    t1 = _install(env, f"agenthr{sfx}", [PACK, HR_PACK, AI_PACK], tmp_path_factory.mktemp("hr"))
    t2 = _install(env, f"agentleg{sfx}", [PACK], tmp_path_factory.mktemp("leg"))
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    comp: Any = prepared.services
    try:
        yield (
            Hr(comp.runtime, prepared.dispatcher, t1, f"agenthr{sfx}"),
            Hr(comp.runtime, prepared.dispatcher, t2, f"agentleg{sfx}"),
        )
    finally:
        prepared.close()


def _advance(hr: Hr, key: str, amount: int, client: str = AGENT_CLIENT) -> dict[str, Any]:
    return hr.call(
        key,
        "create_record",
        {
            "model_id": "advance_request",
            "values": {
                "prepared_on": "2026-10-21",
                "amount": amount,
                "trip_start": "2026-10-22",
                "trip_end": "2026-10-23",
                "settlement_due": "2026-10-30",
                "payout_method": "cash",
                "destination": "Đà Nẵng",
            },
            "idempotency_key": _key(),
        },
        client,
    )


def _hire(hr: Hr, owner: str, jd: dict[str, Any]) -> tuple[str, str]:
    """Trưởng phòng tuyển agent cho `owner`, IT duyệt JD (decide_request + xác nhận Desk)."""
    ho = _ok(hr.desk("NV-0002", "create_record", {
        "model_id": "ho_so_agent",
        "values": {"ten": f"ChatGPT của {owner}", "client_id": AGENT_CLIENT,
                   "nguoi_phu_trach": f"person:{hr.pid(owner)}",
                   "bao_cao_cho": f"person:{hr.pid('NV-0002')}"},
        "idempotency_key": _key(),
    }))["record"]  # fmt: skip
    assert ho["values"]["trang_thai"] == "cho_duyet"
    created = _ok(hr.desk("NV-0002", "create_record", {
        "model_id": "jd_agent", "values": {"ho_so": ho["ref"], **jd}, "idempotency_key": _key(),
    }))["record"]  # fmt: skip
    sub = _ok(hr.desk("NV-0002", "submit_request", {
        "ref": created["ref"], "expected_revision": created["revision"], "idempotency_key": _key(),
    }))  # fmt: skip
    assert sub["request"]["state"] == "in_review", sub
    row = hr.record(created["ref"])
    pending = hr.desk("NV-0003", "decide_request", {
        "ref": created["ref"], "expected_revision": row["revision"], "decision": "approve",
        "idempotency_key": _key(),
    })  # fmt: skip
    done = hr.confirm(pending, "NV-0003")
    assert done.status.value == "confirmed", done
    return ho["ref"], created["ref"]


JD_ADVANCE = {
    "nang_luc": [
        {"model": "advance_request", "read": True, "create": True, "update": True, "submit": True}
    ],
    "muc_tu_chu": 1,
    "han_muc_viec_ngay": 3,
    "han_muc_gia_tri": 5_000_000,
    "khung_gio": "00:00-23:59",
    "ngay_lam": "all",
}


def test_agenthr_lifecycle_on_postgres(hr: tuple[Hr, Hr]) -> None:
    h, _ = hr
    # ---- không có hồ sơ: hành vi cũ (IT dùng ChatGPT với grant sẵn có) --------------------------
    _ok(_advance(h, "NV-0003", 1_000_000))
    _ok(_advance(h, "NV-0004", 1_000_000))
    # agent ngoài (ChatGPT) không ghi được gói nhan-su-ai kể cả thay IT: Deny của policy
    out = h.call("NV-0003", "create_record", {
        "model_id": "ho_so_agent", "values": {"ten": "x", "client_id": "claude",
                                              "nguoi_phu_trach": "me"},
        "idempotency_key": _key()})  # fmt: skip
    assert out["error"]["code"] == "access_denied", out

    # ---- tuyển: hồ sơ mới luôn chờ duyệt -------------------------------------------------------
    _denied(h.desk("NV-0002", "create_record", {
        "model_id": "ho_so_agent",
        "values": {"ten": "x", "client_id": AGENT_CLIENT, "trang_thai": "dang_lam",
                   "nguoi_phu_trach": f"person:{h.pid('NV-0004')}"},
        "idempotency_key": _key()}), "hire_requires_jd_approval")  # fmt: skip
    ho = _ok(h.desk("NV-0002", "create_record", {
        "model_id": "ho_so_agent",
        "values": {"ten": "ChatGPT của Dũng", "client_id": AGENT_CLIENT,
                   "nguoi_phu_trach": f"person:{h.pid('NV-0004')}"},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    ho_ref = ho["ref"]
    # chưa duyệt: mọi lượt (kể cả đọc) bị chặn
    _denied(h.call("NV-0004", "search_records", {"model_id": "advance_request"}),
            "agent_suspended")  # fmt: skip
    # JD sai dạng bị từ chối trước khi ghi
    bad = h.desk("NV-0002", "create_record", {"model_id": "jd_agent", "values": {
        "ho_so": ho_ref, "nang_luc": [{"model": "advance_request", "read": "yes"}]},
        "idempotency_key": _key()})  # fmt: skip
    assert bad["error"]["code"] == "invalid_request", bad
    jd = _ok(h.desk("NV-0002", "create_record", {
        "model_id": "jd_agent", "values": {"ho_so": ho_ref, **JD_ADVANCE, "muc_tu_chu": 2},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    # advance_request có trường PII (số tài khoản) và trường money
    assert jd["values"]["canh_bao"] == [
        "pii_reach_expansion", "money_reach_expansion", "outbound_release",
        "capability_expansion", "autonomy_raise",
    ]  # fmt: skip
    sub = _ok(
        h.desk(
            "NV-0002",
            "submit_request",
            {"ref": jd["ref"], "expected_revision": jd["revision"], "idempotency_key": _key()},
        )
    )
    assert sub["request"]["state"] == "in_review"
    grant_sql = (
        "SELECT autonomy, roles FROM xt_meta.agent_grant WHERE person = %s AND client_id = %s"
    )
    dung = uuid.UUID(h.pid("NV-0004"))
    assert h.sql(grant_sql, dung, AGENT_CLIENT) == [{"autonomy": 1, "roles": ["agent"]}]
    # người lập JD (trưởng phòng) không tự duyệt; IT duyệt bằng xác nhận Desk
    row = h.record(jd["ref"])
    selfd = h.desk("NV-0002", "decide_request", {"ref": jd["ref"], "decision": "approve",
                   "expected_revision": row["revision"], "idempotency_key": _key()})  # fmt: skip
    assert selfd["error"]["code"] == "access_denied", selfd
    pending = h.desk("NV-0003", "decide_request", {"ref": jd["ref"], "decision": "approve",
                     "expected_revision": row["revision"], "idempotency_key": _key()})  # fmt: skip
    assert h.confirm(pending, "NV-0003").status.value == "confirmed"
    prof = h.record(ho_ref)
    assert prof["trang_thai"] == "dang_lam" and prof["jd_hieu_luc"] == jd["ref"].split(":")[1]
    # trần tự chủ = mức tự chủ của JD đã duyệt (L1 cũ -> L2)
    assert h.sql(grant_sql, dung, AGENT_CLIENT) == [{"autonomy": 2, "roles": ["agent"]}]

    # ---- làm việc trong JD -------------------------------------------------------------------
    rec = _ok(_advance(h, "NV-0004", 4_000_000))["record"]
    assert int(h.record(ho_ref)["so_viec_hom_nay"]) == 1
    # vượt giá trị: chặn + một sự cố mở (thử lại không nhân bản), bản ghi không được tạo
    for _ in range(2):
        e = _denied(_advance(h, "NV-0004", 20_000_000), "over_value_limit")
        assert e["details"]["limit"] == 5_000_000 and e["remediation"]
    # ngoài JD
    _denied(h.call("NV-0004", "create_record", {"model_id": "approval_ticket", "values": {
        "title": "t", "content": "c"}, "idempotency_key": _key()}),
        "outside_job_description")  # fmt: skip
    # nộp duyệt chưa có giấy phép: giữ nháp
    _denied(h.call("NV-0004", "submit_request", {"ref": rec["ref"],
            "expected_revision": rec["revision"], "idempotency_key": _key()}),
            "held_for_release")  # fmt: skip
    assert h.record(rec["ref"])["state"] == "draft"
    _ok(h.desk("NV-0002", "create_record", {"model_id": "giay_phep_hanh_dong", "values": {
        "ho_so": ho_ref, "hanh_dong": "request.submit:advance_request",
        "dieu_kien": "Phiếu tạm ứng tới 5 triệu"}, "idempotency_key": _key()}))  # fmt: skip
    sub = _ok(h.call("NV-0004", "submit_request", {"ref": rec["ref"],
              "expected_revision": rec["revision"], "idempotency_key": _key()}))  # fmt: skip
    assert sub["request"]["state"] == "in_review"
    incidents = h.rows("su_co_agent", {"field": "ho_so", "op": "=", "value": ho_ref.split(":")[1]})
    assert sorted(r["loai"] for r in incidents) == ["giu_nhap", "vuot_gia_tri"]
    assert all(r["trang_thai"] == "mo" for r in incidents)

    # ---- hạn mức ngày: 3 việc -> lần thứ 4 bị chặn, tự tạm dừng ---------------------------------
    _ok(_advance(h, "NV-0004", 1_000_000))
    e = _denied(_advance(h, "NV-0004", 1_000_000), "daily_limit_reached")
    assert e["details"]["limit"] == 3
    prof = h.record(ho_ref)
    assert (prof["trang_thai"], prof["ly_do_tam_dung"]) == ("tam_dung", "han_muc")
    assert int(prof["so_viec_hom_nay"]) == 3
    today = datetime.now(HCM).date()
    usage = h.sql("SELECT count FROM xt_meta.agent_usage WHERE person = %s AND day = %s",
                  uuid.UUID(h.pid("NV-0004")), today)  # fmt: skip
    assert usage == [{"count": 3}]
    _denied(h.call("NV-0004", "get_inbox", {}), "agent_suspended")
    # mở lại chỉ qua JD được duyệt
    prof = h.record(ho_ref)
    _denied(h.desk("NV-0002", "update_record", {"ref": ho_ref, "set": {"trang_thai": "dang_lam"},
            "expected_revision": prof["revision"], "idempotency_key": _key()}),
            "reopen_requires_jd_approval")  # fmt: skip
    _denied(h.desk("NV-0002", "update_record", {"ref": ho_ref, "set": {"client_id": "claude"},
            "expected_revision": prof["revision"], "idempotency_key": _key()}),
            "immutable_field")  # fmt: skip

    # ---- cho nghỉ: xóa agent_grant, lượt kế bị chặn, không mở lại --------------------------------
    upd = _ok(h.desk("NV-0002", "update_record", {"ref": ho_ref, "set": {"trang_thai": "da_nghi"},
              "expected_revision": prof["revision"], "idempotency_key": _key()}))  # fmt: skip
    assert h.sql("SELECT 1 FROM xt_meta.agent_grant WHERE person = %s AND client_id = %s",
                 uuid.UUID(h.pid("NV-0004")), AGENT_CLIENT) == []  # fmt: skip
    # trần tự chủ + vai trò agent đã thu hồi: IAM chặn ngay từ lớp đầu (trước cả cổng AgentHR)
    _denied(h.call("NV-0004", "search_records", {"model_id": "advance_request"}),
            "implicit_deny")  # fmt: skip
    _denied(h.desk("NV-0002", "update_record", {"ref": ho_ref, "set": {"trang_thai": "tam_dung",
            "ly_do_tam_dung": "thu_cong"}, "expected_revision": upd["record"]["revision"],
            "idempotency_key": _key()}), "agent_terminated")  # fmt: skip

    # ---- audit: mỗi lần chặn một dòng agenthr.block trong hash-chain, chuỗi nguyên vẹn -----------
    events = h.sql("SELECT reason, tool FROM xt_meta.agenthr_event WHERE ho_so = %s ORDER BY at",
                   uuid.UUID(ho_ref.split(":")[1]))  # fmt: skip
    reasons = [x["reason"] for x in events]
    assert reasons == ["agent_suspended", "over_value_limit", "over_value_limit",
                       "outside_job_description", "held_for_release", "daily_limit_reached",
                       "agent_suspended"]  # fmt: skip
    look = Runtime.lookup_actor(h.tenant)
    audit = [a for a in h.rt.audit.events(look, limit=5000)
             if a["model"] == "xt_meta.agenthr_event"
             and a["diff"]["new"]["ho_so"] == ho_ref.split(":")[1]]  # fmt: skip
    assert [a["diff"]["new"]["reason"] for a in audit] == reasons
    assert all(a["op"] == "INSERT" and a["diff"]["new"]["op"] == "agenthr.block" for a in audit)
    assert all(a["client_azp"] == AGENT_CLIENT for a in audit)
    incidents_after = h.rows("su_co_agent", {"field": "ho_so", "op": "=",
                                              "value": ho_ref.split(":")[1]})  # fmt: skip
    assert sorted(r["loai"] for r in incidents_after) == ["giu_nhap", "vuot_gia_tri",
                                                          "vuot_han_muc"]  # fmt: skip
    assert h.rt.audit.verify(look).intact


def test_outside_working_hours_in_tenant_time(hr: tuple[Hr, Hr]) -> None:
    h, _ = hr
    now = datetime.now(HCM)
    window = "01:00-01:30" if now.hour >= 12 else "22:00-22:30"
    _hire(h, "NV-0005", {**JD_ADVANCE, "khung_gio": window})
    e = _denied(h.call("NV-0005", "search_records", {"model_id": "advance_request"}),
                "outside_working_hours")  # fmt: skip
    assert e["details"]["khung_gio"] == window


def test_approver_cannot_be_the_agent_owner(hr: tuple[Hr, Hr]) -> None:
    h, _ = hr
    ho = _ok(h.desk("NV-0002", "create_record", {
        "model_id": "ho_so_agent",
        "values": {"ten": "ChatGPT của IT", "client_id": "claude",
                   "nguoi_phu_trach": f"person:{h.pid('NV-0003')}"},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    jd = _ok(h.desk("NV-0002", "create_record", {
        "model_id": "jd_agent", "values": {"ho_so": ho["ref"], **JD_ADVANCE},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    _ok(
        h.desk(
            "NV-0002",
            "submit_request",
            {"ref": jd["ref"], "expected_revision": jd["revision"], "idempotency_key": _key()},
        )
    )
    row = h.record(jd["ref"])
    pending = h.desk("NV-0003", "decide_request", {"ref": jd["ref"], "decision": "approve",
                     "expected_revision": row["revision"], "idempotency_key": _key()})  # fmt: skip
    h.confirm(pending, "NV-0003")
    assert h.record(ho["ref"])["trang_thai"] == "cho_duyet"  # op SoD lỗi: không áp
    assert h.sql("SELECT 1 FROM xt_meta.agent_grant WHERE person = %s AND client_id = 'claude'",
                 uuid.UUID(h.pid("NV-0003"))) == []  # fmt: skip


def test_tenant_without_pack_is_unaffected(hr: tuple[Hr, Hr]) -> None:
    _, leg = hr
    # IT có grant ChatGPT L1 ở tenant không cài nhan-su-ai: tạo phiếu như trước
    _ok(_advance(leg, "NV-0003", 20_000_000))
    _ok(leg.call("NV-0003", "search_records", {"model_id": "advance_request"}))
    assert leg.sql("SELECT count(*) AS n FROM xt_meta.agenthr_event") == [{"n": 0}]
    assert leg.sql("SELECT count(*) AS n FROM xt_meta.agent_usage") == [{"n": 0}]


def test_approval_ticket_without_amount_full_flow(hr: tuple[Hr, Hr]) -> None:
    _, leg = hr
    created = _ok(leg.desk("NV-0004", "create_record", {
        "model_id": "approval_ticket",
        "values": {"title": "Duyệt kết quả workflow", "content": "Báo cáo tuần đã soạn"},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    sub = _ok(leg.desk("NV-0004", "submit_request", {
        "ref": created["ref"], "expected_revision": created["revision"],
        "idempotency_key": _key()}))  # fmt: skip
    assert sub["request"]["state"] == "in_review"
    row = leg.record(created["ref"])
    # người lập không tự duyệt
    own = leg.desk("NV-0004", "decide_request", {"ref": created["ref"], "decision": "approve",
                   "expected_revision": row["revision"], "idempotency_key": _key()})  # fmt: skip
    assert own["error"]["code"] == "access_denied", own
    pending = leg.desk(
        "NV-0002",
        "decide_request",
        {
            "ref": created["ref"],
            "decision": "approve",
            "expected_revision": row["revision"],
            "idempotency_key": _key(),
        },
    )
    assert pending["error"]["code"] == "confirmation_required", pending  # human_only
    assert leg.confirm(pending, "NV-0002").status.value == "confirmed"
    assert leg.record(created["ref"])["state"] == "approved"


def test_jd_copies_capabilities_from_role_and_portal_client_is_not_an_agent(
    hr: tuple[Hr, Hr],
) -> None:
    h, _ = hr
    role = _ok(h.desk("NV-0003", "create_record", {"model_id": "vai_tro_agent", "values": {
        "ma": f"thu_ky_{uuid.uuid4().hex[:6]}", "ten": "Thư ký (chỉ đọc)",
        "nang_luc": [{"model": "advance_request", "read": True, "create": False,
                      "update": False, "submit": False}],
        "han_muc_viec_ngay": 7, "khung_gio": "07:00-20:00", "ngay_lam": "all", "muc_tu_chu": 0,
    }, "idempotency_key": _key()}))["record"]  # fmt: skip
    ho = _ok(
        h.desk(
            "NV-0002",
            "create_record",
            {
                "model_id": "ho_so_agent",
                "values": {
                    "ten": "Thư ký của Hà",
                    "client_id": AGENT_CLIENT,
                    "vai_tro": role["ref"],
                    "nguoi_phu_trach": f"person:{h.pid('NV-0006')}",
                },
                "idempotency_key": _key(),
            },
        )
    )["record"]
    jd = _ok(h.desk("NV-0002", "create_record", {"model_id": "jd_agent", "values": {
        "ho_so": ho["ref"]}, "idempotency_key": _key()}))["record"]  # fmt: skip
    v = jd["values"]
    assert v["nang_luc"] == role["values"]["nang_luc"]
    # kiểu number ra dạng chuỗi số (như mọi trường number của xT)
    assert (v["han_muc_viec_ngay"], v["khung_gio"], v["ngay_lam"]) == ("7", "07:00-20:00", "all")
    assert v["canh_bao"] == ["pii_reach_expansion", "capability_expansion"]
    # hồ sơ không có vai trò và JD không có nang_luc: từ chối
    bare = _ok(h.desk("NV-0002", "create_record", {"model_id": "ho_so_agent", "values": {
        "ten": "x", "client_id": "claude", "nguoi_phu_trach": f"person:{h.pid('NV-0006')}"},
        "idempotency_key": _key()}))["record"]  # fmt: skip
    out = h.desk("NV-0002", "create_record", {"model_id": "jd_agent", "values": {
        "ho_so": bare["ref"]}, "idempotency_key": _key()})  # fmt: skip
    assert out["error"]["code"] == "invalid_request", out
    # client cổng Desk không tuyển làm agent được, và lời gọi của Desk không bị cổng đếm
    _denied(h.desk("NV-0002", "create_record", {"model_id": "ho_so_agent", "values": {
        "ten": "x", "client_id": DESK, "nguoi_phu_trach": f"person:{h.pid('NV-0002')}"},
        "idempotency_key": _key()}), "portal_client")  # fmt: skip
    assert h.sql("SELECT count(*) AS n FROM xt_meta.agent_usage WHERE client_id = %s",
                 DESK) == [{"n": 0}]  # fmt: skip
