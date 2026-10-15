"""Compiler: process-spec (dict/YAML, do agent sinh) -> BPMN 2.0 XML an toàn. Agent không bao giờ viết XML.
Chỉ phát sinh: startEvent, userTask(+lane), serviceTask(op allowlist), exclusiveGateway, boundary timer, endEvent."""
import re, yaml
from xml.sax.saxutils import escape, quoteattr
from xtflow import check_expr, UnsafeExpression

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
DUR_RE = re.compile(r"^P(T(\d+H)?(\d+M)?(\d+S)?|\d+D(T(\d+H)?(\d+M)?(\d+S)?)?)$")
class SpecError(Exception): pass

def compile_spec(spec, allowed_ops: dict, notify_op="notify") -> str:
    """allowed_ops: {op_name: [param,...]} – chỉ các service handler đã đăng ký."""
    pid = spec["id"]
    if not ID_RE.match(pid): raise SpecError(f"bad process id {pid!r}")
    roles = spec.get("roles") or []
    nodes, flows, lane_of = {}, [], {}          # nodes: id -> (kind, name, extra)
    def add(i, kind, name="", **x):
        if i in nodes: raise SpecError(f"duplicate id {i}")
        nodes[i] = (kind, name, x)
    def flow(a, b, cond=None, default=False): flows.append(dict(src=a, tgt=b, cond=cond, default=default))

    add("start", "start"); add("end", "end", "Hoàn tất"); add("end_rejected", "end", "Bị từ chối")
    exits = [("start", None, False)]
    seen = set()
    for st in spec["steps"]:
        sid, typ = st["id"], st["type"]
        if not ID_RE.match(sid) or sid in seen: raise SpecError(f"bad/duplicate step id {sid!r}")
        seen.add(sid)
        name = st.get("name", sid)
        entry = None
        if "when" in st:
            check_expr(st["when"])
            add(f"gw_{sid}", "gateway", f"Điều kiện {name}"); entry = f"gw_{sid}"
        if typ == "approve":
            role = st["role"]
            if role not in roles: raise SpecError(f"step {sid}: role {role!r} not in roles")
            add(f"ut_{sid}", "user", name, lane=role); first = f"ut_{sid}"
            add(f"dec_{sid}", "gateway", f"Quyết định {name}")
            flow(f"ut_{sid}", f"dec_{sid}")
            sla = st.get("sla")
            if sla:
                if not DUR_RE.match(sla["after"]): raise SpecError(f"step {sid}: bad ISO duration {sla['after']!r}")
                esc_role = sla["escalate_to"]
                if esc_role not in roles: raise SpecError(f"step {sid}: escalate_to {esc_role!r} not in roles")
                add(f"sla_{sid}", "boundary", f"SLA {name}", attached=f"ut_{sid}", duration=sla["after"])
                add(f"esc_notify_{sid}", "service", "Thông báo leo thang", op=notify_op, params={"msg": repr(f"SLA vượt hạn: {name}")})
                add(f"ut_{sid}_esc", "user", f"{name} (leo thang)", lane=esc_role)
                flow(f"sla_{sid}", f"esc_notify_{sid}"); flow(f"esc_notify_{sid}", f"ut_{sid}_esc"); flow(f"ut_{sid}_esc", f"dec_{sid}")
            flow(f"dec_{sid}", "end_rejected", cond=f"{sid}_decision == 'reject'")
            body_first, body_exits = first, [(f"dec_{sid}", None, True)]
        elif typ == "service":
            op = st["op"]
            if op not in allowed_ops: raise SpecError(f"step {sid}: op {op!r} not in allowlist {sorted(allowed_ops)}")
            params = st.get("params", {})
            if set(params) != set(allowed_ops[op]): raise SpecError(f"step {sid}: params {sorted(params)} != {allowed_ops[op]}")
            for v in params.values(): check_expr(str(v))
            add(f"sv_{sid}", "service", name, op=op, params={k: str(v) for k, v in params.items()}, result=f"{sid}_result")
            body_first, body_exits = f"sv_{sid}", [(f"sv_{sid}", None, False)]
        else:
            raise SpecError(f"step {sid}: unsupported type {typ!r}")
        if entry:
            for (s, c, d) in exits: flow(s, entry, c, d)
            flow(entry, body_first, cond=st["when"]); exits = body_exits + [(entry, None, True)]
        else:
            for (s, c, d) in exits: flow(s, body_first, c, d)
            exits = body_exits
    for (s, c, d) in exits: flow(s, "end", c, d)

    # chèn merge gateway cho node có >1 incoming
    inc = {}
    for f in flows: inc.setdefault(f["tgt"], []).append(f)
    for tgt, fs in list(inc.items()):
        if len(fs) > 1:
            mg = f"mg_{tgt}"; add(mg, "gateway", "merge")
            for f in fs: f["tgt"] = mg
            flow(mg, tgt)
    # emit
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
           f'xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core" id="defs_{pid}" targetNamespace="http://xt.local/bpmn">',
           f'<bpmn:process id="{pid}" name={quoteattr(spec.get("name", pid))} isExecutable="true">']
    out.append('<bpmn:laneSet id="lanes">')
    for r in roles:
        out.append(f'<bpmn:lane id="lane_{r}" name="{r}">' + "".join(f"<bpmn:flowNodeRef>{i}</bpmn:flowNodeRef>" for i, (k, n, x) in nodes.items() if k == "user" and x["lane"] == r) + "</bpmn:lane>")
    out.append("</bpmn:laneSet>")
    fid = {id(f): f"fl_{n}" for n, f in enumerate(flows)}
    outs = {}
    for f in flows: outs.setdefault(f["src"], []).append(f)
    for i, (k, n, x) in nodes.items():
        if k == "start": out.append(f'<bpmn:startEvent id="{i}"/>')
        elif k == "end": out.append(f'<bpmn:endEvent id="{i}" name={quoteattr(n)}/>')
        elif k == "user": out.append(f'<bpmn:userTask id="{i}" name={quoteattr(n)}/>')
        elif k == "gateway":
            d = [fid[id(f)] for f in outs.get(i, []) if f["default"]]
            out.append(f'<bpmn:exclusiveGateway id="{i}" name={quoteattr(n)}' + (f' default="{d[0]}"' if d else "") + "/>")
        elif k == "boundary":
            out.append(f'<bpmn:boundaryEvent id="{i}" name={quoteattr(n)} attachedToRef="{x["attached"]}" cancelActivity="true"><bpmn:timerEventDefinition>'
                       f'<bpmn:timeDuration xsi:type="bpmn:tFormalExpression">"{x["duration"]}"</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:boundaryEvent>')
        elif k == "service":
            ps = "".join(f'<spiffworkflow:parameter id="{p}" type="str" value={quoteattr(v)}/>' for p, v in x["params"].items())
            res = f' resultVariable="{x["result"]}"' if x.get("result") else ""
            out.append(f'<bpmn:serviceTask id="{i}" name={quoteattr(n)}><bpmn:extensionElements><spiffworkflow:serviceTaskOperator id="{x["op"]}"{res}>'
                       f'<spiffworkflow:parameters>{ps}</spiffworkflow:parameters></spiffworkflow:serviceTaskOperator></bpmn:extensionElements></bpmn:serviceTask>')
    for f in flows:
        c = f'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{escape(f["cond"])}</bpmn:conditionExpression>' if f["cond"] else ""
        if f["cond"]: check_expr(f["cond"])
        out.append(f'<bpmn:sequenceFlow id="{fid[id(f)]}" sourceRef="{f["src"]}" targetRef="{f["tgt"]}">{c}</bpmn:sequenceFlow>')
    out.append("</bpmn:process></bpmn:definitions>")
    return "\n".join(out)

SPEC_YAML = """
id: tamung
name: Đề nghị tạm ứng/thanh toán
version: {version}
roles: [truong_phong, giam_doc]
steps:
  - {{id: tp, type: approve, name: Trưởng phòng duyệt, role: truong_phong, sla: {{after: PT2S, escalate_to: giam_doc}}}}
  - {{id: gd, type: approve, name: Giám đốc duyệt, role: giam_doc, when: "amount > {threshold}"}}
  - {{id: ghiso, type: service, name: Ghi sổ, op: ghi_so, params: {{amount: amount, requester: requester}}}}
"""
ALLOWED_OPS = {"ghi_so": ["amount", "requester"], "notify": ["msg"]}
if __name__ == "__main__":
    import sys
    print(compile_spec(yaml.safe_load(SPEC_YAML.format(version=1, threshold=10000000)), ALLOWED_OPS))
