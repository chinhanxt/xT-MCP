# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""BPMNDI: mọi flow node có shape, mọi sequenceFlow có edge, shape nằm trong pool/lane, không chồng nhau;
SpiffWorkflow vẫn parse được. Nếu có Node + viewer/ (bpmn-moddle, bpmn-js) thì render thật trong Chrome headless."""
import os
import pathlib
import shutil
import subprocess

import pytest
from lxml import etree

from conftest import OPS, load_pack
from test_spec import PACK_NAMES
from xtwf.compiler import compile_process
from xtwf.engine import load_specs

NS = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL", "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
      "dc": "http://www.omg.org/spec/DD/20100524/DC", "di": "http://www.omg.org/spec/DD/20100524/DI"}
FLOW_NODES = ["startEvent", "endEvent", "userTask", "serviceTask", "exclusiveGateway", "parallelGateway", "boundaryEvent"]


def bounds(shape):
    b = shape.find("dc:Bounds", NS)
    return tuple(float(b.get(k)) for k in ("x", "y", "width", "height"))


@pytest.mark.parametrize("name", PACK_NAMES)
def test_bpmndi_complete_and_consistent(name):
    c = compile_process(load_pack(name), OPS)
    load_specs(c.bpmn, c.process_id)                                            # SpiffWorkflow parse OK
    root = etree.fromstring(c.bpmn.encode())
    nodes = {e.get("id"): etree.QName(e).localname for t in FLOW_NODES for e in root.iterfind(f".//b:process/b:{t}", NS)}
    flows = [e.get("id") for e in root.iterfind(".//b:process/b:sequenceFlow", NS)]
    shapes = {s.get("bpmnElement"): s for s in root.iterfind(".//bpmndi:BPMNShape", NS)}
    edges = {e.get("bpmnElement"): e for e in root.iterfind(".//bpmndi:BPMNEdge", NS)}
    plane = root.find(".//bpmndi:BPMNPlane", NS)
    assert plane.get("bpmnElement") == root.find("b:collaboration", NS).get("id")
    assert set(nodes) <= set(shapes), set(nodes) - set(shapes)
    assert set(flows) == set(edges)
    assert all(len(e.findall("di:waypoint", NS)) >= 2 for e in edges.values())
    pool = bounds(shapes[f"pool_{c.process_id}"])
    lanes = {ln.get("id"): (bounds(shapes[ln.get("id")]), [r.text for r in ln.iterfind("b:flowNodeRef", NS)])
             for ln in root.iterfind(".//b:lane", NS)}
    assert sum(len(refs) for _, refs in lanes.values()) == len(nodes)           # mỗi node thuộc đúng 1 lane
    for (lx, ly, lw, lh), refs in lanes.values():
        for r in refs:
            x, y, w, h = bounds(shapes[r])
            assert pool[0] <= x and x + w <= pool[0] + pool[2]
            if nodes[r] != "boundaryEvent":
                assert ly <= y and y + h <= ly + lh, (r, (x, y, w, h), (lx, ly, lw, lh))
    boxes = [(i, bounds(shapes[i])) for i, k in nodes.items() if k != "boundaryEvent"]
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            (ia, (x1, y1, w1, h1)), (ib, (x2, y2, w2, h2)) = boxes[a], boxes[b]
            assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, f"chồng nhau: {ia} {ib}"


VIEWER = pathlib.Path(__file__).resolve().parents[1] / "viewer"


@pytest.mark.skipif(not (shutil.which("node") and (VIEWER / "node_modules" / "bpmn-js").exists() and os.path.exists("/usr/bin/google-chrome")),
                    reason="cần node + viewer/node_modules (bpmn-js, bpmn-moddle, playwright-core) + Chrome")
def test_bpmn_opens_in_bpmn_js_without_warnings(tmp_path):
    files = []
    for n in PACK_NAMES:
        c = compile_process(load_pack(n), OPS)
        f = tmp_path / f"{c.process_id}.bpmn"
        f.write_text(c.bpmn)
        files.append(str(f))
    r = subprocess.run(["node", str(VIEWER / "check.mjs"), *files], capture_output=True, text=True, env={**os.environ, "OUT": str(tmp_path)}, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    import json
    res = [json.loads(line) for line in r.stdout.splitlines()]
    assert len(res) == 6 and all(x["ok"] and not x["warnings"] and not x["moddle_warnings"] and x["shapes"] > 3 for x in res)
