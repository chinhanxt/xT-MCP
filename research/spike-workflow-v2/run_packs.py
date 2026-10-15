# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chạy lại 6 process của research/packs-draft qua compiler/runtime v2.
Phần A: spec v1 gốc (bản chụp ở fixtures/v1_processes) qua validator nghiêm ngặt.
Phần B: spec v2 (packs/) compile + kịch bản chạy thật với org từ seed của pack, đồng hồ giả cho SLA/timer.
Dùng: .venv/bin/python run_packs.py > results/packs.txt"""
import json
import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime as _RealDT, timedelta
from zoneinfo import ZoneInfo

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE / "src"), str(HERE / "tests")]

import SpiffWorkflow.bpmn.specs.event_definitions.timer as TM  # noqa: E402

from conftest import FIX, OPS, Services, load_pack  # noqa: E402  (FakeDT được cài trong conftest)
from xtwf.compiler import compile_process  # noqa: E402
from xtwf.org import InMemoryOrg  # noqa: E402
from xtwf.runtime import DecisionError, EngineError, Runtime  # noqa: E402
from xtwf.spec import SpecError  # noqa: E402
from xtwf.timers import TimerPoller  # noqa: E402

FakeDT = TM.datetime
HCM = ZoneInfo("Asia/Ho_Chi_Minh")


def set_clock(dt):
    FakeDT.offset = dt - _RealDT.now(dt.tzinfo)


def advance(**kw):
    FakeDT.offset += timedelta(**kw)


def org():
    return InMemoryOrg.from_seed(json.loads((FIX / "org_users.json").read_text()), json.loads((FIX / "org_nhan_vien.json").read_text()))


def rt_for(name, sv=None, o=None, events=None):
    sv = sv or Services()
    return Runtime(compile_process(load_pack(name), OPS), sv, o or org(), admin_role="admin",
                   on_event=(events.append if events is not None else None)), sv


def roundtrip(rt, i):
    return rt.load(json.loads(json.dumps(i.to_state(), sort_keys=True)))   # mô phỏng JSONB: key bị sắp lại


def drive(rt, i, plan):
    """plan: list of (step, actor, outcome, kwargs). Mỗi bước: reload từ JSON đã sắp key rồi act."""
    path = []
    for step, actor, outcome, kw in plan:
        i = roundtrip(rt, i)
        box = [t for t in i.inbox() if t["step"] == step]
        if not box:
            path.append(f"!không có task {step}; inbox={[(t['step'], t['candidates']) for t in i.inbox()]}")
            break
        t = box[0]
        try:
            i.act(t["task_id"], actor, outcome, **kw)
            path.append(f"{step}[{t['kind']}]@{actor}:{outcome}" + (f"(via {t['via']})" if t["via"] != "primary" else ""))
        except (DecisionError, EngineError) as e:
            path.append(f"DENY {step}@{actor}:{outcome} -> {type(e).__name__}: {str(e)[:110]}")
    return i, path


def line(label, i, path, extra=""):
    print(f"  {label:<52} status={i.status():<9} path={path} {extra}")


def part_a():
    print("=" * 8, "A. 6 spec v1 gốc (packs-draft) qua validator v2 (extra=forbid)")
    for f in sorted((FIX / "v1_processes").glob("*.json")):
        try:
            compile_process(json.loads(f.read_text()), OPS)
            print(f"  {f.name}: !!! được chấp nhận")
        except SpecError as e:
            print(f"  {f.name}: bị từ chối, {len(e.errors)} lỗi; 3 lỗi đầu:")
            for x in e.errors[:3]:
                print(f"      - {x[:180]}")


def part_b_compile():
    print("=" * 8, "B0. Compile 6 spec v2")
    from lxml import etree
    for n in ["de_xuat_phe_duyet", "giao_viec_thuong", "giao_viec_khan", "ban_tin_tuan", "nv_onboarding", "nv_offboarding"]:
        c = compile_process(load_pack(n), OPS)
        r = etree.fromstring(c.bpmn.encode())
        cnt = lambda xp: len(r.xpath(xp))
        print(f"  {c.process_id:<22} bpmn={len(c.bpmn):>6}B userTask={cnt('//*[local-name()=\"userTask\"]'):>2} "
              f"parallelGW={cnt('//*[local-name()=\"parallelGateway\"]')} boundary={cnt('//*[local-name()=\"boundaryEvent\"]')} "
              f"lanes={cnt('//*[local-name()=\"lane\"]')} DI shapes={cnt('//*[local-name()=\"BPMNShape\"]')} edges={cnt('//*[local-name()=\"BPMNEdge\"]')} "
              f"timerStart={bool(cnt('//*[local-name()=\"startEvent\"]/*[local-name()=\"timerEventDefinition\"]'))}")


def pack1():
    print("=" * 8, "B1. proposal_approval (de_xuat_phe_duyet v2)")
    A = "approve"

    def run(label, kind, amount, plan, requester="NV-0006", setup=None):
        rt, sv = rt_for("de_xuat_phe_duyet")
        if setup:
            setup(sv)
        i = rt.start({"kind": kind, "amount": amount}, requester)
        i, path = drive(rt, i, plan)
        line(label, i, path, f"ledger={sv.ledger}")
        return rt, i, sv
    run("tạm ứng 4tr", "advance_request", 4_000_000, [("dept_head", "NV-0005", A, {})])
    run("tạm ứng 8tr", "advance_request", 8_000_000, [("dept_head", "NV-0005", A, {}), ("director", "NV-0001", A, {})])
    run("thanh toán 80tr", "payment_request", 80_000_000, [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("ktt", "NV-0002", A, {}), ("director", "NV-0001", A, {})])
    run("hoàn ứng 12tr", "advance_settlement", 12_000_000, [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("director", "NV-0001", A, {})])
    run("từ chối ở bước 1", "advance_request", 8_000_000, [("dept_head", "NV-0005", "reject", {"comment": "không hợp lệ"})])
    run("từ chối ở bước cuối 80tr", "payment_request", 80_000_000, [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("ktt", "NV-0002", A, {}), ("director", "NV-0001", "reject", {"comment": "hoãn"})])
    run("người đề nghị là TBP (NV-0005), không cần cờ requester_is_dept_head", "advance_request", 8_000_000,
        [("dept_head", "NV-0001", A, {}), ("director", "NV-0001", A, {})], requester="NV-0005")
    run("còn nợ quá hạn -> KTT xem xét", "advance_request", 8_000_000, [("dept_head", "NV-0005", A, {}), ("chief_acct_overdue", "NV-0002", A, {}), ("director", "NV-0001", A, {})],
        setup=lambda sv: sv.overdue.update({"NV-0006": 1}))
    run("G-P3: outcome 'return_for_info' (giá trị cũ gây APPROVED)", "advance_request", 8_000_000, [("dept_head", "NV-0005", "return_for_info", {})])
    run("trả lại bổ sung -> sửa 8tr thành 4tr -> chạy lại từ đầu", "advance_request", 8_000_000,
        [("dept_head", "NV-0005", A, {}), ("director", "NV-0001", "return_for_revision", {"comment": "giảm dưới 5tr"}),
         ("director", "NV-0006", "resubmit", {"updates": {"amount": 4_000_000}}), ("dept_head", "NV-0005", A, {})])
    run("trả lại rồi người đề nghị rút", "advance_request", 4_000_000, [("dept_head", "NV-0005", "return_for_revision", {"comment": "thiếu"}), ("dept_head", "NV-0006", "withdraw", {})])
    run("ủy quyền TBP -> NV-0007", "advance_request", 4_000_000, [("dept_head", "NV-0005", "delegate", {"delegate_to": "NV-0007"}), ("dept_head", "NV-0007", A, {})])
    run("hỏi thêm thông tin người đề nghị", "advance_request", 4_000_000,
        [("dept_head", "NV-0005", "request_info", {"comment": "mấy ngày?"}), ("dept_head", "NV-0006", "provide_info", {"comment": "3 ngày"}), ("dept_head", "NV-0005", A, {})])
    run("Việt (NV-0006, người đề nghị) / NV-0012 (sai phòng) duyệt bước TBP", "advance_request", 4_000_000,
        [("dept_head", "NV-0006", A, {}), ("dept_head", "NV-0012", A, {})])
    events = []
    rt, sv = rt_for("de_xuat_phe_duyet", events=events)
    i = rt.start({"kind": "advance_request", "amount": 1_000_000}, "NV-0001")
    t = i.inbox()[0]
    print(f"  {'giám đốc NV-0001 tự đề nghị (không có quản lý)':<52} via={t['via']} candidates={t['candidates']} trail={t['trail']}")
    i.reassign(t["task_id"], "NV-0012", "NV-0002", "HĐQT ủy quyền")
    i, path = drive(rt, i, [("dept_head", "NV-0002", A, {})])
    line("  -> quản trị NV-0012 gán lại cho NV-0002", i, path, f"events={[e['type'] for e in events]} ledger={sv.ledger}")
    rt, sv = rt_for("de_xuat_phe_duyet")
    set_clock(_RealDT(2026, 10, 15, 9, tzinfo=HCM))
    i = rt.start({"kind": "advance_request", "amount": 4_000_000}, "NV-0006")
    advance(hours=25)
    i = roundtrip(rt, i)
    i.tick()
    t = i.inbox()[0]
    i, path = drive(rt, i, [("dept_head", t["candidates"][0], A, {})])
    line("SLA 24h quá hạn -> leo thang requester.manager", i, path, f"spec={t['spec']} via={t['via']} trail={t['trail']} notes={sv.notes}")


def pack2():
    for name in ("giao_viec_thuong", "giao_viec_khan"):
        pid = load_pack(name)["id"]
        print("=" * 8, f"B2. {pid} ({name} v2)")
        D = "done"
        for label, inp, plan in [
            ("happy: NV-0007 làm, NV-0005 giao & nghiệm thu", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
             [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", "approve", {})]),
            ("nghiệm thu không đạt -> làm lại -> đạt", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
             [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", "return_for_revision", {"comment": "thiếu số liệu"}),
              ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", "approve", {})]),
            ("nghiệm thu không đạt -> từ chối", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
             [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", "reject", {"comment": "hủy"})]),
            ("tự giao cho mình: không được tự nghiệm thu", {"assignee_user": "NV-0005", "assigner_user": "NV-0005"},
             [("accept", "NV-0005", D, {}), ("work", "NV-0005", D, {}), ("acceptance", "NV-0005", "approve", {}), ("acceptance", "NV-0001", "approve", {})]),
            ("người làm cố nhận nghiệm thu", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
             [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0007", "approve", {})]),
        ]:
            rt, sv = rt_for(name)
            i = rt.start({"task_id": "CV-1", **inp}, inp["assigner_user"])
            i, path = drive(rt, i, plan)
            line(label, i, path, f"closed={[c for c in sv.calls if c[0] == 'close_task'] != []}")
        rt, sv = rt_for(name)
        set_clock(_RealDT(2026, 10, 15, 9, tzinfo=HCM))
        i = rt.start({"task_id": "CV-9", "assignee_user": "NV-0013", "assigner_user": "NV-0005"}, "NV-0005")
        i, _ = drive(rt, i, [("accept", "NV-0013", D, {})])
        advance(hours=73 if name.endswith("thuong") else 9)
        i.tick()
        t = i.inbox()[0]
        print(f"  {'SLA bước work quá hạn -> trưởng phòng của người làm':<52} spec={t['spec']} candidates={t['candidates']} reason={t['reason']}")


def pack_digest():
    print("=" * 8, "B3. weekly_digest (ban_tin_tuan v2): timer start + poller")
    rt, sv = rt_for("ban_tin_tuan")
    with tempfile.TemporaryDirectory() as d:
        conns = [sqlite3.connect(f"{d}/w.db", timeout=30) for _ in range(2)]
        ps = [TimerPoller(c, [{"process_key": "weekly_digest", "runtime": rt, "not_before": _RealDT(2026, 10, 15, 9, tzinfo=HCM)}]) for c in conns]
        ps[0].ensure_schema()
        for now in [_RealDT(2026, 10, 19, 7, 59, tzinfo=HCM), _RealDT(2026, 10, 19, 8, 0, 30, tzinfo=HCM), _RealDT(2026, 10, 19, 8, 5, tzinfo=HCM),
                    _RealDT(2026, 10, 26, 8, 1, tzinfo=HCM), _RealDT(2026, 11, 16, 9, 0, tzinfo=HCM)]:
            set_clock(now)
            for k, p in enumerate(ps):
                r = p.poll(now)
                print(f"  poll worker{k} {now.isoformat()} -> {[(x['period_key'], x['result']) for x in r]}")
        print(f"  wf_timer_fire={conns[0].execute('select period_key,status from wf_timer_fire order by 1').fetchall()}")
        print(f"  rollup calls={sv.calls}")
        print(f"  notify={sv.notes}")


def pack_hr():
    print("=" * 8, "B4. employee_onboarding (song song)")
    rt, sv = rt_for("nv_onboarding")
    i = rt.start({"case_id": "HS-15", "employee_id": "E-15", "employee_user": "NV-0015"}, "NV-0010")
    i, p = drive(rt, i, [("hr", "NV-0010", "done", {})])
    print(f"  sau HR: READY={[(t['step'], t['candidates']) for t in i.inbox()]}")
    i, p2 = drive(rt, i, [("manager", "NV-0005", "done", {}), ("it", "NV-0013", "request_info", {"comment": "laptop hay PC?", "ask": "NV-0005"}),
                          ("it", "NV-0005", "provide_info", {"comment": "laptop"}), ("accounting", "NV-0003", "done", {}), ("it", "NV-0013", "done", {})])
    line("HR -> (QL ‖ IT hỏi thêm ‖ KT) -> kích hoạt", i, p + p2, f"activate={[c for c in sv.calls if c[0] == 'activate_employee']}")
    print("=" * 8, "B5. employee_offboarding (song song + service trong nhánh)")
    for label, overdue, unreturned in [("không nợ, đã trả tài sản", 0, 0), ("còn nợ tạm ứng", 1, 0), ("còn tài sản -> close_lifecycle_case chặn", 0, 1)]:
        sv = Services()
        sv.overdue["NV-0011"] = overdue
        sv.unreturned = unreturned

        def close_case(case_id, sv=sv):
            if sv.unreturned:
                raise RuntimeError(f"không đóng được hồ sơ: còn {sv.unreturned} tài sản chưa thu hồi")
            sv.calls.append(("close_lifecycle_case", case_id))
            return {"closed": case_id}
        sv["close_lifecycle_case"] = close_case
        rt, _ = rt_for("nv_offboarding", sv)
        i = rt.start({"case_id": "HS-11", "employee_id": "E-11", "employee_user": "NV-0011"}, "NV-0010")
        plan = [("hr_confirm", "NV-0010", "done", {}), ("it", "NV-0014", "done", {}), ("manager", "NV-0009", "done", {}), ("accounting", "NV-0003", "done", {})]
        if overdue or unreturned:
            plan.append(("chief_acct_overdue", "NV-0002", "approve", {}))
        plan.append(("hr_close", "NV-0010", "done", {}))
        i, path = drive(rt, i, plan)
        line(label, i, path, f"closed={[c for c in sv.calls if c[0] == 'close_lifecycle_case']} inbox={[t['step'] for t in i.inbox()]}")


if __name__ == "__main__":
    part_a()
    part_b_compile()
    pack1()
    pack2()
    pack_digest()
    pack_hr()
