# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Compiler ProcessSpec v2 -> BPMN 2.0 (+BPMNDI) + manifest cho runtime.

Agent không bao giờ viết XML. Compiler chỉ phát sinh: startEvent (thường / timer), userTask(+lane),
serviceTask (op allowlist), exclusiveGateway, parallelGateway, boundary timer, endEvent (thường / terminate).
Mọi cổng quyết định chỉ có nhánh cho outcome đã khai, KHÔNG có nhánh mặc định: giá trị lạ -> SpiffWorkflow
báo "No conditions satisfied" -> runtime khôi phục state cũ (fail closed, G-P3).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from xml.sax.saxutils import escape, quoteattr

from .engine import check_expr
from .layout import layout_di
from .spec import ApproveStep, Assignee, ParallelStep, ProcessSpec, ServiceStep, SpecError, TaskStep, TimerTrigger, parse_spec

OUTCOME_LABEL = {"approve": "Duyệt", "reject": "Từ chối", "return_for_revision": "Trả lại bổ sung", "delegate": "Ủy quyền",
                 "request_info": "Hỏi thêm thông tin", "done": "Xong", "resubmit": "Nộp lại", "withdraw": "Rút"}
SYSTEM_LANE = "Hệ thống xT"
REQUESTER_LANE = "Người đề nghị"
INFO_LANE = "Người được hỏi thông tin"


@dataclass
class Node:
    id: str
    kind: str          # start|end|user|service|xgw|pgw|boundary
    name: str = ""
    lane: str = SYSTEM_LANE
    extra: dict = field(default_factory=dict)


@dataclass
class Flow:
    src: str
    tgt: str
    cond: str | None = None
    default: bool = False
    name: str = ""


@dataclass
class Compiled:
    process_id: str
    bpmn: str
    manifest: dict
    spec: ProcessSpec


class _G:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.flows: list[Flow] = []

    def add(self, n: Node) -> str:
        if n.id in self.nodes:
            raise SpecError([f"id sinh ra bị trùng: {n.id}"])
        self.nodes[n.id] = n
        return n.id

    def flow(self, a, b, cond=None, default=False, name=""):
        self.flows.append(Flow(a, b, cond, default, name))

    def connect(self, exits, tgt):
        for (s, c, d, nm) in exits:
            self.flow(s, tgt, c, d, nm)


def _cond(var, val):
    return f"{var} == {val!r}"


