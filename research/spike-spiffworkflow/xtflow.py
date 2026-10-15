"""Lõi spike: parser + safe script engine + serializer. (SPDX: Apache-2.0 cho code spike; phụ thuộc SpiffWorkflow LGPL-3.0)"""
import ast, json
from SpiffWorkflow.bpmn import BpmnWorkflow
from SpiffWorkflow.bpmn.parser import BpmnValidator
from SpiffWorkflow.spiff.parser.process import SpiffBpmnParser
from SpiffWorkflow.spiff.serializer import DEFAULT_CONFIG
from SpiffWorkflow.bpmn.serializer import BpmnWorkflowSerializer
from SpiffWorkflow.bpmn.script_engine import PythonScriptEngine
from SpiffWorkflow.bpmn.script_engine.python_environment import BasePythonScriptEngineEnvironment

# ---------- Safe environment ----------
ALLOWED_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Div, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Name, ast.Load, ast.Constant, ast.List, ast.Tuple,
    ast.Subscript, ast.Slice, ast.IfExp)
SAFE_FUNCS = {}   # không cho gọi hàm nào trong expression

class UnsafeExpression(Exception): pass

def check_expr(expr: str):
    tree = ast.parse(expr.strip(), mode="eval")
    for n in ast.walk(tree):
        if not isinstance(n, ALLOWED_NODES):
            raise UnsafeExpression(f"node {type(n).__name__} not allowed in: {expr!r}")
        if isinstance(n, ast.Name) and n.id.startswith("_"):
            raise UnsafeExpression(f"name {n.id!r} not allowed")
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) > 200:
            raise UnsafeExpression("string constant too long")
    return tree

class SafeEnvironment(BasePythonScriptEngineEnvironment):
    def __init__(self, services=None):
        super().__init__({})
        self.services = services or {}          # whitelist: operation_name -> callable(**params)->dict
    def evaluate(self, expression, context, external_context=None):
        tree = check_expr(expression)
        env = {"__builtins__": {}}
        env.update(external_context or {}); env.update(context)
        return eval(compile(tree, "<expr>", "eval"), env)
    def execute(self, script, context, external_context=None):
        raise UnsafeExpression("script execution (scriptTask/pre/postScript) is disabled")
    def call_service(self, context, operation_name, operation_params, **kw):
        fn = self.services.get(operation_name)
        if fn is None:
            raise UnsafeExpression(f"service operation {operation_name!r} not registered")
        params = {k: v["value"] for k, v in operation_params.items()}
        return json.dumps(fn(**params))          # SpiffWorkflow mong đợi chuỗi JSON

class SafePythonScriptEngine(PythonScriptEngine):
    # PythonScriptEngine.call_service của SpiffWorkflow 3.2.0 có bug: nhánh lỗi tham chiếu biến `script` không tồn tại
    # -> NameError che mất lỗi thật. Override để lỗi rõ ràng.
    def call_service(self, task, **kwargs):
        try:
            return self.environment.call_service(task.data, **kwargs)
        except Exception as err:
            raise self.create_task_exec_exception(task, f"service:{kwargs.get('operation_name')}", err)

def safe_engine(services): return SafePythonScriptEngine(environment=SafeEnvironment(services))

# ---------- parse / serialize ----------
def load_specs(xml_strings, process_id):
    p = SpiffBpmnParser()
    for i, x in enumerate(xml_strings): p.add_bpmn_str(x.encode() if isinstance(x, str) else x, filename=f"m{i}.bpmn")
    spec = p.get_spec(process_id)
    return spec, p.get_subprocess_specs(process_id)

SER = BpmnWorkflowSerializer(BpmnWorkflowSerializer.configure(DEFAULT_CONFIG))

def new_instance(xml, process_id, engine, data=None):
    spec, subs = load_specs([xml], process_id)
    wf = BpmnWorkflow(spec, subs, script_engine=engine)
    wf.task_tree.data.update(data or {})     # dữ liệu khởi tạo nằm ở task gốc, tự lan sang các task sau
    wf.do_engine_steps()
    return wf

def complete(wf, task, data=None):
    task.data.update(data or {}); task.run(); wf.do_engine_steps()

def ready_user_tasks(wf):
    from SpiffWorkflow.util.task import TaskState
    return [t for t in wf.get_tasks(state=TaskState.READY) if t.task_spec.manual]

# ---------- Postgres JSONB helpers ----------
# BẪY: SpiffWorkflow 3.x lưu data task dạng delta so với task cha và deserialize THEO THỨ TỰ KEY của dict `tasks`.
# JSONB không giữ thứ tự key -> reload mất toàn bộ data (amount...). Phải sắp lại cha-trước-con khi load.
def _reorder(tasks: dict, root: str) -> dict:
    out, queue = {}, [root]
    while queue:
        k = queue.pop(0); t = tasks[k]; out[k] = t
        queue.extend(t["children"])
    assert len(out) == len(tasks), "orphan tasks"
    return out

def to_state(wf) -> dict:
    return json.loads(SER.serialize_json(wf))

def from_state(d: dict, engine):
    d = dict(d); d["tasks"] = _reorder(d["tasks"], d["root"])
    subs = {}
    for k, s in d.get("subprocesses", {}).items():
        s = dict(s); s["tasks"] = _reorder(s["tasks"], s["root"]); subs[k] = s
    d["subprocesses"] = subs
    wf = SER.deserialize_json(json.dumps(d))
    wf.script_engine = engine          # script engine không được serialize -> luôn gắn lại engine an toàn
    return wf
