# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
import json, sys, uuid, time, hashlib, copy, dataclasses
from datetime import datetime as _RealDT, timedelta
import _paths
import psycopg
from psycopg_pool import ConnectionPool
import SpiffWorkflow.bpmn.specs.event_definitions.timer as TM
from SpiffWorkflow.util.task import TaskState
from compiler import compile_spec, SpecError
from xtflow import safe_engine, new_instance, complete, ready_user_tasks, to_state, from_state, UnsafeExpression, load_specs
from seedlib import *
import run_iam as IAM          # re-runs IAM cases (output suppressed below)
from xtmodel.crud import Engine

# ---------- fake clock for SLA timers (F-06: "leo thang test được bằng đồng hồ giả")
class FakeDT(_RealDT):
    offset = timedelta(0)
    @classmethod
    def now(cls, tz=None): return _RealDT.now(tz) + cls.offset
TM.datetime = FakeDT
def advance(h): FakeDT.offset += timedelta(hours=h)
def reset_clock(): FakeDT.offset = timedelta(0)

ALLOWED_OPS = {"post_to_ledger": ["amount", "requester"], "notify": ["msg"], "check_overdue_advances": ["requester"], "close_task": ["task_id"],
 "weekly_rollup": ["from_date", "to_date", "department"], "create_lifecycle_tasks": ["case_id", "kind"], "activate_employee": ["employee_id"],
 "check_unreturned_assets": ["employee_id"], "close_lifecycle_case": ["case_id"]}
PACKS = ["de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi"]
SPECS = {}
for p in PACKS:
    for f in sorted((ROOT / p / "processes").glob("*.json")):
        s = json.loads(f.read_text()); SPECS[s["id"]] = (p, s)
XML = {pid: compile_spec(s, ALLOWED_OPS) for pid, (p, s) in SPECS.items()}

# ---------- DB-backed services (real model tables via Engine, as xt_workflow)
pool = ConnectionPool("host=localhost port=15440 dbname=xt user=xt_app password=app", min_size=1, max_size=3, open=True)
cat = load_catalog(PACKS, 3); eng = Engine(pool, cat)
WF = Actor_ = None
from xtmodel.crud import Actor
wf_actor = Actor(tenant_id(), user_id("xt-workflow"), ("xt_workflow",))
it_actor = Actor(tenant_id(), user_id("NV-0013"), ("it_admin", "employee"), department_ids=(dept_id("IT"),))
su = psycopg.connect("host=localhost port=15440 dbname=xt user=xt_owner password=owner", autocommit=True)
LEDGER, NOTES, OVERDUE = [], [], {}
def svc_post(amount, requester): LEDGER.append((requester, amount)); return {"so": f"PC-{len(LEDGER):04d}"}
def svc_notify(msg): NOTES.append(msg); return {"ok": 1}
def svc_overdue(requester): return {"overdue": OVERDUE.get(requester, 0)}
def svc_close_task(task_id): return {"closed": task_id}
def sq(q, *a): return su.execute(q, a).fetchall()
def svc_rollup(from_date, to_date, department):
    rows = sq("select task_no, state, deadline < %s::date + 1 as qua, created_at::date between %s::date and %s::date from xt_data.task", to_date, from_date, to_date)
    moi = [r[0] for r in rows if r[3]]; xong = [r[0] for r in rows if r[1] == "done"]
    qua = [r[0] for r in rows if r[2] and r[1] not in ("done", "cancelled", "not_accepted")]; ket = [r[0] for r in rows if r[1] == "blocked"]
    ma = f"BT-{to_date[:4]}-W{_RealDT.fromisoformat(to_date).isocalendar()[1]:02d}"
    out = eng.create_record(wf_actor, "weekly_digest", {"code": ma, "from_date": from_date, "to_date": to_date, "total_assigned": len(moi), "total_completed": len(xong),
        "total_overdue": len(qua), "total_blocked": len(ket), "content": f"Tuần {ma[-3:]}: giao mới {len(moi)}, hoàn thành {len(xong)}, quá hạn {len(qua)}, bị chặn {len(ket)}",
        "detail": {"assigned": moi, "done": xong, "overdue": qua, "blocked": ket}})
    if not out["ok"]: raise RuntimeError(json.dumps(out, ensure_ascii=False))
    return {"code": ma, "summary": out["record"]["content"], "id": str(out["record"]["id"])}
