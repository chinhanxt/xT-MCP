// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Kiểm BPMN do compiler xT sinh ra bằng bpmn-moddle (MIT, schema BPMN 2.0), không cần trình duyệt.
// Kiểm: cảnh báo/tham chiếu không giải được của moddle; mọi flow node có BPMNShape có bounds;
// mọi sequenceFlow có BPMNEdge >= 2 waypoint; mỗi flow node thuộc đúng 1 lane.
// Dùng: node check.mjs a.bpmn b.bpmn ...  In 1 dòng JSON/file; exit 1 nếu có lỗi.
import fs from 'node:fs';
import BpmnModdle from 'bpmn-moddle';

const FLOW = new Set(['bpmn:StartEvent', 'bpmn:EndEvent', 'bpmn:UserTask', 'bpmn:ServiceTask',
  'bpmn:ExclusiveGateway', 'bpmn:ParallelGateway', 'bpmn:BoundaryEvent']);
const moddle = new BpmnModdle();
let fail = 0;
for (const f of process.argv.slice(2)) {
  const errors = [];
  let shapes = 0, edges = 0;
  try {
    const { rootElement: defs, warnings } = await moddle.fromXML(fs.readFileSync(f, 'utf8'));
    for (const w of warnings) errors.push(`moddle: ${w.message}`);
    const proc = defs.rootElements.find(e => e.$type === 'bpmn:Process');
    const nodes = (proc.flowElements || []).filter(e => FLOW.has(e.$type));
    const flows = (proc.flowElements || []).filter(e => e.$type === 'bpmn:SequenceFlow');
    const di = new Map();
    for (const d of defs.diagrams || []) {
      for (const el of d.plane.planeElement || []) if (el.bpmnElement) di.set(el.bpmnElement.id, el);
    }
    for (const n of nodes) {
      const s = di.get(n.id);
      if (!s || s.$type !== 'bpmndi:BPMNShape' || !s.bounds) errors.push(`thiếu BPMNShape: ${n.id}`);
      else shapes++;
    }
    for (const fl of flows) {
      const e = di.get(fl.id);
      if (!e || e.$type !== 'bpmndi:BPMNEdge' || (e.waypoint || []).length < 2) errors.push(`thiếu BPMNEdge: ${fl.id}`);
      else edges++;
    }
    const laneOf = new Map();
    for (const ls of proc.laneSets || []) for (const ln of ls.lanes || []) {
      for (const r of ln.flowNodeRef || []) laneOf.set(r.id, (laneOf.get(r.id) || 0) + 1);
    }
    for (const n of nodes) if (laneOf.get(n.id) !== 1) errors.push(`node không thuộc đúng 1 lane: ${n.id}`);
  } catch (e) { errors.push(`parse: ${e}`); }
  if (errors.length) fail++;
  console.log(JSON.stringify({ file: f.split('/').pop(), ok: errors.length === 0, errors, shapes, edges }));
}
process.exit(fail ? 1 : 0);
