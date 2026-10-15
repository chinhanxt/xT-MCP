# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Compiler ProcessSpec v2 -> BPMN 2.0 (+BPMNDI) + manifest cho runtime.

Agent không bao giờ viết XML. Compiler chỉ phát sinh: startEvent (thường / timer), userTask (+lane),
serviceTask (op allowlist), exclusiveGateway, parallelGateway, boundary timer, endEvent (thường /
terminate). Không có scriptTask. Mọi giá trị chèn vào XML đều qua ID regex hoặc
`quoteattr`/`escape`.

Cổng quyết định (G-P3): `ut_X -> dec_ut_X` với MỘT nhánh có điều kiện cho mỗi outcome đã khai,
KHÔNG có nhánh mặc định: giá trị lạ -> SpiffWorkflow báo "No conditions satisfied" -> runtime khôi
phục state cũ (fail closed).
- approve/done -> bước kế; reject -> end_rejected (terminate);
- delegate -> quay lại chính ut_X (runtime gán người được ủy quyền);
- request_info -> ri_ut_X (người được hỏi) -> quay lại ut_X (đúng người đã hỏi);
- return_for_revision -> rv_X (người đề nghị: resubmit/withdraw) -> điểm đầu (restart) hoặc chính
  bước; hoặc thẳng về `to_step`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from xt.adapters.workflow.bpmn.engine import SLA_OP
from xt.adapters.workflow.bpmn.layout import SYSTEM_LANE, Flow, Graph, Node, layout_di
from xt.application.process.ports import CompiledProcess
from xt.domain.process.errors import SpecError, UnsafeExpression
from xt.domain.process.expr import check_expr
from xt.domain.process.spec import (
    AnyStep,
    ApproveStep,
    Assignee,
    HumanStep,
    ParallelStep,
    ProcessSpec,
    ServiceStep,
    TaskStep,
    TimerTrigger,
    parse_spec,
)

NOTIFY_OP = "notify"
SLA_DUE_PREFIX = "xt_sla_due_"
MAX_NAME_IN_PARAM = 150
OUTCOME_LABEL = {
    "approve": "Duyệt", "reject": "Từ chối", "return_for_revision": "Trả lại bổ sung",
    "delegate": "Ủy quyền", "request_info": "Hỏi thêm thông tin", "done": "Xong",
    "resubmit": "Nộp lại", "withdraw": "Rút",
}  # fmt: skip
REQUESTER_LANE = "Người đề nghị"
INFO_LANE = "Người được hỏi thông tin"

Exit = tuple[str, str | None, bool, str]  # (nguồn, điều kiện, default, nhãn)


def _cond(var: str, val: str) -> str:
    return f"{var} == {val!r}"


def spec_sha256(spec: ProcessSpec) -> str:
    raw = json.dumps(spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def check_ops(spec: ProcessSpec, allowed_ops: Mapping[str, Sequence[str]], notify_op: str) -> None:
    errs: list[str] = []
    for st, _ in spec.iter_steps():
        if isinstance(st, ServiceStep):
            if st.op.startswith("xt_"):
                errs.append(f"bước {st.id}: op {st.op!r} dùng tiền tố 'xt_' dành riêng cho engine")
            elif st.op not in allowed_ops:
                errs.append(
                    f"bước {st.id}: op {st.op!r} không có trong allowlist {sorted(allowed_ops)}"
                )
            elif set(st.params) != set(allowed_ops[st.op]):
                errs.append(
                    f"bước {st.id}: tham số {sorted(st.params)} khác khai báo của op "
                    f"{st.op!r}: {sorted(allowed_ops[st.op])}"
                )
        if isinstance(st, ApproveStep | TaskStep) and st.sla and notify_op not in allowed_ops:
            errs.append(f"bước {st.id}: có SLA nhưng op thông báo {notify_op!r} chưa đăng ký")
    if errs:
        raise SpecError(errs)


@dataclass
class _Ctx:
    first_entry: str | None = None
    entries: dict[str, str] = field(default_factory=dict)
    user_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)


