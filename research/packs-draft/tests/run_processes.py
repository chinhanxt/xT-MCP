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

ALLOWED_OPS = {"ghi_so": ["amount", "requester"], "notify": ["msg"], "kiem_tra_cong_no": ["requester"], "dong_viec": ["task_id"],
 "tong_hop_tuan": ["tu_ngay", "den_ngay", "phong_ban"], "tao_viec_vong_doi": ["ho_so_id", "loai"], "kich_hoat_nhan_vien": ["nhan_vien_id"],
 "kiem_tra_tai_san": ["nhan_vien_id"], "dong_ho_so_vong_doi": ["ho_so_id"]}
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
it_actor = Actor(tenant_id(), user_id("NV-0013"), ("it_admin", "nhan_vien"), department_ids=(dept_id("IT"),))
su = psycopg.connect("host=localhost port=15440 dbname=xt user=xt_owner password=owner", autocommit=True)
LEDGER, NOTES, OVERDUE = [], [], {}
def svc_ghi_so(amount, requester): LEDGER.append((requester, amount)); return {"so": f"PC-{len(LEDGER):04d}"}
def svc_notify(msg): NOTES.append(msg); return {"ok": 1}
def svc_cong_no(requester): return {"qua_han": OVERDUE.get(requester, 0)}
def svc_dong_viec(task_id): return {"closed": task_id}
def sq(q, *a): return su.execute(q, a).fetchall()
def svc_tong_hop(tu_ngay, den_ngay, phong_ban):
    rows = sq("select ma_cv, trang_thai, han_chot < %s::date + 1 as qua, created_at::date between %s::date and %s::date from xt_data.cong_viec", den_ngay, tu_ngay, den_ngay)
    moi = [r[0] for r in rows if r[3]]; xong = [r[0] for r in rows if r[1] == "hoan_thanh"]
    qua = [r[0] for r in rows if r[2] and r[1] not in ("hoan_thanh", "huy", "khong_chap_nhan")]; ket = [r[0] for r in rows if r[1] == "bi_chan"]
    ma = f"BT-{den_ngay[:4]}-W{_RealDT.fromisoformat(den_ngay).isocalendar()[1]:02d}"
    out = eng.create_record(wf_actor, "ban_tin_tuan", {"ma": ma, "tu_ngay": tu_ngay, "den_ngay": den_ngay, "tong_giao_moi": len(moi), "tong_hoan_thanh": len(xong),
        "tong_qua_han": len(qua), "tong_dang_ket": len(ket), "noi_dung": f"Tuần {ma[-3:]}: giao mới {len(moi)}, hoàn thành {len(xong)}, quá hạn {len(qua)}, bị chặn {len(ket)}",
        "chi_tiet": {"giao_moi": moi, "hoan_thanh": xong, "qua_han": qua, "bi_chan": ket}})
    if not out["ok"]: raise RuntimeError(json.dumps(out, ensure_ascii=False))
    return {"ma": ma, "tom_tat": out["record"]["noi_dung"], "id": str(out["record"]["id"])}
def svc_tao_viec(ho_so_id, loai):
    spec = {"onboarding": [("nhan_su","Hồ sơ, HĐLĐ, BHXH"),("it","Email, tài khoản SSO, thiết bị"),("ke_toan","Mã lương, tài khoản nhận lương"),("quan_ly","Buddy và kế hoạch 30-60-90")],
            "offboarding": [("nhan_su","Xác nhận ngày nghỉ, đơn thôi việc"),("quan_ly","Bàn giao công việc, khách hàng, tài liệu"),("it","Thu hồi quyền và thiết bị"),("ke_toan","Quyết toán lương, phép, tạm ứng"),("nhan_su","Ẩn danh dữ liệu, đóng hồ sơ")]}[loai]
    for b, t in spec:
        o = eng.create_record(wf_actor, "viec_vong_doi", {"ho_so": ho_so_id, "bo_phan": b, "ten": t}); assert o["ok"], o
    return {"so_viec": len(spec)}
