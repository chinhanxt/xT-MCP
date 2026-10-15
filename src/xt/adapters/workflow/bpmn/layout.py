# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Đồ thị trung gian của compiler + auto-layout BPMNDI.

Pool 1 participant, lane ngang theo người nhận, cột = lớp longest-path trên đồ thị đã bỏ cạnh quay
lui (DFS), hàng theo lane. Cạnh quay lui đi vòng phía dưới; boundary đặt trên mép dưới task.
Đủ để mở trong bpmn-js / Camunda Modeler; sửa tay được vì là DI chuẩn.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from xt.domain.process.errors import SpecError

SYSTEM_LANE = "Hệ thống xT"
SIZE = {
    "start": (36, 36), "end": (36, 36), "boundary": (36, 36), "xgw": (50, 50),
    "pgw": (50, 50), "user": (120, 80), "service": (120, 80),
}  # fmt: skip
COL_W, ROW_H, LABEL_W, X0, Y0 = 170, 130, 30, 20, 20

Box = tuple[float, float, float, float]


@dataclass
class Node:
    id: str
    kind: str  # start|end|user|service|xgw|pgw|boundary
    name: str = ""
    lane: str = SYSTEM_LANE
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Flow:
    src: str
    tgt: str
    cond: str | None = None
    default: bool = False
    name: str = ""


class Graph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.flows: list[Flow] = []

    def add(self, n: Node) -> str:
        if n.id in self.nodes:
            raise SpecError([f"id sinh ra bị trùng: {n.id}"])
        self.nodes[n.id] = n
        return n.id

    def flow(
        self, a: str, b: str, cond: str | None = None, default: bool = False, name: str = ""
    ) -> None:
        self.flows.append(Flow(a, b, cond, default, name))

    def connect(self, exits: list[tuple[str, str | None, bool, str]], tgt: str) -> None:
        for s, c, d, nm in exits:
            self.flow(s, tgt, c, d, nm)


def _back_edges(g: Graph) -> tuple[set[int], dict[str, list[tuple[Flow, str]]]]:
    """DFS từ start; cạnh tới node đang trên stack là cạnh quay lui (vòng trả lại/ủy quyền)."""
    adj: dict[str, list[tuple[Flow, str]]] = {}
    for f in g.flows:
        src_node = g.nodes[f.src]
        src = src_node.extra.get("attached", f.src) if src_node.kind == "boundary" else f.src
        adj.setdefault(src, []).append((f, f.tgt))
    back: set[int] = set()
    state: dict[str, int] = {"start": 1}
    stack: list[tuple[str, Iterator[tuple[Flow, str]]]] = [("start", iter(adj.get("start", [])))]
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
    return back, adj


def _layers(g: Graph, back: set[int], adj: dict[str, list[tuple[Flow, str]]]) -> dict[str, int]:
    layer = {"start": 0}
    for _ in range(len(g.nodes) + 1):  # longest path (Bellman-Ford trên DAG cạnh xuôi)
        changed = False
        for src, outs in adj.items():
            if src not in layer:
                continue
            for f, t in outs:
                if id(f) in back or g.nodes[t].kind == "boundary":
                    continue
                if layer.get(t, -1) < layer[src] + 1:
                    layer[t] = layer[src] + 1
                    changed = True
        if not changed:
            break
    for n, nd in g.nodes.items():
        if nd.kind != "boundary":
            layer.setdefault(n, 0)
    last = max(layer.values())
    for nd in g.nodes.values():  # end event ở cột cuối
        if nd.kind == "end":
            layer[nd.id] = max(layer[nd.id], last)
    return layer


def _edge_points(
    g: Graph, f: Flow, bounds: dict[str, Box], back: set[int], k: int
) -> list[tuple[float, float]]:
    sx, sy, sw, sh = bounds[f.src]
    tx, ty, tw, th = bounds[f.tgt]
    scx, scy, tcx, tcy = sx + sw / 2, sy + sh / 2, tx + tw / 2, ty + th / 2
    if g.nodes[f.src].kind == "boundary":
        if tcy > sy + sh:
            return [(scx, sy + sh), (scx, tcy), (tx, tcy)]
        return [
            (scx, sy + sh),
            (scx, sy + sh + 20),
            (tx - 15, sy + sh + 20),
            (tx - 15, tcy),
            (tx, tcy),
        ]
    if id(f) in back or tcx <= scx:
        yb = max(sy + sh, ty + th) + 18 + 7 * (k % 4)
        return [(scx, sy + sh), (scx, yb), (tcx, yb), (tcx, ty + th)]
    if abs(scy - tcy) < 1:
        return [(sx + sw, scy), (tx, tcy)]
    bx = tx - 20
    return [(sx + sw, scy), (bx, scy), (bx, tcy), (tx, tcy)]


