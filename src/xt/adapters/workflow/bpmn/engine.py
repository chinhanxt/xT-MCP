# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Script engine an toàn cho SpiffWorkflow 3.2.0 (LGPL-3.0, chỉ import + subclass, không sửa).

- Mọi biểu thức (gateway, tham số service, timer) qua `xt.domain.process.expr` (AST whitelist).
- scriptTask/preScript/postScript bị tắt hẳn.
- Service chỉ chạy op có trong registry; lỗi service luôn bọc thành chuỗi (G-P9: SpiffWorkflow
  3.2.0 gốc nổ `TypeError` khi `args[0]` của lỗi không phải str).
- Op khai tham số `idempotency_key` nhận khóa `(instance, task)` (G-P10).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from datetime import UTC, timedelta
from typing import Any

import SpiffWorkflow.bpmn.specs.event_definitions.timer as _spiff_timer
from SpiffWorkflow.bpmn.script_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.script_engine.python_environment import BasePythonScriptEngineEnvironment

from xt.application.process.ports import ServiceRegistry
from xt.domain.process.errors import UnsafeExpression
from xt.domain.process.expr import evaluate
from xt.domain.process.spec import duration_seconds

SLA_OP = "xt_sla_deadline"


def sla_deadline(after: str) -> str:
    """Hạn SLA (ISO UTC) = bây giờ + after. Dùng cùng đồng hồ với timer của SpiffWorkflow."""
    now = _spiff_timer.datetime.now(UTC)
    return str((now + timedelta(seconds=duration_seconds(after))).isoformat())


class ServiceError(RuntimeError):
    pass


class SafeEnvironment(BasePythonScriptEngineEnvironment):  # type: ignore[misc]
    def __init__(self, services: ServiceRegistry | None = None):
        super().__init__({})
        self.services: ServiceRegistry = services or {}

    def evaluate(
        self,
        expression: str,
        context: Mapping[str, Any],
        external_context: Mapping[str, Any] | None = None,
    ) -> Any:
        env = dict(external_context or {})
        env.update(context)
        return evaluate(expression, env)

    def execute(
        self,
        script: str,
        context: Mapping[str, Any],
        external_context: Mapping[str, Any] | None = None,
    ) -> None:
        raise UnsafeExpression("scriptTask/preScript/postScript bị tắt")

    def call_service(
        self,
        context: Mapping[str, Any],
        operation_name: str | None = None,
        operation_params: Mapping[str, Mapping[str, Any]] | None = None,
        idempotency_key: str | None = None,
        **kw: Any,
    ) -> str:
        params = {k: v["value"] for k, v in (operation_params or {}).items()}
        if operation_name == SLA_OP:  # op nội bộ, không qua registry của deployment
            return json.dumps(sla_deadline(str(params.get("after"))))
        fn = self.services.get(operation_name or "")
        if fn is None:
            raise UnsafeExpression(f"service {operation_name!r} chưa được đăng ký")
        if idempotency_key is not None and "idempotency_key" in inspect.signature(fn).parameters:
            params["idempotency_key"] = idempotency_key
        return json.dumps(fn(**params), ensure_ascii=False)


class SafePythonScriptEngine(PythonScriptEngine):  # type: ignore[misc]
    def __init__(self, services: ServiceRegistry, instance_id: str):
        super().__init__(environment=SafeEnvironment(services))
        self.instance_id = instance_id

    def execute(self, task: Any, script: str, external_context: Any = None) -> None:
        raise UnsafeExpression("scriptTask/preScript/postScript bị tắt")

    def call_service(self, task: Any, **kwargs: Any) -> Any:
        op = kwargs.get("operation_name")
        try:
            return self.environment.call_service(
                task.data, idempotency_key=f"{self.instance_id}:{task.id}", **kwargs
            )
        except Exception as err:  # G-P9: luôn đưa về chuỗi để SpiffWorkflow không nổ TypeError
            wrapped = ServiceError(f"service {op!r} lỗi: {type(err).__name__}: {err}")
            raise self.create_task_exec_exception(task, f"service:{op}", wrapped) from err


def safe_engine(services: ServiceRegistry, instance_id: str = "local") -> SafePythonScriptEngine:
    return SafePythonScriptEngine(services, instance_id)
