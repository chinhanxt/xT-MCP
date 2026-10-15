# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Lưu/khôi phục state SpiffWorkflow dạng JSON.

Bẫy JSONB (SSPIFF §3): SpiffWorkflow deserialize theo thứ tự key của `tasks`; JSONB sắp lại key
-> mất dữ liệu. Hai lớp phòng thủ: (1) lưu cột `json` (giữ thứ tự), (2) `_reorder` BFS cha trước con
khi nạp (đúng với song song + vòng lặp, VS A.4). State hỏng (task mồ côi, vòng cha-con) -> từ chối.
Script engine KHÔNG được serialize: luôn gắn lại engine an toàn khi nạp.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from typing import Any

from SpiffWorkflow.bpmn import BpmnWorkflow
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.spiff.serializer import DEFAULT_CONFIG

from xt.adapters.workflow.bpmn.engine import SafePythonScriptEngine
from xt.domain.process.errors import EngineError

_SERIALIZER = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure(DEFAULT_CONFIG))


def reorder_tasks(tasks: Mapping[str, Any], root: str) -> dict[str, Any]:
    """BFS từ root, cha trước con; bỏ qua node lặp. Thiếu/thừa task -> state hỏng."""
    if root not in tasks:
        raise EngineError("state hỏng: không có task gốc")
    out: dict[str, Any] = {}
    queue: deque[str] = deque([root])
    while queue:
        k = queue.popleft()
        if k in out:
            continue
        if k not in tasks:
            raise EngineError(f"state hỏng: task con {k!r} không tồn tại")
        out[k] = tasks[k]
        queue.extend(tasks[k].get("children", []))
    if len(out) != len(tasks):
        raise EngineError("state hỏng: có task mồ côi")
    return out


def to_state(wf: BpmnWorkflow) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(_SERIALIZER.serialize_json(wf))
    return state


def from_state(state: Mapping[str, Any], engine: SafePythonScriptEngine) -> BpmnWorkflow:
    d = dict(state)
    d["tasks"] = reorder_tasks(d["tasks"], d["root"])
    subs: dict[str, Any] = {}
    for k, s in dict(d.get("subprocesses", {})).items():
        s = dict(s)
        s["tasks"] = reorder_tasks(s["tasks"], s["root"])
        subs[k] = s
    d["subprocesses"] = subs
    wf = _SERIALIZER.deserialize_json(json.dumps(d))
    wf.script_engine = engine
    return wf