class _Builder:
    def __init__(self, spec: ProcessSpec, notify_op: str):
        self.spec = spec
        self.notify_op = notify_op
        self.g = Graph()
        self.ctx = _Ctx()

    def _first(self, entry: str) -> None:
        self.ctx.first_entry = self.ctx.first_entry or entry

    def decision_block(
        self, st: HumanStep, ut_id: str, assignee: Assignee, escalated: bool, entry: str
    ) -> list[Exit]:
        """userTask + gateway quyết định. Trả về các lối ra 'approve/done'."""
        g, sid = self.g, st.id
        kind = "decision" if isinstance(st, ApproveStep) else "task"
        lane = assignee.describe()
        g.add(
            Node(
                ut_id, "user", st.name + (" (leo thang)" if escalated else ""), lane, {"step": sid}
            )
        )
        dec = g.add(Node(f"dec_{ut_id}", "xgw", f"Kết quả: {st.name}"))
        g.flow(ut_id, dec)
        var = f"{sid}_decision"
        on_return = st.on_return if isinstance(st, ApproveStep) else None
        self.ctx.user_tasks[ut_id] = {
            "step": sid, "kind": kind, "name": st.name, "assignee": assignee.model_dump(),
            "escalated": escalated, "outcomes": list(st.outcomes), "sod": st.sod.model_dump(),
            "on_return": on_return.model_dump() if on_return else None,
        }  # fmt: skip
        ok = "approve" if kind == "decision" else "done"
        exits: list[Exit] = [(dec, _cond(var, ok), False, OUTCOME_LABEL[ok])]
        if "reject" in st.outcomes:
            g.flow(dec, "end_rejected", _cond(var, "reject"), name=OUTCOME_LABEL["reject"])
        if "delegate" in st.outcomes:
            g.flow(dec, ut_id, _cond(var, "delegate"), name=OUTCOME_LABEL["delegate"])
        if "request_info" in st.outcomes:
            ri = g.add(Node(f"ri_{ut_id}", "user", f"Bổ sung thông tin: {st.name}", INFO_LANE,
                            {"step": sid}))  # fmt: skip
            self.ctx.user_tasks[ri] = {
                "step": sid, "kind": "info", "name": f"Bổ sung thông tin: {st.name}",
                "for_task": ut_id, "outcomes": ["provide_info"],
            }  # fmt: skip
            g.flow(dec, ri, _cond(var, "request_info"), name=OUTCOME_LABEL["request_info"])
            g.flow(ri, ut_id)
            if st.sla and not escalated:
                # AUD-2: chờ bổ sung thông tin KHÔNG dừng đồng hồ SLA (cùng hạn xt_sla_due_X)
                extra = {"attached": ri, "due": f"{SLA_DUE_PREFIX}{sid}"}
                rb = g.add(Node(f"slari_{sid}", "boundary", f"SLA {st.sla.after}", extra=extra))
                g.flow(rb, f"esc_{sid}")
        if "return_for_revision" in st.outcomes and on_return is not None:
            label = OUTCOME_LABEL["return_for_revision"]
            if on_return.to_step:
                target = self.ctx.entries[on_return.to_step]
                g.flow(dec, target, _cond(var, "return_for_revision"), name=label)
            else:
                rv = f"rv_{sid}"
                if rv not in g.nodes:
                    g.add(
                        Node(
                            rv, "user", f"Sửa và nộp lại: {st.name}", REQUESTER_LANE, {"step": sid}
                        )
                    )
                    rdec = g.add(Node(f"dec_{rv}", "xgw", "Nộp lại hay rút"))
                    g.flow(rv, rdec)
                    first = self.ctx.first_entry or entry
                    target = first if on_return.resume == "restart" else entry
                    g.flow(rdec, target, _cond(f"{sid}_revise", "resubmit"),
                           name=OUTCOME_LABEL["resubmit"])  # fmt: skip
                    g.flow(rdec, "end_rejected", _cond(f"{sid}_revise", "withdraw"),
                           name=OUTCOME_LABEL["withdraw"])  # fmt: skip
                    self.ctx.user_tasks[rv] = {
                        "step": sid, "kind": "revise", "name": f"Sửa và nộp lại: {st.name}",
                        "outcomes": ["resubmit", "withdraw"], "resume": on_return.resume,
                        "max_rounds": on_return.max_rounds,
                    }  # fmt: skip
                g.flow(dec, rv, _cond(var, "return_for_revision"), name=label)
        return exits

    def emit(self, st: AnyStep, incoming: list[Exit]) -> list[Exit]:
        g = self.g
        entry: str | None = None
        if st.when:
            check_expr(st.when)
            entry = g.add(Node(f"gw_{st.id}", "xgw", f"Điều kiện: {st.name}"))
            g.connect(incoming, entry)
            body_in: list[Exit] = [(entry, st.when, False, "Có")]
            skip: list[Exit] = [(entry, None, True, "Bỏ qua")]
        else:
            body_in, skip = incoming, []
        if isinstance(st, ServiceStep):
            extra = {"op": st.op, "params": dict(st.params), "result": f"{st.id}_result"}
            sv = g.add(Node(f"sv_{st.id}", "service", st.name, extra=extra))
            self._first(entry or sv)
            g.connect(body_in, sv)
            return [(sv, None, False, ""), *skip]
        if isinstance(st, ParallelStep):
            fork = g.add(Node(f"pf_{st.id}", "pgw", f"Song song: {st.name}"))
            join = g.add(Node(f"pj_{st.id}", "pgw", f"Gộp: {st.name}", extra={"join": True}))
            self._first(entry or fork)
            g.connect(body_in, fork)
            for bi, br in enumerate(st.branches):
                ex: list[Exit] = [(fork, None, False, "")]
                for s in br:
                    ex = self.emit(s, ex)
                if len(ex) > 1:
                    mg = g.add(Node(f"bm_{st.id}_{bi}", "xgw", "Gộp nhánh"))
                    g.connect(ex, mg)
                    ex = [(mg, None, False, "")]
                g.connect(ex, join)
            return [(join, None, False, ""), *skip]
        ut = f"ut_{st.id}"
        due = f"{SLA_DUE_PREFIX}{st.id}"
        if st.sla:
            # Hạn SLA tính MỘT lần khi vào bước (ủy quyền / hỏi thêm quay lại ut_X không đặt lại);
            # làm lại bước (restart, to_step) đi qua đây nên có hạn mới.
            sd = g.add(Node(f"slas_{st.id}", "service", f"Bắt đầu SLA {st.sla.after}",
                            extra={"op": SLA_OP, "params": {"after": repr(st.sla.after)},
                                   "result": due}))  # fmt: skip
            g.connect(body_in, sd)
            entry = entry or sd
            body_in = [(sd, None, False, "")]
        entry = entry or ut
        self._first(entry)
        self.ctx.entries[st.id] = entry
        g.connect(body_in, ut)
        if st.sla:
            # node leo thang tạo trước để boundary của ri_ut_X (trong decision_block) nối vào
            msg = repr(f"SLA vượt hạn: {st.name[:MAX_NAME_IN_PARAM]}")
            n = g.add(Node(f"esc_{st.id}", "service", "Thông báo leo thang",
                           extra={"op": self.notify_op, "params": {"msg": msg},
                                  "result": f"esc_{st.id}_result"}))  # fmt: skip
        exits = self.decision_block(st, ut, st.target(), False, entry)
        if st.sla:
            b = g.add(Node(f"sla_{st.id}", "boundary", f"SLA {st.sla.after}",
                           extra={"attached": ut, "due": due}))  # fmt: skip
            g.flow(b, n)
            esc_ut = f"ut_{st.id}_esc"
            g.flow(n, esc_ut)
            exits += self.decision_block(st, esc_ut, st.sla.target(), True, entry)
        return exits + skip


