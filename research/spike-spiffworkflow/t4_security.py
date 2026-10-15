import sys, os, json; sys.path.insert(0, "src")
from xtflow import *
from SpiffWorkflow.bpmn.script_engine import TaskDataEnvironment

HDR = '''<?xml version="1.0"?><bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:spiffworkflow="http://spiffworkflow.org/bpmn/schema/1.0/core" id="d" targetNamespace="x"><bpmn:process id="p" isExecutable="true">'''
FTR = '</bpmn:process></bpmn:definitions>'
def cond_xml(cond):  # start -> gateway(cond) -> end ; default -> end
    cond = cond.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return HDR + f'''<bpmn:startEvent id="s"/><bpmn:exclusiveGateway id="g" default="fd"/><bpmn:endEvent id="e1"/><bpmn:endEvent id="e2"/>
<bpmn:sequenceFlow id="f0" sourceRef="s" targetRef="g"/><bpmn:sequenceFlow id="fc" sourceRef="g" targetRef="e1"><bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{cond}</bpmn:conditionExpression></bpmn:sequenceFlow>
<bpmn:sequenceFlow id="fd" sourceRef="g" targetRef="e2"/>''' + FTR
def script_xml(script):
    script = script.replace("&", "&amp;").replace("<", "&lt;")
    return HDR + f'''<bpmn:startEvent id="s"/><bpmn:scriptTask id="st"><bpmn:script>{script}</bpmn:script></bpmn:scriptTask><bpmn:endEvent id="e"/>
<bpmn:sequenceFlow id="a" sourceRef="s" targetRef="st"/><bpmn:sequenceFlow id="b" sourceRef="st" targetRef="e"/>''' + FTR
def prescript_xml(script):
    return HDR + f'''<bpmn:startEvent id="s"/><bpmn:userTask id="u"><bpmn:extensionElements><spiffworkflow:preScript>{script}</spiffworkflow:preScript></bpmn:extensionElements></bpmn:userTask><bpmn:endEvent id="e"/>
<bpmn:sequenceFlow id="a" sourceRef="s" targetRef="u"/><bpmn:sequenceFlow id="b" sourceRef="u" targetRef="e"/>''' + FTR
def service_xml(op):
    return HDR + f'''<bpmn:startEvent id="s"/><bpmn:serviceTask id="sv"><bpmn:extensionElements><spiffworkflow:serviceTaskOperator id="{op}" resultVariable="r"><spiffworkflow:parameters><spiffworkflow:parameter id="x" type="str" value="'hi'"/></spiffworkflow:parameters></spiffworkflow:serviceTaskOperator></bpmn:extensionElements></bpmn:serviceTask><bpmn:endEvent id="e"/>
<bpmn:sequenceFlow id="a" sourceRef="s" targetRef="sv"/><bpmn:sequenceFlow id="b" sourceRef="sv" targetRef="e"/>''' + FTR
def timer_xml(expr):
    expr = expr.replace("&", "&amp;")
    return HDR + f'''<bpmn:startEvent id="s"/><bpmn:intermediateCatchEvent id="t"><bpmn:timerEventDefinition><bpmn:timeDuration xsi:type="bpmn:tFormalExpression">{expr}</bpmn:timeDuration></bpmn:timerEventDefinition></bpmn:intermediateCatchEvent><bpmn:endEvent id="e"/>
<bpmn:sequenceFlow id="a" sourceRef="s" targetRef="t"/><bpmn:sequenceFlow id="b" sourceRef="t" targetRef="e"/>''' + FTR

MARK = "/tmp/xt-spike-spiff/PWNED"
def attempt(label, xml, engine):
    if os.path.exists(MARK): os.remove(MARK)
    try:
        wf = new_instance(xml, "p", engine, {"amount": 1})
        out = "ĐÃ CHẠY (không lỗi)"
    except Exception as e:
        out = f"CHẶN: {type(e).__name__}: {str(e).splitlines()[0][:90]}"
    pwned = os.path.exists(MARK)
    print(f"  {'!!! PWNED' if pwned else 'safe    '} | {label:<34} | {out}")
    return pwned

ATT = {
 "gateway: os.system": ("cond", f"__import__('os').system('touch {MARK}')"),
 "gateway: object-graph escape": ("cond", f"[c for c in ().__class__.__base__.__subclasses__() if c.__name__=='Popen'][0](['touch','{MARK}']) and True"),
 "gateway: dunder attr no call": ("cond", "amount.__class__.__name__ == 'int'"),
 "gateway: lambda/walrus/fstring": ("cond", "(lambda: 1)() == 1"),
 "gateway: fstring": ("cond", "f'{amount}' == '1'"),
 "gateway: DoS list*1e9": ("cond", "len([0]*1000000000) > 0"),
 "gateway: legit amount>10tr": ("cond", "amount > 10000000"),
 "scriptTask: os.system": ("script", f"import os; os.system('touch {MARK}')"),
 "preScript (userTask ext)": ("pre", f"import os; os.system('touch {MARK}')"),
 "timer expr: __import__": ("timer", f"__import__('os').system('touch {MARK}') or 'PT1S'"),
 "serviceTask: op chưa đăng ký": ("svc", "rm_rf"),
 "serviceTask: op đã đăng ký": ("svc", "echo"),
}
BUILD = {"cond": cond_xml, "script": script_xml, "pre": prescript_xml, "timer": timer_xml, "svc": service_xml}
for name, mk in (("DEFAULT PythonScriptEngine (TaskDataEnvironment)", lambda: PythonScriptEngine()),
                 ("SAFE engine (xtflow.SafeEnvironment)", lambda: safe_engine({"echo": lambda x: {"echo": x}}))):
    print("###", name)
    for label, (kind, payload) in ATT.items():
        attempt(label, BUILD[kind](payload), mk())