def svc_create_tasks(case_id, kind):
    spec = {"onboarding": [("hr","Hồ sơ, HĐLĐ, BHXH"),("it","Email, tài khoản SSO, thiết bị"),("accounting","Mã lương, tài khoản nhận lương"),("manager","Buddy và kế hoạch 30-60-90")],
            "offboarding": [("hr","Xác nhận ngày nghỉ, đơn thôi việc"),("manager","Bàn giao công việc, khách hàng, tài liệu"),("it","Thu hồi quyền và thiết bị"),("accounting","Quyết toán lương, phép, tạm ứng"),("hr","Ẩn danh dữ liệu, đóng hồ sơ")]}[kind]
    for b, t in spec:
        o = eng.create_record(wf_actor, "lifecycle_task", {"lifecycle_case": case_id, "owner_unit": b, "name": t}); assert o["ok"], o
    return {"task_count": len(spec)}
def svc_activate(employee_id):
    r = sq("select revision from xt_data.employee where id=%s", employee_id)[0][0]
    o = eng.update_record(wf_actor, "employee", employee_id, r, {"state": "probation"}); assert o["ok"], o
    return {"state": "probation"}
def svc_check_assets(employee_id):
    ts = sq("select count(*) from xt_data.asset_issue where employee=%s and revoked_on is null", employee_id)[0][0]
    cq = sq("select count(*) from xt_data.access_grant where employee=%s and revoked_on is null", employee_id)[0][0]
    return {"unreturned": int(ts), "active_grants": int(cq)}
def svc_close_case(case_id):
    nv = sq("select employee, revision from xt_data.lifecycle_case where id=%s", case_id)[0]
    left = svc_check_assets(str(nv[0]))
    if left["unreturned"] or left["active_grants"]:
        raise RuntimeError(f"không đóng được hồ sơ: còn {left['unreturned']} tài sản và {left['active_grants']} quyền chưa thu hồi")
    o = eng.update_record(wf_actor, "lifecycle_case", case_id, nv[1], {"state": "completed", "closed_at": "2026-10-15T17:00:00+07:00"}); assert o["ok"], o
    r = sq("select revision from xt_data.employee where id=%s", str(nv[0]))[0][0]
    o = eng.update_record(wf_actor, "employee", str(nv[0]), r, {"state": "resigned", "left_on": "2026-10-31"}); assert o["ok"], o
    return {"closed": case_id}
SERVICES = {"post_to_ledger": svc_post, "notify": svc_notify, "check_overdue_advances": svc_overdue, "close_task": svc_close_task, "weekly_rollup": svc_rollup,
            "create_lifecycle_tasks": svc_create_tasks, "activate_employee": svc_activate, "check_unreturned_assets": svc_check_assets, "close_lifecycle_case": svc_close_case}
ENGINE = lambda: safe_engine(SERVICES)

# ---------- enforcement harness: what the xT runtime MUST do (neither engine does it)
class Instance:
    def __init__(self, pid, data, resource, xrn, step_action=None, dynamic=None):
        self.pid, self.resource, self.xrn = pid, resource, xrn
        self.dynamic = dynamic or {}      # lane -> user key bound to instance data (spec cannot declare this)
        self.step_action = step_action or {}
        self.wf = new_instance(XML[pid], pid, ENGINE(), data)
        self.log = []
    def ready(self): return ready_user_tasks(self.wf)
    def task(self): r = self.ready(); return r[0] if r else None
    def label(self, t): return f"{t.task_spec.bpmn_name}@{t.task_spec.lane}"
    def act(self, host_pack, actor_key, decision, humans_hosts):
        """claim+complete with 3 checks: decision value, lane role, IAM."""
        t = self.task(); step = t.task_spec.name.replace("ut_", "").replace("_esc", "")
        host, hum = humans_hosts
        who = hum[actor_key] if actor_key in hum else None
        if decision not in ("approve", "reject"):
            return self._deny(t, actor_key, "invalid_decision")
        lane = t.task_spec.lane
        lane_ok = (actor_key == self.dynamic[lane]) if lane in self.dynamic else (who is not None and lane in who.roles)
        if who is None or not lane_ok:
            return self._deny(t, actor_key, f"lane_mismatch(lane={t.task_spec.lane})")
        action = self.step_action.get(step, "record.approve")
        d = host.evaluate(IAM.Request(actor=who, action=action, xrn=self.xrn, resource=self.resource))
        if not d.allowed: return self._deny(t, actor_key, f"iam:{d.reason}")
        self.log.append(("OK", self.label(t), actor_key, decision))
        complete(self.wf, t, {f"{step}_decision": decision}); return True
    def _deny(self, t, actor_key, why):
        self.log.append(("DENY", self.label(t), actor_key, why)); return False
    def outcome(self):
        if not self.wf.is_completed(): return "PENDING:" + ",".join(self.label(t) for t in self.ready())
        return "REJECTED" if any(t.task_spec.name == "end_rejected" for t in self.wf.get_tasks(state=TaskState.COMPLETED)) else "APPROVED"
    def path(self): return [f"{l[2]}:{l[1].split('@')[0]}[{l[3]}]" if l[0]=="OK" else f"DENY({l[2]}):{l[3]}" for l in self.log]