def compile_process(
    spec_data: ProcessSpec | Mapping[str, Any] | Any,
    allowed_ops: Mapping[str, Sequence[str]],
    notify_op: str = NOTIFY_OP,
) -> CompiledProcess:
    """Spec (dict hoặc ProcessSpec) -> CompiledProcess. Lỗi -> `SpecError` tiếng Việt."""
    spec = parse_spec(spec_data)
    check_ops(spec, allowed_ops, notify_op)
    b = _Builder(spec, notify_op)
    g = b.g
    timer = isinstance(spec.trigger, TimerTrigger)
    start_name = (
        f"Định kỳ {spec.trigger.rrule} ({spec.trigger.tz})"
        if isinstance(spec.trigger, TimerTrigger)
        else "Bắt đầu"
    )
    g.add(Node("start", "start", start_name, extra={"timer": timer}))
    g.add(Node("end", "end", "Hoàn tất"))
    g.add(Node("end_rejected", "end", "Kết thúc: từ chối/rút", extra={"terminate": True}))
    exits: list[Exit] = [("start", None, False, "")]
    for st in spec.steps:
        exits = b.emit(st, exits)
    g.connect(exits, "end")
    if not any(f.tgt == "end_rejected" for f in g.flows):
        del g.nodes["end_rejected"]
    # gateway gộp cho node có >1 incoming (trừ parallel join: mỗi nhánh đã gộp thành 1 flow)
    inc: dict[str, list[Flow]] = {}
    for f in g.flows:
        inc.setdefault(f.tgt, []).append(f)
    for tgt, fs in list(inc.items()):
        if len(fs) > 1 and not g.nodes[tgt].extra.get("join"):
            mg = g.add(Node(f"mg_{tgt}", "xgw", "Gộp"))
            for f in fs:
                f.tgt = mg
            g.flow(mg, tgt)
    try:
        for f in g.flows:
            if f.cond:
                check_expr(f.cond)
        for nd in g.nodes.values():  # AUD-1: kiểm cả tham số do compiler tự sinh
            for e in nd.extra.get("params", {}).values():
                check_expr(e)
    except UnsafeExpression as e:
        raise SpecError([f"biểu thức sinh ra không hợp lệ: {e}"]) from None
    lanes = [SYSTEM_LANE] + [
        n for n in dict.fromkeys(nd.lane for nd in g.nodes.values()) if n != SYSTEM_LANE
    ]
    fid = {id(f): f"fl_{i}" for i, f in enumerate(g.flows)}
    di = layout_di(g, lanes, fid, spec.id)
    bpmn = _emit_xml(spec, g, lanes, fid, di)
    ut = b.ctx.user_tasks
    manifest: dict[str, Any] = {
        "process_id": spec.id,
        "version": spec.version,
        "spec_sha256": spec_sha256(spec),
        "trigger": spec.trigger.model_dump(),
        "inputs": {k: v.model_dump() for k, v in spec.inputs.items()},
        "fallback_assignee": (fb.model_dump() if (fb := spec.fallback()) else None),
        "user_tasks": ut,
        "decision_steps": sorted(
            {v["step"] for v in ut.values() if v["kind"] in ("decision", "task")}
        ),
        "distinct": _distinct_map(spec),
        "lanes": lanes,
        "step_order": [st.id for st, _ in spec.iter_steps()],
    }
    return CompiledProcess(spec.id, bpmn, manifest, spec)