def compile_process(spec_data, allowed_ops: dict[str, list[str]], notify_op: str = "notify") -> Compiled:
    spec = spec_data if isinstance(spec_data, ProcessSpec) else parse_spec(spec_data)
    errs = []
    for st, _ in spec.iter_steps():
        if isinstance(st, ServiceStep):
            if st.op not in allowed_ops:
                errs.append(f"bước {st.id}: op {st.op!r} không có trong allowlist {sorted(allowed_ops)}")
            elif set(st.params) != set(allowed_ops[st.op]):
                errs.append(f"bước {st.id}: tham số {sorted(st.params)} khác khai báo của op {st.op!r}: {sorted(allowed_ops[st.op])}")
        if isinstance(st, (ApproveStep, TaskStep)) and st.sla and notify_op not in allowed_ops:
            errs.append(f"bước {st.id}: có SLA nhưng op thông báo {notify_op!r} chưa đăng ký")
    if errs:
        raise SpecError(errs)

    g = _G()
    timer = isinstance(spec.trigger, TimerTrigger)
    start_name = f"Định kỳ {spec.trigger.rrule} ({spec.trigger.tz})" if timer else "Bắt đầu"
    g.add(Node("start", "start", start_name, extra={"timer": timer}))
    g.add(Node("end", "end", "Hoàn tất"))
    g.add(Node("end_rejected", "end", "Kết thúc: từ chối/rút", extra={"terminate": True}))
    user_tasks: dict[str, dict] = {}
    lanes: dict[str, str] = {}      # lane name -> assignee key (for doc)
    ctx = {"first_entry": None, "entries": {}}

    def lane_for(a: Assignee) -> str:
        nm = a.describe()
        lanes.setdefault(nm, a.key())
        return nm

    def decision_block(st, ut_id, lane, assignee, escalated, entry_node, sid):
        """userTask + gateway quyết định. Trả về các lối ra 'approve/done'."""
        kind = "decision" if isinstance(st, ApproveStep) else "task"
        g.add(Node(ut_id, "user", st.name + (" (leo thang)" if escalated else ""), lane, {"step": sid}))
        dec = g.add(Node(f"dec_{ut_id}", "xgw", f"Kết quả: {st.name}"))
        g.flow(ut_id, dec)
        var = f"{sid}_decision"
        user_tasks[ut_id] = {"step": sid, "kind": kind, "name": st.name, "assignee": assignee.model_dump(), "escalated": escalated,
                             "outcomes": list(st.outcomes), "sod": st.sod.model_dump(),
                             "on_return": st.on_return.model_dump() if isinstance(st, ApproveStep) and st.on_return else None}
        ok = "approve" if kind == "decision" else "done"
        exits = [(dec, _cond(var, ok), False, OUTCOME_LABEL[ok])]
        if "reject" in st.outcomes:
            g.flow(dec, "end_rejected", _cond(var, "reject"), name=OUTCOME_LABEL["reject"])
        if "delegate" in st.outcomes:
            g.flow(dec, ut_id, _cond(var, "delegate"), name=OUTCOME_LABEL["delegate"])
        if "request_info" in st.outcomes:
            ri = g.add(Node(f"ri_{ut_id}", "user", f"Bổ sung thông tin: {st.name}", INFO_LANE, {"step": sid}))
            user_tasks[ri] = {"step": sid, "kind": "info", "name": f"Bổ sung thông tin: {st.name}", "for_task": ut_id, "outcomes": ["provide_info"]}
            g.flow(dec, ri, _cond(var, "request_info"), name=OUTCOME_LABEL["request_info"])
            g.flow(ri, ut_id)
        if "return_for_revision" in st.outcomes and st.on_return.to_step:
            g.flow(dec, ctx["entries"][st.on_return.to_step], _cond(var, "return_for_revision"), name=OUTCOME_LABEL["return_for_revision"])
            user_tasks[ut_id]["on_return"] = st.on_return.model_dump()
        elif "return_for_revision" in st.outcomes:
            rv = f"rv_{sid}"
            if rv not in g.nodes:
                g.add(Node(rv, "user", f"Sửa và nộp lại: {st.name}", REQUESTER_LANE, {"step": sid}))
                rdec = g.add(Node(f"dec_{rv}", "xgw", "Nộp lại hay rút"))
                g.flow(rv, rdec)
                target = ctx["first_entry"] if st.on_return.resume == "restart" else entry_node
                g.flow(rdec, target, _cond(f"{sid}_revise", "resubmit"), name=OUTCOME_LABEL["resubmit"])
                g.flow(rdec, "end_rejected", _cond(f"{sid}_revise", "withdraw"), name=OUTCOME_LABEL["withdraw"])
                user_tasks[rv] = {"step": sid, "kind": "revise", "name": f"Sửa và nộp lại: {st.name}", "outcomes": ["resubmit", "withdraw"],
                                  "resume": st.on_return.resume, "max_rounds": st.on_return.max_rounds}
            g.flow(dec, rv, _cond(var, "return_for_revision"), name=OUTCOME_LABEL["return_for_revision"])
        return exits

    def emit(st, incoming, in_parallel=False):
        entry = None
        if st.when:
            check_expr(st.when)
            entry = g.add(Node(f"gw_{st.id}", "xgw", f"Điều kiện: {st.name}"))
            g.connect(incoming, entry)
            body_in = [(entry, st.when, False, "Có")]
            skip = [(entry, None, True, "Bỏ qua")]
        else:
            body_in, skip = incoming, []
        if isinstance(st, ServiceStep):
            sv = g.add(Node(f"sv_{st.id}", "service", st.name, extra={"op": st.op, "params": dict(st.params), "result": f"{st.id}_result"}))
            entry = entry or sv
            ctx["first_entry"] = ctx["first_entry"] or entry
            g.connect(body_in, sv)
            return [(sv, None, False, "")] + skip
        if isinstance(st, ParallelStep):
            fork = g.add(Node(f"pf_{st.id}", "pgw", f"Song song: {st.name}"))
            join = g.add(Node(f"pj_{st.id}", "pgw", f"Gộp: {st.name}", extra={"join": True}))
            entry = entry or fork
            ctx["first_entry"] = ctx["first_entry"] or entry
            g.connect(body_in, fork)
            for bi, br in enumerate(st.branches):
                ex = [(fork, None, False, "")]
                for s in br:
                    ex = emit(s, ex, in_parallel=True)
                if len(ex) > 1:
                    mg = g.add(Node(f"bm_{st.id}_{bi}", "xgw", "Gộp nhánh"))
                    g.connect(ex, mg)
                    ex = [(mg, None, False, "")]
                g.connect(ex, join)
            return [(join, None, False, "")] + skip
        # human step
        ut = f"ut_{st.id}"
        entry = entry or ut
        ctx["first_entry"] = ctx["first_entry"] or entry
        ctx["entries"][st.id] = entry
        g.connect(body_in, ut)
        exits = decision_block(st, ut, lane_for(st.assignee), st.assignee, False, entry, st.id)
        if st.sla:
            b = g.add(Node(f"sla_{st.id}", "boundary", f"SLA {st.sla.after}", extra={"attached": ut, "duration": st.sla.after}))
            n = g.add(Node(f"esc_{st.id}", "service", "Thông báo leo thang",
                           extra={"op": notify_op, "params": {"msg": repr(f"SLA vượt hạn: {st.name}")}, "result": f"esc_{st.id}_result"}))
            g.flow(b, n)
            esc_ut = f"ut_{st.id}_esc"
            g.flow(n, esc_ut)
            exits += decision_block(st, esc_ut, lane_for(st.sla.escalate_to), st.sla.escalate_to, True, entry, st.id)
        return exits + skip

    exits = [("start", None, False, "")]
    for st in spec.steps:
        exits = emit(st, exits)
    g.connect(exits, "end")

    if not any(f.tgt == "end_rejected" for f in g.flows):
        del g.nodes["end_rejected"]
    # merge gateway cho node có >1 incoming (trừ parallel join: mỗi nhánh đã được gộp thành 1 flow)
    inc: dict[str, list[Flow]] = {}
    for f in g.flows:
        inc.setdefault(f.tgt, []).append(f)
    for tgt, fs in list(inc.items()):
        if len(fs) > 1 and not g.nodes[tgt].extra.get("join"):
            mg = g.add(Node(f"mg_{tgt}", "xgw", "Gộp"))
            for f in fs:
                f.tgt = mg
            g.flow(mg, tgt)
    for f in g.flows:
        if f.cond:
            check_expr(f.cond)

    lane_names = [SYSTEM_LANE] + [n for n in dict.fromkeys(nd.lane for nd in g.nodes.values()) if n != SYSTEM_LANE]
    fid = {id(f): f"fl_{i}" for i, f in enumerate(g.flows)}
    di = layout_di(g, lane_names, fid, spec.id)
    bpmn = _emit_xml(spec, g, lane_names, fid, di)
    manifest = {
        "process_id": spec.id, "version": spec.version,
        "spec_sha256": hashlib.sha256(json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        "trigger": spec.trigger.model_dump(), "inputs": {k: v.model_dump() for k, v in spec.inputs.items()},
        "fallback_assignee": spec.fallback_assignee.model_dump() if spec.fallback_assignee else None,
        "user_tasks": user_tasks, "decision_steps": sorted({v["step"] for v in user_tasks.values() if v["kind"] in ("decision", "task")}),
        "distinct": _distinct_map(spec),
        "lanes": lane_names, "step_order": [st.id for st, _ in spec.iter_steps()],
    }
    return Compiled(spec.id, bpmn, manifest, spec)


def _distinct_map(spec) -> dict:
    """distinct_from là quan hệ đối xứng: A khác B thì B cũng khác A (kể cả hai nhánh song song, ai làm trước cũng vậy)."""
    m: dict[str, set] = {}
    for st, _ in spec.iter_steps():
        for d in getattr(getattr(st, "sod", None), "distinct_from", []) or []:
            m.setdefault(st.id, set()).add(d)
            m.setdefault(d, set()).add(st.id)
    return {k: sorted(v) for k, v in m.items()}


def _emit_xml(spec, g: _G, lane_names, fid, di: str) -> str:
    pid = spec.id
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
           'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
           f'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core" id="defs_{pid}" targetNamespace="http://xt.local/bpmn" '
           'exporter="xT process compiler" exporterVersion="2">',
           f'<bpmn:collaboration id="collab_{pid}"><bpmn:participant id="pool_{pid}" name={quoteattr(spec.name)} processRef="{pid}"/></bpmn:collaboration>',
           f'<bpmn:process id="{pid}" name={quoteattr(spec.name)} isExecutable="true">',
           '<bpmn:laneSet id="lanes">']
    for i, ln in enumerate(lane_names):
        refs = "".join(f"<bpmn:flowNodeRef>{n.id}</bpmn:flowNodeRef>" for n in g.nodes.values() if n.lane == ln)
        out.append(f'<bpmn:lane id="lane_{i}" name={quoteattr(ln)}>{refs}</bpmn:lane>')
    out.append("</bpmn:laneSet>")
    outs: dict[str, list[Flow]] = {}
    ins: dict[str, list[Flow]] = {}
    for f in g.flows:
        outs.setdefault(f.src, []).append(f)
        ins.setdefault(f.tgt, []).append(f)

    def io(i):
        return "".join(f"<bpmn:incoming>{fid[id(f)]}</bpmn:incoming>" for f in ins.get(i, [])) + \
               "".join(f"<bpmn:outgoing>{fid[id(f)]}</bpmn:outgoing>" for f in outs.get(i, []))

    for n in g.nodes.values():
        nm = quoteattr(n.name)
        if n.kind == "start":
            ev = ('<bpmn:timerEventDefinition id="start_timer"><bpmn:timeDate xsi:type="bpmn:tFormalExpression">xt_fire_at</bpmn:timeDate>'
                  '</bpmn:timerEventDefinition>') if n.extra.get("timer") else ""
            out.append(f'<bpmn:startEvent id="{n.id}" name={nm}>{io(n.id)}{ev}</bpmn:startEvent>')
        elif n.kind == "end":
            ev = f'<bpmn:terminateEventDefinition id="term_{n.id}"/>' if n.extra.get("terminate") else ""
            out.append(f'<bpmn:endEvent id="{n.id}" name={nm}>{io(n.id)}{ev}</bpmn:endEvent>')
        elif n.kind == "user":
            out.append(f'<bpmn:userTask id="{n.id}" name={nm}>{io(n.id)}</bpmn:userTask>')
        elif n.kind == "xgw":
            d = [fid[id(f)] for f in outs.get(n.id, []) if f.default]
            out.append(f'<bpmn:exclusiveGateway id="{n.id}" name={nm}' + (f' default="{d[0]}"' if d else "") + f">{io(n.id)}</bpmn:exclusiveGateway>")
        elif n.kind == "pgw":
            out.append(f'<bpmn:parallelGateway id="{n.id}" name={nm}>{io(n.id)}</bpmn:parallelGateway>')
        elif n.kind == "boundary":
            out.append(f'<bpmn:boundaryEvent id="{n.id}" name={nm} attachedToRef="{n.extra["attached"]}" cancelActivity="true">{io(n.id)}'
                       f'<bpmn:timerEventDefinition id="td_{n.id}"><bpmn:timeDuration xsi:type="bpmn:tFormalExpression">'
                       f'{escape(repr(n.extra["duration"]))}</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:boundaryEvent>')
        elif n.kind == "service":
            ps = "".join(f'<spiffworkflow:parameter id="{p}" type="any" value={quoteattr(v)}/>' for p, v in n.extra["params"].items())
            out.append(f'<bpmn:serviceTask id="{n.id}" name={nm}><bpmn:extensionElements><spiffworkflow:serviceTaskOperator id="{n.extra["op"]}" '
                       f'resultVariable="{n.extra["result"]}"><spiffworkflow:parameters>{ps}</spiffworkflow:parameters></spiffworkflow:serviceTaskOperator>'
                       f'</bpmn:extensionElements>{io(n.id)}</bpmn:serviceTask>')
    for f in g.flows:
        c = f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{escape(f.cond)}</bpmn:conditionExpression>' if f.cond else ""
        nm = f" name={quoteattr(f.name)}" if f.name else ""
        out.append(f'<bpmn:sequenceFlow id="{fid[id(f)]}"{nm} sourceRef="{f.src}" targetRef="{f.tgt}">{c}</bpmn:sequenceFlow>')
    out.append("</bpmn:process>")
    out.append(di)
    out.append("</bpmn:definitions>")
    return "\n".join(out)