def layout_di(g: Graph, lane_names: list[str], fid: dict[int, str], pid: str) -> str:
    back, adj = _back_edges(g)
    layer = _layers(g, back, adj)
    order = ["start"] + [n for n in g.nodes if n != "start" and g.nodes[n].kind != "boundary"]
    slots: dict[tuple[str, int], int] = {}
    row: dict[str, int] = {}
    for n in order:
        key = (g.nodes[n].lane, layer[n])
        row[n] = slots.get(key, 0)
        slots[key] = row[n] + 1
    rows_per_lane = {
        ln: max([c for (l2, _), c in slots.items() if l2 == ln] or [1]) for ln in lane_names
    }
    lane_top: dict[str, int] = {}
    y = Y0
    for ln in lane_names:
        lane_top[ln] = y
        y += rows_per_lane[ln] * ROW_H
    total_h = y - Y0
    total_w = LABEL_W * 2 + (max(layer.values()) + 1) * COL_W + 40
    bounds: dict[str, Box] = {}
    for n in order:
        nd = g.nodes[n]
        w, h = SIZE[nd.kind]
        cx = X0 + LABEL_W * 2 + layer[n] * COL_W + COL_W / 2
        cy = lane_top[nd.lane] + row[n] * ROW_H + ROW_H / 2
        bounds[n] = (cx - w / 2, cy - h / 2, w, h)
    for nd in g.nodes.values():
        if nd.kind == "boundary":
            hx, hy, hw, hh = bounds[nd.extra["attached"]]
            bounds[nd.id] = (hx + hw - 48, hy + hh - 18, 36, 36)
    out = [
        f'<bpmndi:BPMNDiagram id="dia_{pid}"><bpmndi:BPMNPlane id="plane_{pid}" '
        f'bpmnElement="collab_{pid}">',
        f'<bpmndi:BPMNShape id="pool_{pid}_di" bpmnElement="pool_{pid}" isHorizontal="true">'
        f'<dc:Bounds x="{X0}" y="{Y0}" width="{total_w}" height="{total_h}"/></bpmndi:BPMNShape>',
    ]
    for i, ln in enumerate(lane_names):
        out.append(
            f'<bpmndi:BPMNShape id="lane_{i}_di" bpmnElement="lane_{i}" isHorizontal="true">'
            f'<dc:Bounds x="{X0 + LABEL_W}" y="{lane_top[ln]}" width="{total_w - LABEL_W}" '
            f'height="{rows_per_lane[ln] * ROW_H}"/></bpmndi:BPMNShape>'
        )
    for n, (bx, by, bw, bh) in bounds.items():
        mk = ' isMarkerVisible="true"' if g.nodes[n].kind == "xgw" else ""
        out.append(
            f'<bpmndi:BPMNShape id="{n}_di" bpmnElement="{n}"{mk}><dc:Bounds x="{bx:.0f}" '
            f'y="{by:.0f}" width="{bw}" height="{bh}"/></bpmndi:BPMNShape>'
        )
    k = 0
    for f in g.flows:
        src_cx = bounds[f.src][0] + bounds[f.src][2] / 2
        tgt_cx = bounds[f.tgt][0] + bounds[f.tgt][2] / 2
        if g.nodes[f.src].kind != "boundary" and (id(f) in back or tgt_cx <= src_cx):
            k += 1  # cạnh vòng phía dưới: so le độ cao để không đè nhau
        pts = _edge_points(g, f, bounds, back, k)
        wp = "".join(f'<di:waypoint x="{px:.0f}" y="{py:.0f}"/>' for px, py in pts)
        fl = fid[id(f)]
        out.append(f'<bpmndi:BPMNEdge id="{fl}_di" bpmnElement="{fl}">{wp}</bpmndi:BPMNEdge>')
    out.append("</bpmndi:BPMNPlane></bpmndi:BPMNDiagram>")
    return "".join(out)