def banner(s): print("\n" + "=" * 8, s)
H = {p: IAM.build_host(p) for p in PACKS}
HH = lambda p: (H[p][0], H[p][1])
def line(name, inst, extra=""): print(f"  {name:<46} outcome={inst.outcome():<12} path={inst.path()} {extra}")

# ===================== PACK 1
banner("PACK 1 proposal_approval")
p1 = "de-xuat-phe-duyet"; RES = lambda owner, amt, dept="KD": IAM.res(owner, amount=amt, dept=dept)
def p1_inst(loai, amt, owner="NV-0006", dept_head_req=False, xrn=None):
    return Instance("proposal_approval", {"kind": loai, "amount": amt, "requester": owner, "requester_is_dept_head": dept_head_req}, RES(owner, amt), xrn or IAM.X("proposal", f"{loai}-{amt}"))
i = p1_inst("advance_request", 4_000_000); i.act(p1, "NV-0005", "approve", HH(p1)); line("tạm ứng 4tr: Mai", i)
i = p1_inst("advance_request", 8_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0001")]; line("tạm ứng 8tr: Mai, Long (S1/S2)", i)
i = p1_inst("payment_request", 80_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0003", "NV-0002", "NV-0001")]; line("thanh toán 80tr: Mai, Bảo, Hà, Long", i)
i = p1_inst("advance_settlement", 12_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0003", "NV-0001")]; line("hoàn ứng 12tr: Mai, Bảo, Long", i)
n0 = len(LEDGER); i = p1_inst("advance_request", 8_000_000); i.act(p1, "NV-0005", "reject", HH(p1)); line("reject tại Mai (bước 1)", i, f"ledger+{len(LEDGER)-n0}")
n0 = len(LEDGER); i = p1_inst("payment_request", 80_000_000); [i.act(p1, k, d, HH(p1)) for k, d in (("NV-0005","approve"),("NV-0003","approve"),("NV-0002","approve"),("NV-0001","reject"))]; line("reject tại Long (bước cuối) 80tr", i, f"ledger+{len(LEDGER)-n0}")
i = p1_inst("advance_request", 8_000_000, owner="NV-0005", dept_head_req=True); i.act(p1, "NV-0001", "approve", HH(p1)); line("Mai là người đề nghị (bỏ bước TBP): Long", i)
OVERDUE["NV-0006"] = 1
i = p1_inst("advance_request", 8_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0002", "NV-0001")]; line("còn nợ quá hạn: Mai, Hà(xem xét nợ), Long", i); OVERDUE.clear()
print("  -- chặn tại bước (SoD / lane / quyết định lạ / agent)")
i = p1_inst("advance_request", 8_000_000, owner="NV-0005", dept_head_req=False)   # cố tình định tuyến sai: người đề nghị là TBP nhưng cờ = False
i.act(p1, "NV-0005", "approve", HH(p1)); line("Mai tự duyệt đề xuất của mình (định tuyến sai)", i)
i = p1_inst("advance_request", 8_000_000); i.act(p1, "NV-0006", "approve", HH(p1)); i.act(p1, "NV-0007", "approve", HH(p1)); line("Việt/Anh (không có vai trò) duyệt bước Mai", i)
i.act(p1, "NV-0012", "approve", HH(p1)); line("Thịnh (TBP IT, sai phòng) duyệt", i)
i.act(p1, "NV-0005", "return", HH(p1)); line("Mai trả quyết định 'return' (ngoài approve/reject)", i)
hum1 = H[p1][1]; ag = IAM.agent(IAM.pack_users(p1), "NV-0005", 3); hum_ag = {**hum1, "agent-claude": ag}
h_ag = IAM.Host(H[p1][0].backend, actors={**hum1, "agent-claude": ag})
i = p1_inst("advance_request", 8_000_000); i.act(p1, "agent-claude", "approve", (h_ag, hum_ag)); line("agent-claude duyệt bước TBP (không có lane)", i)
ag_lane = dataclasses.replace(ag, roles=("agent", "dept_head"))       # kể cả khi bị gán nhầm vai trò lane
h_ag2 = IAM.Host(H[p1][0].backend, actors={**hum1, "agent-claude": ag_lane})
i.act(p1, "agent-claude", "approve", (h_ag2, {**hum1, "agent-claude": ag_lane})); line("agent-claude gán nhầm role TBP, L3, không receipt", i)
print("  -- ghi sổ (LEDGER):", LEDGER)

# ===== SLA escalation with fake clock, state persisted through REAL Postgres JSONB (+ poller next_due)
banner("PACK 1: SLA leo thang (đồng hồ giả +25h) + lưu JSONB Postgres + poller next_due")
su.execute("create schema if not exists packs_test"); su.execute("drop table if exists packs_test.proc_instance")
su.execute("create table packs_test.proc_instance(id serial primary key, pid text, state jsonb not null, next_due timestamptz, done bool default false)")
reset_clock(); i = p1_inst("advance_request", 8_000_000)
def save(inst):
    evs = inst.wf.waiting_events()
    due = min((_RealDT.fromisoformat(e.value) if isinstance(e.value, str) else e.value for e in evs), default=None) if evs else None
    return su.execute("insert into packs_test.proc_instance(pid,state,next_due,done) values (%s,%s::jsonb,%s,%s) returning id, next_due", (inst.pid, json.dumps(to_state(inst.wf)), due, inst.wf.is_completed())).fetchone()
rid, due = save(i); print(f"  lưu instance #{rid}: next_due={due}")
print("  READY ban đầu:", [i.label(t) for t in i.ready()])
advance(25)
rows = su.execute("select id from packs_test.proc_instance where not done and next_due <= now() + %s::interval for update skip locked" , (f"{int(FakeDT.offset.total_seconds())} seconds",)).fetchall()
print("  poller (now giả = +25h) tìm thấy:", rows)
st = su.execute("select state from packs_test.proc_instance where id=%s", (rid,)).fetchone()[0]
i.wf = from_state(st, ENGINE()); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  sau refresh_timers (process mới, nạp từ JSONB):", [i.label(t) for t in i.ready()], "| NOTES:", NOTES[-1:])
i.act(p1, "NV-0005", "approve", HH(p1)); line("Mai (đã bị thay thế) cố duyệt sau khi leo thang", i)
i.act(p1, "NV-0001", "approve", HH(p1)); line("Long duyệt bước leo thang", i)
i.act(p1, "NV-0001", "approve", HH(p1)); line("Long duyệt tiếp bước GĐ (>5tr, lặp)", i, "<- GAP: GĐ phải duyệt 2 lần")
reset_clock()

# ===================== PACK 2
banner("PACK 2 task_*")
p2 = "giao-viec-sla"; SA = {"accept": "record.update", "work": "record.update"}
def p2_inst(pid="task_normal"): return Instance(pid, {"task_id": "CV-2026-0101"}, IAM.res("NV-0006", creator="NV-0006"), IAM.X("task", "CV-2026-0101"), SA, dynamic={"assignee": "NV-0006", "assigner": "NV-0005"})
i = p2_inst(); [i.act(p2, k, "approve", HH(p2)) for k in ("NV-0006", "NV-0006", "NV-0005")]; line("happy: Việt nhận, Việt nộp, Mai nghiệm thu", i)
i = p2_inst(); i.act(p2, "NV-0006", "approve", HH(p2)); i.act(p2, "NV-0006", "approve", HH(p2)); i.act(p2, "NV-0006", "approve", HH(p2)); line("Việt tự nghiệm thu", i)
i = p2_inst(); i.act(p2, "NV-0006", "reject", HH(p2)); line("Việt từ chối nhận việc", i)
i = p2_inst(); [i.act(p2, k, d, HH(p2)) for k, d in (("NV-0006","approve"),("NV-0006","approve"),("NV-0005","reject"))]; line("Mai không chấp nhận kết quả", i, "<- GAP: kết thúc luôn, không có vòng làm lại")
reset_clock(); i = p2_inst(); advance(5)
i.wf.refresh_timers(); i.wf.do_engine_steps(); print("  +5h chưa nhận việc ->", [i.label(t) for t in i.ready()])
i.act(p2, "NV-0005", "approve", HH(p2)); line("Mai (người giao) xử lý bước nhận đã leo thang", i)
reset_clock(); i = p2_inst(); i.act(p2, "NV-0006", "approve", HH(p2)); advance(73); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  +73h chưa nộp kết quả ->", [i.label(t) for t in i.ready()], "(người làm bị thay bằng dept_head)")
reset_clock(); i = p2_inst("task_urgent"); advance(1.5); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  lớp khẩn +1.5h chưa nhận ->", [i.label(t) for t in i.ready()])
reset_clock(); i = p2_inst("task_urgent"); i.act(p2, "NV-0006", "approve", HH(p2)); advance(0.5); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  lớp khẩn: +0.5h sau khi nhận, vẫn chờ người làm ->", [i.label(t) for t in i.ready()]); reset_clock()
print("  -- weekly digest (op tổng hợp đọc DB thật)")
dg = Instance("weekly_digest", {"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""}, IAM.res("NV-0006"), IAM.X("weekly_digest", "x"))
print("  outcome:", dg.outcome(), "| notify:", NOTES[-1])
print("  kiểm chứng bằng SQL:", sq("select code, total_assigned, total_completed, total_overdue, total_blocked, detail->'overdue' from xt_data.weekly_digest where code='BT-2026-W42'"))

# ===================== PACK 3
banner("PACK 3 employee_onboarding / employee_offboarding")
p3 = "nhan-su-vong-doi"; hum3 = H[p3][1]
def mk_emp(ma, ten, ph="KD", user_key=None):
    o = eng.create_record(seed_actor(), "employee", {"emp_no": ma, "user_id": str(user_id(ma)), "full_name": ten, "job_title": "Nhân viên Kinh doanh", "department": sq("select id from xt_data.department where code=%s", ph)[0][0].__str__(),
        "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "synthetic": True}); assert o["ok"], o; return str(o["record"]["id"])
def mk_case(code, emp_id, emp_no, loai, ph="KD"):
    o = eng.create_record(wf_actor, "lifecycle_case", {"code": code, "employee": emp_id, "kind": loai, "effective_on": "2026-10-31", "initiated_by": str(user_id("NV-0009")),
        "owner_id": str(user_id(emp_no)), "department_id": str(dept_id(ph))}); assert o["ok"], o; return str(o["record"]["id"])
nv16 = mk_emp("NV-0016", "Phạm Thị Hoa"); hs16 = mk_case("ON-2026-0002", nv16, "NV-0016", "onboarding")
RHS = IAM.res("NV-0016", creator="NV-0009")
i = Instance("employee_onboarding", {"case_id": hs16, "employee_id": nv16}, RHS, IAM.X("lifecycle_case", "ON-2026-0002"))
i.act(p3, "NV-0009", "approve", HH(p3)); line("Tâm (người khởi tạo hồ sơ) tự duyệt bước HR", i)
for k in ("NV-0010", "NV-0013", "NV-0003", "NV-0005"): i.act(p3, k, "approve", HH(p3))
line("onboarding: Linh, Vy, Bảo, Mai", i)
print("  DB: checklist =", sq("select owner_unit, count(*) from xt_data.lifecycle_task where lifecycle_case=%s group by 1 order by 1", hs16), "| employee.state =", sq("select state from xt_data.employee where id=%s", nv16))
# offboarding: tạo NV-0017 có tài sản/quyền
def mk_off(ma, ten, ph="KD"):
    nv = mk_emp(ma, ten, ph)
    eng.create_record(seed_actor(), "asset_issue", {"employee": nv, "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "kind": "laptop", "asset_code": f"Dell-{ma[-2:]}", "issued_on": "2025-01-02"})
    eng.create_record(seed_actor(), "access_grant", {"employee": nv, "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "system": "email", "role": "hộp thư", "issued_on": "2025-01-02"})
    return nv, mk_case(f"OF-2026-00{ma[-2:]}", nv, ma, "offboarding", ph)
def it_revoke(nv):
    su.execute("update xt_data.asset_issue set revoked_on='2026-10-31' where employee=%s", (nv,)); su.execute("update xt_data.access_grant set revoked_on='2026-10-31' where employee=%s", (nv,))
OFF_STEPS = lambda i, nv, revoke=True: ([i.act(p3, "NV-0010", "approve", HH(p3)), i.act(p3, "NV-0005", "approve", HH(p3)), (it_revoke(nv) if revoke else None), i.act(p3, "NV-0013", "approve", HH(p3)), i.act(p3, "NV-0003", "approve", HH(p3))])
nv17, hs17 = mk_off("NV-0017", "Lê Văn Nam"); RO = IAM.res("NV-0017", creator="NV-0009")
i = Instance("employee_offboarding", {"case_id": hs17, "employee_id": nv17, "employee_user": "NV-0017"}, RO, IAM.X("lifecycle_case", "OF-2026-0017"))
OFF_STEPS(i, nv17, True); i.act(p3, "NV-0010", "approve", HH(p3)); line("offboarding sạch: Linh, Mai, Vy(thu hồi), Bảo, Linh(đóng)", i)
print("  DB:", sq("select state from xt_data.employee where id=%s", nv17), sq("select state from xt_data.lifecycle_case where id=%s", hs17), "| checklist:", sq("select count(*) from xt_data.lifecycle_task where lifecycle_case=%s", hs17))
nv18, hs18 = mk_off("NV-0018", "Trần Thị Lan")
i = Instance("employee_offboarding", {"case_id": hs18, "employee_id": nv18, "employee_user": "NV-0018"}, IAM.res("NV-0018", creator="NV-0009"), IAM.X("lifecycle_case", "OF-2026-0018"))
OFF_STEPS(i, nv18, False); print("  tài sản chưa thu hồi -> bước kế tiếp:", [i.label(t) for t in i.ready()])
i.act(p3, "NV-0002", "approve", HH(p3))
print("  Hà xử lý khoản nợ -> bước kế tiếp:", [i.label(t) for t in i.ready()])
try:
    i.act(p3, "NV-0010", "approve", HH(p3)); print("  (guard không kích hoạt?)", i.outcome())
except Exception as e:
    print("  GUARD close_lifecycle_case chặn đóng hồ sơ:", type(e).__name__, "->", str(e)[:140])
    print("  DB: lifecycle_case =", sq("select state from xt_data.lifecycle_case where id=%s", hs18), "| employee =", sq("select state from xt_data.employee where id=%s", nv18))
    st = [(t.task_spec.name, TaskState.get_name(t.state)) for t in i.wf.get_tasks() if t.task_spec.name in ("sv_close_case", "ut_hr_close")]
    print("  trạng thái task sau lỗi:", st)
nv19, hs19 = mk_off("NV-0019", "Đinh Công Sơn")
i = Instance("employee_offboarding", {"case_id": hs19, "employee_id": nv19, "employee_user": "NV-0019"}, IAM.res("NV-0019", creator="NV-0009"), IAM.X("lifecycle_case", "OF-2026-0019"))
i.act(p3, "NV-0010", "approve", HH(p3)); i.act(p3, "NV-0005", "approve", HH(p3)); reset_clock(); advance(5)
i.wf.refresh_timers(); i.wf.do_engine_steps(); print("  IT idle +5h (SLA 4h, 'thu hồi trong ngày') ->", [i.label(t) for t in i.ready()]); reset_clock()
i = Instance("employee_offboarding", {"case_id": hs19, "employee_id": nv19, "employee_user": "NV-0019"}, IAM.res("NV-0019", creator="NV-0009"), IAM.X("lifecycle_case", "OF-2026-0019"))
i.act(p3, "NV-0010", "reject", HH(p3)); line("HR từ chối ngay bước đầu", i)

# ===================== compiler gap probes
banner("GAP PROBES: compiler/engine")
base = copy.deepcopy(SPECS["employee_onboarding"][1]); x0 = hashlib.sha256(compile_spec(base, ALLOWED_OPS).encode()).hexdigest()[:12]
s = copy.deepcopy(base); s["steps"][2]["parallel_with"] = "accounting"; s["steps"][3]["parallel"] = True
print("  parallel_with/parallel bị bỏ qua im lặng, XML y hệt:", hashlib.sha256(compile_spec(s, ALLOWED_OPS).encode()).hexdigest()[:12] == x0)
s = copy.deepcopy(base); s["steps"][1]["slaa"] = s["steps"][1].pop("sla")
x1 = compile_spec(s, ALLOWED_OPS); print("  gõ sai khóa 'sla'->'slaa': compile OK, số boundary timer:", x1.count("boundaryEvent id=") , "(gốc:", compile_spec(base, ALLOWED_OPS).count("boundaryEvent id="), ")")
for bad_type in ("parallel", "task", "wait", "timer", "subprocess"):
    s = copy.deepcopy(base); s["steps"][1]["type"] = bad_type
    try: compile_spec(s, ALLOWED_OPS); print("  type", bad_type, "-> compile OK ???")
    except Exception as e: print(f"  type={bad_type:<10} -> {type(e).__name__}: {e}")
s = copy.deepcopy(base); s["trigger"] = {"kind": "schedule", "cron": "* * * * *"}; print("  khóa 'trigger' lạ ở cấp spec: bị bỏ qua:", hashlib.sha256(compile_spec(s, ALLOWED_OPS).encode()).hexdigest()[:12] == x0)
s = copy.deepcopy(base); s["steps"][1]["sla"]["after"] = "P7D"
try: compile_spec(s, ALLOWED_OPS); print("  sla P7D: compile OK")
except Exception as e: print("  sla P7D ->", type(e).__name__, e)
s = copy.deepcopy(base); s["steps"][1]["sla"]["after"] = "P1W"
try: compile_spec(s, ALLOWED_OPS); print("  sla P1W: compile OK")
except Exception as e: print("  sla P1W ->", type(e).__name__, e)
s = copy.deepcopy(base); s["roles"].append("nhóm&<x>")
try:
    xr = compile_spec(s, ALLOWED_OPS); print("  role 'nhóm&<x>': compile_spec trả XML (không kiểm ID_RE/escape lane name)")
    try: load_specs([xr], "employee_onboarding"); print("    parser nhận XML?!")
    except Exception as e: print("    nhưng parser BPMN từ chối:", type(e).__name__, str(e)[:100])
except Exception as e: print("  role 'nhóm&<x>' ->", type(e).__name__, str(e)[:90])
s = copy.deepcopy(base); s["steps"][2]["when"] = "employee_age >= 18"; x2 = compile_spec(s, ALLOWED_OPS)
w = new_instance(x2, "employee_onboarding", ENGINE(), {"case_id": "x", "employee_id": "y"}) if False else None
try:
    w = new_instance(x2, "employee_onboarding", ENGINE(), {"case_id": "x", "employee_id": "y"}); t = w.get_tasks(state=TaskState.READY)[0]; w2 = ready_user_tasks(w)[0]; complete(w, w2, {"hr_decision": "approve"})
    print("  biến 'when' thiếu dữ liệu -> chạy tiếp?", [w.get_tasks(state=TaskState.READY)])
except Exception as e: print("  biến thiếu trong 'when' chỉ lỗi lúc chạy:", type(e).__name__, str(e)[:110])
# quyết định ngoài {approve, reject} đi thẳng qua engine (không qua harness)
w = new_instance(XML["proposal_approval"], "proposal_approval", ENGINE(), {"kind": "advance_request", "amount": 4_000_000, "requester": "NV-0006", "requester_is_dept_head": False, "chk_overdue_result": {"overdue": 0}})
complete(w, ready_user_tasks(w)[0], {"dept_head_decision": "return_for_info"})
print("  engine trần: dept_head_decision='return_for_info' ->", "APPROVED + ghi sổ" if w.is_completed() and not any(t.task_spec.name == "end_rejected" for t in w.get_tasks(state=TaskState.COMPLETED)) else "khác", "(gateway chỉ kiểm == 'reject')")
w = new_instance(XML["proposal_approval"], "proposal_approval", ENGINE(), {"kind": "advance_request", "amount": 4_000_000, "requester": "NV-0006", "requester_is_dept_head": False})
try: complete(w, ready_user_tasks(w)[0], {}); print("  thiếu dept_head_decision -> hoàn tất?", w.is_completed())
except Exception as e: print("  thiếu dept_head_decision ->", type(e).__name__, str(e)[:100])
print("\nLEDGER", LEDGER, "| NOTES", NOTES)
pool.close()
