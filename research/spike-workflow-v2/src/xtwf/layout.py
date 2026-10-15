# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Auto-layout BPMNDI đơn giản: pool 1 participant, lane ngang theo người nhận, x theo lớp (longest path trên đồ thị
đã bỏ cạnh quay lui), y theo lane + hàng. Đủ để BPMN mở có sơ đồ trong bpmn-js/Camunda Modeler (critic finding)."""
from __future__ import annotations

SIZE = {"start": (36, 36), "end": (36, 36), "boundary": (36, 36), "xgw": (50, 50), "pgw": (50, 50), "user": (120, 80), "service": (120, 80)}
COL_W, ROW_H, LABEL_W, X0, Y0 = 170, 130, 30, 20, 20


def _back_edges(g):
    """DFS từ start theo thứ tự flow; cạnh tới node đang trên stack là cạnh quay lui (vòng trả lại/ủy quyền)."""
    adj: dict[str, list] = {}
    for f in g.flows:
        src = g.nodes[f.src].extra.get("attached", f.src) if g.nodes[f.src].kind == "boundary" else f.src
        adj.setdefault(src, []).append((f, f.tgt))
    back, state = set(), {}
    stack = [("start", iter(adj.get("start", [])))]
    state["start"] = 1
    while stack:
        node, it = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            state[node] = 2
            stack.pop()
            continue
        f, t = nxt
        if state.get(t) == 1:
            back.add(id(f))
        elif t not in state:
            state[t] = 1
            stack.append((t, iter(adj.get(t, []))))
    for n in g.nodes.values():   # boundary chưa duyệt qua host
        if n.kind == "boundary":
            state.setdefault(n.id, 2)
    return back, adj


def layout_di(g, lane_names, fid, pid) -> str:
    back, adj = _back_edges(g)
    layer = {"start": 0}
    order = [n for n in g.nodes if n != "start" and g.nodes[n].kind != "boundary"]
    # longest path: lặp Bellman-Ford trên DAG cạnh xuôi (đồ thị nhỏ, ≤ vài trăm node)
    for _ in range(len(g.nodes) + 1):
        changed = False
        for src, outs in adj.items():
            if src not in layer:
                continue
            for f, t in outs:
                if id(f) in back:
                    continue
                if g.nodes[t].kind == "boundary":
                    continue
                if layer.get(t, -1) < layer[src] + 1:
                    layer[t] = layer[src] + 1
                    changed = True
        if not changed:
            break
    for n in order:
        layer.setdefault(n, 0)
    if "end" in layer:   # đặt các end event ở cột cuối
        last = max(layer.values())
        for n in g.nodes.values():
            if n.kind == "end":
                layer[n.id] = max(layer[n.id], last)
    slots: dict[tuple, int] = {}
    row = {}
    for n in ["start"] + order:
        key = (g.nodes[n].lane, layer[n])
        row[n] = slots.get(key, 0)
        slots[key] = row[n] + 1
    rows_per_lane = {ln: max([c for (l2, _), c in slots.items() if l2 == ln] or [1]) for ln in lane_names}
    lane_top, y = {}, Y0
    for ln in lane_names:
        lane_top[ln] = y
        y += rows_per_lane[ln] * ROW_H
    total_h = y - Y0
    total_w = LABEL_W * 2 + (max(layer.values()) + 1) * COL_W + 40
    bounds = {}
    for n in ["start"] + order:
        nd = g.nodes[n]
        w, h = SIZE[nd.kind]
        cx = X0 + LABEL_W * 2 + layer[n] * COL_W + COL_W / 2
        cy = lane_top[nd.lane] + row[n] * ROW_H + ROW_H / 2
        bounds[n] = (cx - w / 2, cy - h / 2, w, h)
    for nd in g.nodes.values():
        if nd.kind == "boundary":
            hx, hy, hw, hh = bounds[nd.extra["attached"]]
            bounds[nd.id] = (hx + hw - 48, hy + hh - 18, 36, 36)
    out = [f'<bpmndi:BPMNDiagram id="dia_{pid}"><bpmndi:BPMNPlane id="plane_{pid}" bpmnElement="collab_{pid}">',
           f'<bpmndi:BPMNShape id="pool_{pid}_di" bpmnElement="pool_{pid}" isHorizontal="true"><dc:Bounds x="{X0}" y="{Y0}" width="{total_w}" height="{total_h}"/></bpmndi:BPMNShape>']
    for i, ln in enumerate(lane_names):
        out.append(f'<bpmndi:BPMNShape id="lane_{i}_di" bpmnElement="lane_{i}" isHorizontal="true"><dc:Bounds x="{X0 + LABEL_W}" y="{lane_top[ln]}" '
                   f'width="{total_w - LABEL_W}" height="{rows_per_lane[ln] * ROW_H}"/></bpmndi:BPMNShape>')
    for n, (x, yy, w, h) in bounds.items():
        mk = ' isMarkerVisible="true"' if g.nodes[n].kind == "xgw" else ""
        out.append(f'<bpmndi:BPMNShape id="{n}_di" bpmnElement="{n}"{mk}><dc:Bounds x="{x:.0f}" y="{yy:.0f}" width="{w}" height="{h}"/></bpmndi:BPMNShape>')
    k = 0
    for f in g.flows:
        sx, sy, sw, sh = bounds[f.src]
        tx, ty, tw, th = bounds[f.tgt]
        scx, scy, tcx, tcy = sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2
        if g.nodes[f.src].kind == "boundary":
            pts = [(scx, sy + sh), (scx, tcy), (tx, tcy)] if tcy > sy + sh else [(scx, sy + sh), (scx, sy + sh + 20), (tx - 15, sy + sh + 20), (tx - 15, tcy), (tx, tcy)]
        elif id(f) in back or tcx <= scx:
            k += 1
            yb = max(sy + sh, ty + th) + 18 + 7 * (k % 4)
            pts = [(scx, sy + sh), (scx, yb), (tcx, yb), (tcx, ty + th)]
        elif abs(scy - tcy) < 1:
            pts = [(sx + sw, scy), (tx, tcy)]
        else:
            bx = tx - 20
            pts = [(sx + sw, scy), (bx, scy), (bx, tcy), (tx, tcy)]
        wp = "".join(f'<di:waypoint x="{px:.0f}" y="{py:.0f}"/>' for px, py in pts)
        out.append(f'<bpmndi:BPMNEdge id="{fid[id(f)]}_di" bpmnElement="{fid[id(f)]}">{wp}</bpmndi:BPMNEdge>')
    out.append("</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>")
    return "".join(out)