def _distinct_map(spec: ProcessSpec) -> dict[str, list[str]]:
    """distinct_from đối xứng: A khác B thì B khác A (kể cả hai nhánh song song, H3)."""
    m: dict[str, set[str]] = {}
    for st, _ in spec.iter_steps():
        if isinstance(st, ApproveStep | TaskStep):
            for d in st.sod.distinct_from:
                m.setdefault(st.id, set()).add(d)
                m.setdefault(d, set()).add(st.id)
    return {k: sorted(v) for k, v in m.items()}


_NS = (
    'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" '
    'xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" '
    'xmlns:di="http://www.omg.org/spec/DD/20100524/DI" '
    'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core"'
)


def _emit_xml(spec: ProcessSpec, g: Graph, lanes: list[str], fid: dict[int, str], di: str) -> str:
    pid, pname = spec.id, quoteattr(spec.name)
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<bpmn:definitions {_NS} id="defs_{pid}" targetNamespace="http://xt.local/bpmn" '
        'exporter="xT process compiler" exporterVersion="2">',
        f'<bpmn:collaboration id="collab_{pid}"><bpmn:participant id="pool_{pid}" name={pname} '
        f'processRef="{pid}"/></bpmn:collaboration>',
        f'<bpmn:process id="{pid}" name={pname} isExecutable="true">',
        '<bpmn:laneSet id="lanes">',
    ]
    for i, ln in enumerate(lanes):
        refs = "".join(
            f"<bpmn:flowNodeRef>{n.id}</bpmn:flowNodeRef>" for n in g.nodes.values() if n.lane == ln
        )
        out.append(f'<bpmn:lane id="lane_{i}" name={quoteattr(ln)}>{refs}</bpmn:lane>')
    out.append("</bpmn:laneSet>")
    outs: dict[str, list[Flow]] = {}
    ins: dict[str, list[Flow]] = {}
    for f in g.flows:
        outs.setdefault(f.src, []).append(f)
        ins.setdefault(f.tgt, []).append(f)

    def io(i: str) -> str:
        return "".join(
            f"<bpmn:incoming>{fid[id(f)]}</bpmn:incoming>" for f in ins.get(i, [])
        ) + "".join(f"<bpmn:outgoing>{fid[id(f)]}</bpmn:outgoing>" for f in outs.get(i, []))

    for n in g.nodes.values():
        nm = quoteattr(n.name)
        if n.kind == "start":
            ev = (
                '<bpmn:timerEventDefinition id="start_timer"><bpmn:timeDate '
                'xsi:type="bpmn:tFormalExpression">xt_fire_at</bpmn:timeDate>'
                "</bpmn:timerEventDefinition>"
                if n.extra.get("timer")
                else ""
            )
            out.append(f'<bpmn:startEvent id="{n.id}" name={nm}>{io(n.id)}{ev}</bpmn:startEvent>')
        elif n.kind == "end":
            ev = (
                f'<bpmn:terminateEventDefinition id="term_{n.id}"/>'
                if n.extra.get("terminate")
                else ""
            )
            out.append(f'<bpmn:endEvent id="{n.id}" name={nm}>{io(n.id)}{ev}</bpmn:endEvent>')
        elif n.kind == "user":
            out.append(f'<bpmn:userTask id="{n.id}" name={nm}>{io(n.id)}</bpmn:userTask>')
        elif n.kind == "xgw":
            d = [fid[id(f)] for f in outs.get(n.id, []) if f.default]
            dflt = f' default="{d[0]}"' if d else ""
            out.append(
                f'<bpmn:exclusiveGateway id="{n.id}" name={nm}{dflt}>{io(n.id)}'
                "</bpmn:exclusiveGateway>"
            )
        elif n.kind == "pgw":
            out.append(
                f'<bpmn:parallelGateway id="{n.id}" name={nm}>{io(n.id)}</bpmn:parallelGateway>'
            )
        elif n.kind == "boundary":
            out.append(
                f'<bpmn:boundaryEvent id="{n.id}" name={nm} attachedToRef="{n.extra["attached"]}" '
                f'cancelActivity="true">{io(n.id)}<bpmn:timerEventDefinition id="td_{n.id}">'
                f'<bpmn:timeDate xsi:type="bpmn:tFormalExpression">{escape(n.extra["due"])}'
                "</bpmn:timeDate></bpmn:timerEventDefinition></bpmn:boundaryEvent>"
            )
        elif n.kind == "service":
            ps = "".join(
                f'<spiffworkflow:parameter id="{p}" type="any" value={quoteattr(v)}/>'
                for p, v in n.extra["params"].items()
            )
            out.append(
                f'<bpmn:serviceTask id="{n.id}" name={nm}><bpmn:extensionElements>'
                f'<spiffworkflow:serviceTaskOperator id="{n.extra["op"]}" '
                f'resultVariable="{n.extra["result"]}"><spiffworkflow:parameters>{ps}'
                "</spiffworkflow:parameters></spiffworkflow:serviceTaskOperator>"
                f"</bpmn:extensionElements>{io(n.id)}</bpmn:serviceTask>"
            )
    for f in g.flows:
        c = (
            f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{escape(f.cond)}'
            "</bpmn:conditionExpression>"
            if f.cond
            else ""
        )
        nm = f" name={quoteattr(f.name)}" if f.name else ""
        out.append(
            f'<bpmn:sequenceFlow id="{fid[id(f)]}"{nm} sourceRef="{f.src}" targetRef="{f.tgt}">'
            f"{c}</bpmn:sequenceFlow>"
        )
    out += ["</bpmn:process>", di, "</bpmn:definitions>"]
    return "\n".join(out)
