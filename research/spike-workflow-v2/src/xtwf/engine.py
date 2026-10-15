# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Safe script engine + parse/serialize cho SpiffWorkflow 3.2.0 (LGPL-3.0, chỉ import, không sửa).

Kế thừa research/spike-spiffworkflow/xtflow.py, bổ sung:
- trần độ dài/số node của biểu thức (minor VS phần A.1);
- bọc mọi lỗi service thành chuỗi (G-P9: args[0] không phải str làm SpiffWorkflow nổ TypeError);
- idempotency key (instance, task) cho service có khai tham số `idempotency_key` (G-P10);
- parser BPMN cứng: cấm DOCTYPE/entity trước khi đưa vào SpiffWorkflow (khuyến nghị VS A.3).
"""
import ast
import inspect
import json

from lxml import etree
from SpiffWorkflow.bpmn import BpmnWorkflow
from SpiffWorkflow.bpmn.script_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.script_engine.python_environment import BasePythonScriptEngineEnvironment
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer import DEFAULT_CONFIG
from SpiffWorkflow.util.task import TaskState

ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                 ast.BinOp, ast.Add, ast.Sub, ast.Div, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
                 ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Name, ast.Load, ast.Constant,
                 ast.List, ast.Tuple, ast.Subscript, ast.Slice, ast.IfExp)
MAX_EXPR_LEN = 500
MAX_EXPR_NODES = 120
MAX_STR_CONST = 200


class UnsafeExpression(ValueError):
    pass


class ServiceError(RuntimeError):
    pass


def check_expr(expr: str) -> ast.Expression:
    if not isinstance(expr, str):
        raise UnsafeExpression("biểu thức phải là chuỗi")
    if len(expr) > MAX_EXPR_LEN:
        raise UnsafeExpression(f"biểu thức dài quá {MAX_EXPR_LEN} ký tự")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except (SyntaxError, RecursionError, MemoryError, ValueError) as e:
        raise UnsafeExpression(f"biểu thức sai cú pháp: {type(e).__name__}") from None
    count = 0
    for n in ast.walk(tree):
        count += 1
        if count > MAX_EXPR_NODES:
            raise UnsafeExpression(f"biểu thức quá phức tạp (> {MAX_EXPR_NODES} node)")
        if not isinstance(n, ALLOWED_NODES):
            raise UnsafeExpression(f"cú pháp '{type(n).__name__}' không được phép trong biểu thức {expr!r}")
        if isinstance(n, ast.Name) and n.id.startswith("_"):
            raise UnsafeExpression(f"tên {n.id!r} không được phép (bắt đầu bằng '_')")
        if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)) and len(n.value) > MAX_STR_CONST:
            raise UnsafeExpression(f"hằng chuỗi dài quá {MAX_STR_CONST} ký tự")
        if isinstance(n, ast.Constant) and isinstance(n.value, bytes):
            raise UnsafeExpression("không cho phép hằng bytes")
    return tree


def expr_names(expr: str) -> set:
    return {n.id for n in ast.walk(check_expr(expr)) if isinstance(n, ast.Name)}


class SafeEnvironment(BasePythonScriptEngineEnvironment):
    def __init__(self, services=None):
        super().__init__({})
        self.services = services or {}

    def evaluate(self, expression, context, external_context=None):
        tree = check_expr(expression)
        env = {"__builtins__": {}}
        env.update(external_context or {})
        env.update(context)
        return eval(compile(tree, "<expr>", "eval"), env)

    def execute(self, script, context, external_context=None):
        raise UnsafeExpression("scriptTask/preScript/postScript bị tắt")

    def call_service(self, context, operation_name, operation_params, idempotency_key=None, **kw):
        fn = self.services.get(operation_name)
        if fn is None:
            raise UnsafeExpression(f"service {operation_name!r} chưa được đăng ký")
        params = {k: v["value"] for k, v in operation_params.items()}
        if idempotency_key is not None and "idempotency_key" in inspect.signature(fn).parameters:
            params["idempotency_key"] = idempotency_key
        return json.dumps(fn(**params), ensure_ascii=False)


class SafePythonScriptEngine(PythonScriptEngine):
    instance_id = "local"

    def call_service(self, task, **kwargs):
        try:
            return self.environment.call_service(task.data, idempotency_key=f"{self.instance_id}:{task.id}", **kwargs)
        except Exception as err:  # G-P9: luôn đưa về chuỗi để create_task_exec_exception không nổ TypeError
            wrapped = ServiceError(f"service {kwargs.get('operation_name')!r} lỗi: {type(err).__name__}: {err}")
            raise self.create_task_exec_exception(task, f"service:{kwargs.get('operation_name')}", wrapped) from err


def safe_engine(services, instance_id="local"):
    eng = SafePythonScriptEngine(environment=SafeEnvironment(services))
    eng.instance_id = instance_id
    return eng


HARD_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)
FORBIDDEN_TAGS = {"scriptTask", "callActivity"}


def harden_bpmn(xml: str | bytes) -> bytes:
    """Kiểm BPMN trước khi đưa cho SpiffWorkflow: không DOCTYPE, không entity, không scriptTask/pre/postScript."""
    raw = xml.encode() if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise UnsafeExpression("BPMN chứa DOCTYPE/ENTITY: bị từ chối")
    root = etree.fromstring(raw, HARD_PARSER)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = etree.QName(el).localname
        if local in FORBIDDEN_TAGS or local in ("preScript", "postScript", "script"):
            raise UnsafeExpression(f"BPMN chứa phần tử bị cấm: {local}")
    return raw


def load_specs(xml, process_id):
    p = SpiffBpmnParser()
    p.add_bpmn_str(harden_bpmn(xml), filename=f"{process_id}.bpmn")
    return p.get_spec(process_id), p.get_subprocess_specs(process_id)


SER = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure(DEFAULT_CONFIG))


def new_workflow(xml, process_id, engine, data=None):
    spec, subs = load_specs(xml, process_id)
    wf = BpmnWorkflow(spec, subs, script_engine=engine)
    wf.task_tree.data.update(data or {})
    wf.do_engine_steps()
    return wf


def ready_user_tasks(wf):
    return [t for t in wf.get_tasks(state=TaskState.READY) if t.task_spec.manual]


# Bẫy JSONB (SSPIFF §3): SpiffWorkflow deserialize theo thứ tự key của `tasks` -> sắp lại cha trước con.
def _reorder(tasks: dict, root: str) -> dict:
    out, queue = {}, [root]
    while queue:
        k = queue.pop(0)
        if k in out:
            continue
        out[k] = tasks[k]
        queue.extend(tasks[k]["children"])
    if len(out) != len(tasks):
        raise ValueError("state hỏng: có task mồ côi")
    return out


def to_state(wf) -> dict:
    return json.loads(SER.serialize_json(wf))


def from_state(d: dict, engine):
    d = dict(d)
    d["tasks"] = _reorder(d["tasks"], d["root"])
    subs = {}
    for k, s in d.get("subprocesses", {}).items():
        s = dict(s)
        s["tasks"] = _reorder(s["tasks"], s["root"])
        subs[k] = s
    d["subprocesses"] = subs
    wf = SER.deserialize_json(json.dumps(d))
    wf.script_engine = engine  # engine không được serialize: luôn gắn lại engine an toàn
    return wf
