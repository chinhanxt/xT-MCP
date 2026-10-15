# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""BPMNDI: mọi flow node có shape, mọi sequenceFlow có edge, shape nằm trong pool/lane, không chồng
nhau; SpiffWorkflow vẫn parse được. Kiểm bằng bpmn-moddle ở tests/integration/process."""

from __future__ import annotations

import pytest
from lxml import etree

from tests.unit.process.support import OPS, PACK_NAMES, load_pack
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.adapters.workflow.bpmn.spiff import load_specs

NS = {
    "b": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
}
FLOW_NODES = [
    "startEvent", "endEvent", "userTask", "serviceTask", "exclusiveGateway", "parallelGateway",
    "boundaryEvent",
]  # fmt: skip
Box = tuple[float, float, float, float]


def bounds(shape: etree._Element) -> Box:
    b = shape.find("dc:Bounds", NS)
    assert b is not None
    x, y, w, h = (float(b.get(k) or "nan") for k in ("x", "y", "width", "height"))
    return x, y, w, h


@pytest.mark.parametrize("name", PACK_NAMES)
def test_bpmndi_complete_and_consistent(name: str) -> None:
    c = compile_process(load_pack(name), OPS)
    load_specs(c.bpmn, c.process_id)  # SpiffWorkflow parse OK
    root = etree.fromstring(c.bpmn.encode())
    nodes = {
        str(e.get("id")): etree.QName(e).localname
        for t in FLOW_NODES
        for e in root.iterfind(f".//b:process/b:{t}", NS)
    }
    flows = [e.get("id") for e in root.iterfind(".//b:process/b:sequenceFlow", NS)]
    shapes = {s.get("bpmnElement"): s for s in root.iterfind(".//bpmndi:BPMNShape", NS)}
    edges = {e.get("bpmnElement"): e for e in root.iterfind(".//bpmndi:BPMNEdge", NS)}
    plane = root.find(".//bpmndi:BPMNPlane", NS)
    collab = root.find("b:collaboration", NS)
    assert plane is not None and collab is not None
    assert plane.get("bpmnElement") == collab.get("id")
    assert set(nodes) <= set(shapes), set(nodes) - set(shapes)
    assert set(flows) == set(edges)
    assert all(len(e.findall("di:waypoint", NS)) >= 2 for e in edges.values())
    pool = bounds(shapes[f"pool_{c.process_id}"])
    lanes = {
        ln.get("id"): (
            bounds(shapes[ln.get("id")]),
            [r.text for r in ln.iterfind("b:flowNodeRef", NS)],
        )
        for ln in root.iterfind(".//b:lane", NS)
    }
    assert sum(len(refs) for _, refs in lanes.values()) == len(nodes)  # mỗi node đúng 1 lane
    for (_, ly, _, lh), refs in lanes.values():
        for r in refs:
            x, y, w, h = bounds(shapes[r])
            assert pool[0] <= x and x + w <= pool[0] + pool[2]
            if nodes[str(r)] != "boundaryEvent":
                assert ly <= y and y + h <= ly + lh, r
    boxes = [(i, bounds(shapes[i])) for i, k in nodes.items() if k != "boundaryEvent"]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            (ia, (x1, y1, w1, h1)), (ib, (x2, y2, w2, h2)) = boxes[a], boxes[b]
            disjoint = x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1
            assert disjoint, f"chồng nhau: {ia} {ib}"