def svc_kich_hoat(nhan_vien_id):
    r = sq("select revision from xt_data.nhan_vien where id=%s", nhan_vien_id)[0][0]
    o = eng.update_record(wf_actor, "nhan_vien", nhan_vien_id, r, {"trang_thai": "thu_viec"}); assert o["ok"], o
    return {"trang_thai": "thu_viec"}
def svc_ktts(nhan_vien_id):
    ts = sq("select count(*) from xt_data.tai_san_cap where nhan_vien=%s and ngay_thu_hoi is null", nhan_vien_id)[0][0]
    cq = sq("select count(*) from xt_data.cap_quyen where nhan_vien=%s and ngay_thu_hoi is null", nhan_vien_id)[0][0]
    return {"chua_tra": int(ts), "con_quyen": int(cq)}
def svc_dong_hs(ho_so_id):
    nv = sq("select nhan_vien, revision from xt_data.ho_so_vong_doi where id=%s", ho_so_id)[0]
    left = svc_ktts(str(nv[0]))
    if left["chua_tra"] or left["con_quyen"]:
        raise RuntimeError(f"không đóng được hồ sơ: còn {left['chua_tra']} tài sản và {left['con_quyen']} quyền chưa thu hồi")
    o = eng.update_record(wf_actor, "ho_so_vong_doi", ho_so_id, nv[1], {"trang_thai": "hoan_tat", "hoan_tat_luc": "2026-10-15T17:00:00+07:00"}); assert o["ok"], o
    r = sq("select revision from xt_data.nhan_vien where id=%s", str(nv[0]))[0][0]
    o = eng.update_record(wf_actor, "nhan_vien", str(nv[0]), r, {"trang_thai": "da_nghi", "ngay_nghi": "2026-10-31"}); assert o["ok"], o
    return {"closed": ho_so_id}
SERVICES = {"ghi_so": svc_ghi_so, "notify": svc_notify, "kiem_tra_cong_no": svc_cong_no, "dong_viec": svc_dong_viec, "tong_hop_tuan": svc_tong_hop,
            "tao_viec_vong_doi": svc_tao_viec, "kich_hoat_nhan_vien": svc_kich_hoat, "kiem_tra_tai_san": svc_ktts, "dong_ho_so_vong_doi": svc_dong_hs}
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
banner("PACK 1 de_xuat_phe_duyet")
p1 = "de-xuat-phe-duyet"; RES = lambda owner, amt, dept="KD": IAM.res(owner, amount=amt, dept=dept)
def p1_inst(loai, amt, owner="NV-0006", tbp_req=False, xrn=None):
    return Instance("de_xuat_phe_duyet", {"loai": loai, "amount": amt, "requester": owner, "requester_la_tbp": tbp_req}, RES(owner, amt), xrn or IAM.X("de_xuat", f"{loai}-{amt}"))
i = p1_inst("tam_ung", 4_000_000); i.act(p1, "NV-0005", "approve", HH(p1)); line("tạm ứng 4tr: Mai", i)
i = p1_inst("tam_ung", 8_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0001")]; line("tạm ứng 8tr: Mai, Long (S1/S2)", i)
i = p1_inst("thanh_toan", 80_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0003", "NV-0002", "NV-0001")]; line("thanh toán 80tr: Mai, Bảo, Hà, Long", i)
i = p1_inst("hoan_ung", 12_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0003", "NV-0001")]; line("hoàn ứng 12tr: Mai, Bảo, Long", i)
n0 = len(LEDGER); i = p1_inst("tam_ung", 8_000_000); i.act(p1, "NV-0005", "reject", HH(p1)); line("reject tại Mai (bước 1)", i, f"ledger+{len(LEDGER)-n0}")
n0 = len(LEDGER); i = p1_inst("thanh_toan", 80_000_000); [i.act(p1, k, d, HH(p1)) for k, d in (("NV-0005","approve"),("NV-0003","approve"),("NV-0002","approve"),("NV-0001","reject"))]; line("reject tại Long (bước cuối) 80tr", i, f"ledger+{len(LEDGER)-n0}")
i = p1_inst("tam_ung", 8_000_000, owner="NV-0005", tbp_req=True); i.act(p1, "NV-0001", "approve", HH(p1)); line("Mai là người đề nghị (bỏ bước TBP): Long", i)
OVERDUE["NV-0006"] = 1
i = p1_inst("tam_ung", 8_000_000); [i.act(p1, k, "approve", HH(p1)) for k in ("NV-0005", "NV-0002", "NV-0001")]; line("còn nợ quá hạn: Mai, Hà(xem xét nợ), Long", i); OVERDUE.clear()
print("  -- chặn tại bước (SoD / lane / quyết định lạ / agent)")
i = p1_inst("tam_ung", 8_000_000, owner="NV-0005", tbp_req=False)   # cố tình định tuyến sai: người đề nghị là TBP nhưng cờ = False
i.act(p1, "NV-0005", "approve", HH(p1)); line("Mai tự duyệt đề xuất của mình (định tuyến sai)", i)
i = p1_inst("tam_ung", 8_000_000); i.act(p1, "NV-0006", "approve", HH(p1)); i.act(p1, "NV-0007", "approve", HH(p1)); line("Việt/Anh (không có vai trò) duyệt bước Mai", i)
i.act(p1, "NV-0012", "approve", HH(p1)); line("Thịnh (TBP IT, sai phòng) duyệt", i)
i.act(p1, "NV-0005", "return", HH(p1)); line("Mai trả quyết định 'return' (ngoài approve/reject)", i)
hum1 = H[p1][1]; ag = IAM.agent(IAM.pack_users(p1), "NV-0005", 3); hum_ag = {**hum1, "agent-claude": ag}
h_ag = IAM.Host(H[p1][0].backend, actors={**hum1, "agent-claude": ag})
i = p1_inst("tam_ung", 8_000_000); i.act(p1, "agent-claude", "approve", (h_ag, hum_ag)); line("agent-claude duyệt bước TBP (không có lane)", i)
ag_lane = dataclasses.replace(ag, roles=("agent", "truong_bo_phan"))       # kể cả khi bị gán nhầm vai trò lane
h_ag2 = IAM.Host(H[p1][0].backend, actors={**hum1, "agent-claude": ag_lane})
i.act(p1, "agent-claude", "approve", (h_ag2, {**hum1, "agent-claude": ag_lane})); line("agent-claude gán nhầm role TBP, L3, không receipt", i)
print("  -- ghi sổ (LEDGER):", LEDGER)

# ===== SLA escalation with fake clock, state persisted through REAL Postgres JSONB (+ poller next_due)
banner("PACK 1: SLA leo thang (đồng hồ giả +25h) + lưu JSONB Postgres + poller next_due")
su.execute("create schema if not exists packs_test"); su.execute("drop table if exists packs_test.proc_instance")
su.execute("create table packs_test.proc_instance(id serial primary key, pid text, state jsonb not null, next_due timestamptz, done bool default false)")
reset_clock(); i = p1_inst("tam_ung", 8_000_000)
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
banner("PACK 2 giao_viec_*")
p2 = "giao-viec-sla"; SA = {"nhan": "record.update", "lam": "record.update"}
def p2_inst(pid="giao_viec_thuong"): return Instance(pid, {"task_id": "CV-2026-0101"}, IAM.res("NV-0006", creator="NV-0006"), IAM.X("cong_viec", "CV-2026-0101"), SA, dynamic={"nguoi_thuc_hien": "NV-0006", "nguoi_giao": "NV-0005"})
i = p2_inst(); [i.act(p2, k, "approve", HH(p2)) for k in ("NV-0006", "NV-0006", "NV-0005")]; line("happy: Việt nhận, Việt nộp, Mai nghiệm thu", i)
i = p2_inst(); i.act(p2, "NV-0006", "approve", HH(p2)); i.act(p2, "NV-0006", "approve", HH(p2)); i.act(p2, "NV-0006", "approve", HH(p2)); line("Việt tự nghiệm thu", i)
i = p2_inst(); i.act(p2, "NV-0006", "reject", HH(p2)); line("Việt từ chối nhận việc", i)
i = p2_inst(); [i.act(p2, k, d, HH(p2)) for k, d in (("NV-0006","approve"),("NV-0006","approve"),("NV-0005","reject"))]; line("Mai không chấp nhận kết quả", i, "<- GAP: kết thúc luôn, không có vòng làm lại")
reset_clock(); i = p2_inst(); advance(5)
i.wf.refresh_timers(); i.wf.do_engine_steps(); print("  +5h chưa nhận việc ->", [i.label(t) for t in i.ready()])
i.act(p2, "NV-0005", "approve", HH(p2)); line("Mai (người giao) xử lý bước nhận đã leo thang", i)
reset_clock(); i = p2_inst(); i.act(p2, "NV-0006", "approve", HH(p2)); advance(73); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  +73h chưa nộp kết quả ->", [i.label(t) for t in i.ready()], "(người làm bị thay bằng truong_bo_phan)")
reset_clock(); i = p2_inst("giao_viec_khan"); advance(1.5); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  lớp khẩn +1.5h chưa nhận ->", [i.label(t) for t in i.ready()])
reset_clock(); i = p2_inst("giao_viec_khan"); i.act(p2, "NV-0006", "approve", HH(p2)); advance(0.5); i.wf.refresh_timers(); i.wf.do_engine_steps()
print("  lớp khẩn: +0.5h sau khi nhận, vẫn chờ người làm ->", [i.label(t) for t in i.ready()]); reset_clock()
print("  -- weekly digest (op tổng hợp đọc DB thật)")
dg = Instance("ban_tin_tuan", {"tu_ngay": "2026-10-12", "den_ngay": "2026-10-18", "phong_ban": ""}, IAM.res("NV-0006"), IAM.X("ban_tin_tuan", "x"))
print("  outcome:", dg.outcome(), "| notify:", NOTES[-1])
print("  kiểm chứng bằng SQL:", sq("select ma, tong_giao_moi, tong_hoan_thanh, tong_qua_han, tong_dang_ket, chi_tiet->'qua_han' from xt_data.ban_tin_tuan where ma='BT-2026-W42'"))

# ===================== PACK 3
banner("PACK 3 nv_onboarding / nv_offboarding")
p3 = "nhan-su-vong-doi"; hum3 = H[p3][1]
def mk_nv(ma, ten, ph="KD", user_key=None):
    o = eng.create_record(seed_actor(), "nhan_vien", {"ma_nv": ma, "user_id": str(user_id(ma)), "ho_ten": ten, "chuc_danh": "Nhân viên Kinh doanh", "phong_ban": sq("select id from xt_data.phong_ban where ma=%s", ph)[0][0].__str__(),
        "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "synthetic": True}); assert o["ok"], o; return str(o["record"]["id"])
def mk_hs(code, nv_id, ma_nv, loai, ph="KD"):
    o = eng.create_record(wf_actor, "ho_so_vong_doi", {"ma": code, "nhan_vien": nv_id, "loai": loai, "ngay_hieu_luc": "2026-10-31", "nguoi_khoi_tao": str(user_id("NV-0009")),
        "owner_id": str(user_id(ma_nv)), "department_id": str(dept_id(ph))}); assert o["ok"], o; return str(o["record"]["id"])
nv16 = mk_nv("NV-0016", "Phạm Thị Hoa"); hs16 = mk_hs("ON-2026-0002", nv16, "NV-0016", "onboarding")
RHS = IAM.res("NV-0016", creator="NV-0009")
i = Instance("nv_onboarding", {"ho_so_id": hs16, "nhan_vien_id": nv16}, RHS, IAM.X("ho_so_vong_doi", "ON-2026-0002"))
i.act(p3, "NV-0009", "approve", HH(p3)); line("Tâm (người khởi tạo hồ sơ) tự duyệt bước HR", i)
for k in ("NV-0010", "NV-0013", "NV-0003", "NV-0005"): i.act(p3, k, "approve", HH(p3))
line("onboarding: Linh, Vy, Bảo, Mai", i)
print("  DB: checklist =", sq("select bo_phan, count(*) from xt_data.viec_vong_doi where ho_so=%s group by 1 order by 1", hs16), "| nhan_vien.trang_thai =", sq("select trang_thai from xt_data.nhan_vien where id=%s", nv16))
# offboarding: tạo NV-0017 có tài sản/quyền
def mk_off(ma, ten, ph="KD"):
    nv = mk_nv(ma, ten, ph)
    eng.create_record(seed_actor(), "tai_san_cap", {"nhan_vien": nv, "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "loai": "laptop", "ma_tai_san": f"Dell-{ma[-2:]}", "ngay_cap": "2025-01-02"})
    eng.create_record(seed_actor(), "cap_quyen", {"nhan_vien": nv, "owner_id": str(user_id(ma)), "department_id": str(dept_id(ph)), "he_thong": "email", "vai_tro": "hộp thư", "ngay_cap": "2025-01-02"})
    return nv, mk_hs(f"OF-2026-00{ma[-2:]}", nv, ma, "offboarding", ph)
def it_revoke(nv):
    su.execute("update xt_data.tai_san_cap set ngay_thu_hoi='2026-10-31' where nhan_vien=%s", (nv,)); su.execute("update xt_data.cap_quyen set ngay_thu_hoi='2026-10-31' where nhan_vien=%s", (nv,))
OFF_STEPS = lambda i, nv, revoke=True: ([i.act(p3, "NV-0010", "approve", HH(p3)), i.act(p3, "NV-0005", "approve", HH(p3)), (it_revoke(nv) if revoke else None), i.act(p3, "NV-0013", "approve", HH(p3)), i.act(p3, "NV-0003", "approve", HH(p3))])
nv17, hs17 = mk_off("NV-0017", "Lê Văn Nam"); RO = IAM.res("NV-0017", creator="NV-0009")
i = Instance("nv_offboarding", {"ho_so_id": hs17, "nhan_vien_id": nv17, "nhan_vien_user": "NV-0017"}, RO, IAM.X("ho_so_vong_doi", "OF-2026-0017"))
OFF_STEPS(i, nv17, True); i.act(p3, "NV-0010", "approve", HH(p3)); line("offboarding sạch: Linh, Mai, Vy(thu hồi), Bảo, Linh(đóng)", i)
print("  DB:", sq("select trang_thai from xt_data.nhan_vien where id=%s", nv17), sq("select trang_thai from xt_data.ho_so_vong_doi where id=%s", hs17), "| checklist:", sq("select count(*) from xt_data.viec_vong_doi where ho_so=%s", hs17))
nv18, hs18 = mk_off("NV-0018", "Trần Thị Lan")
i = Instance("nv_offboarding", {"ho_so_id": hs18, "nhan_vien_id": nv18, "nhan_vien_user": "NV-0018"}, IAM.res("NV-0018", creator="NV-0009"), IAM.X("ho_so_vong_doi", "OF-2026-0018"))
OFF_STEPS(i, nv18, False); print("  tài sản chưa thu hồi -> bước kế tiếp:", [i.label(t) for t in i.ready()])
i.act(p3, "NV-0002", "approve", HH(p3))
print("  Hà xử lý khoản nợ -> bước kế tiếp:", [i.label(t) for t in i.ready()])
try:
    i.act(p3, "NV-0010", "approve", HH(p3)); print("  (guard không kích hoạt?)", i.outcome())
except Exception as e:
    print("  GUARD dong_ho_so_vong_doi chặn đóng hồ sơ:", type(e).__name__, "->", str(e)[:140])
    print("  DB: ho_so =", sq("select trang_thai from xt_data.ho_so_vong_doi where id=%s", hs18), "| nhan_vien =", sq("select trang_thai from xt_data.nhan_vien where id=%s", nv18))
    st = [(t.task_spec.name, TaskState.get_name(t.state)) for t in i.wf.get_tasks() if t.task_spec.name in ("sv_dong_ho_so", "ut_hr_dong")]
    print("  trạng thái task sau lỗi:", st)
nv19, hs19 = mk_off("NV-0019", "Đinh Công Sơn")
i = Instance("nv_offboarding", {"ho_so_id": hs19, "nhan_vien_id": nv19, "nhan_vien_user": "NV-0019"}, IAM.res("NV-0019", creator="NV-0009"), IAM.X("ho_so_vong_doi", "OF-2026-0019"))
i.act(p3, "NV-0010", "approve", HH(p3)); i.act(p3, "NV-0005", "approve", HH(p3)); reset_clock(); advance(5)
i.wf.refresh_timers(); i.wf.do_engine_steps(); print("  IT idle +5h (SLA 4h, 'thu hồi trong ngày') ->", [i.label(t) for t in i.ready()]); reset_clock()
i = Instance("nv_offboarding", {"ho_so_id": hs19, "nhan_vien_id": nv19, "nhan_vien_user": "NV-0019"}, IAM.res("NV-0019", creator="NV-0009"), IAM.X("ho_so_vong_doi", "OF-2026-0019"))
i.act(p3, "NV-0010", "reject", HH(p3)); line("HR từ chối ngay bước đầu", i)

# ===================== compiler gap probes
banner("GAP PROBES: compiler/engine")
base = copy.deepcopy(SPECS["nv_onboarding"][1]); x0 = hashlib.sha256(compile_spec(base, ALLOWED_OPS).encode()).hexdigest()[:12]
s = copy.deepcopy(base); s["steps"][2]["parallel_with"] = "kt"; s["steps"][3]["parallel"] = True
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
    try: load_specs([xr], "nv_onboarding"); print("    parser nhận XML?!")
    except Exception as e: print("    nhưng parser BPMN từ chối:", type(e).__name__, str(e)[:100])
except Exception as e: print("  role 'nhóm&<x>' ->", type(e).__name__, str(e)[:90])
s = copy.deepcopy(base); s["steps"][2]["when"] = "tuoi_nhan_vien >= 18"; x2 = compile_spec(s, ALLOWED_OPS)
w = new_instance(x2, "nv_onboarding", ENGINE(), {"ho_so_id": "x", "nhan_vien_id": "y"}) if False else None
try:
    w = new_instance(x2, "nv_onboarding", ENGINE(), {"ho_so_id": "x", "nhan_vien_id": "y"}); t = w.get_tasks(state=TaskState.READY)[0]; w2 = ready_user_tasks(w)[0]; complete(w, w2, {"hr_decision": "approve"})
    print("  biến 'when' thiếu dữ liệu -> chạy tiếp?", [w.get_tasks(state=TaskState.READY)])
except Exception as e: print("  biến thiếu trong 'when' chỉ lỗi lúc chạy:", type(e).__name__, str(e)[:110])
# quyết định ngoài {approve, reject} đi thẳng qua engine (không qua harness)
w = new_instance(XML["de_xuat_phe_duyet"], "de_xuat_phe_duyet", ENGINE(), {"loai": "tam_ung", "amount": 4_000_000, "requester": "NV-0006", "requester_la_tbp": False, "chk_no_result": {"qua_han": 0}})
complete(w, ready_user_tasks(w)[0], {"tbp_decision": "tra_lai_bo_sung"})
print("  engine trần: tbp_decision='tra_lai_bo_sung' ->", "APPROVED + ghi sổ" if w.is_completed() and not any(t.task_spec.name == "end_rejected" for t in w.get_tasks(state=TaskState.COMPLETED)) else "khác", "(gateway chỉ kiểm == 'reject')")
w = new_instance(XML["de_xuat_phe_duyet"], "de_xuat_phe_duyet", ENGINE(), {"loai": "tam_ung", "amount": 4_000_000, "requester": "NV-0006", "requester_la_tbp": False})
try: complete(w, ready_user_tasks(w)[0], {}); print("  thiếu tbp_decision -> hoàn tất?", w.is_completed())
except Exception as e: print("  thiếu tbp_decision ->", type(e).__name__, str(e)[:100])
print("\nLEDGER", LEDGER, "| NOTES", NOTES)
pool.close()
