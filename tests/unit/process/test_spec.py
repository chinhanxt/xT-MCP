# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""G-P8: spec nghiêm ngặt (extra=forbid, lỗi tiếng Việt, JSON Schema) + giá trị độc trong spec."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
from lxml import etree

from tests.unit.process.support import FIX, OPS, PACK_NAMES, load_pack
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.adapters.workflow.bpmn.hardening import harden_bpmn
from xt.adapters.workflow.bpmn.spiff import load_specs
from xt.domain.process.errors import SpecError, UnsafeExpression
from xt.domain.process.expr import check_expr
from xt.domain.process.spec import json_schema


def errors_of(spec: Any, ops: dict[str, list[str]] = OPS) -> list[str]:
    with pytest.raises(SpecError) as e:
        compile_process(spec, ops)
    return e.value.errors


# ------------------------------------------------------------------ G-P8
def test_gp8_typo_slaa_rejected_not_silently_dropping_timer() -> None:
    spec = load_pack("giao_viec_thuong")
    spec["steps"][1]["slaa"] = spec["steps"][1].pop("sla")
    errs = errors_of(spec)
    assert len(errs) == 1
    assert errs[0].startswith("steps[1].slaa: khóa lạ 'slaa' không được phép (có phải 'sla'?)")


def test_gp8_unknown_keys_everywhere_rejected_in_vietnamese() -> None:
    spec = load_pack("de_xuat_phe_duyet")
    spec["parallel_with"] = ["kttt"]
    spec["steps"][1]["assignee"]["departmnt_of"] = "requester"
    spec["steps"][1]["sla"]["remind"] = "PT1H"
    spec["steps"][5]["on_return"]["max_round"] = 5
    spec["inputs"]["amount"]["min"] = 0
    joined = "\n".join(errors_of(spec))
    for frag in (
        "parallel_with: khóa lạ",
        "assignee.departmnt_of: khóa lạ 'departmnt_of'",
        "(có phải 'department_of'?)",
        "sla.remind: khóa lạ",
        "on_return.max_round: khóa lạ 'max_round' không được phép (có phải 'max_rounds'?)",
        "inputs.amount.min: khóa lạ",
    ):
        assert frag in joined, (frag, joined)


def test_gp8_when_uses_undeclared_variable_fails_at_compile() -> None:
    spec = load_pack("de_xuat_phe_duyet")
    spec["steps"][5]["when"] = "amout > 5000000"
    assert "biến chưa khai báo ['amout']" in errors_of(spec)[0]


def test_gp8_variable_from_later_step_or_sibling_branch_rejected() -> None:
    spec = load_pack("nv_offboarding")
    # chk_assets ở nhánh khác
    spec["steps"][2]["branches"][0][0]["when"] = "chk_assets_result['unreturned'] > 0"
    assert "chk_assets_result" in errors_of(spec)[0]


def test_gp8_original_v1_pack_specs_are_rejected_with_reasons() -> None:
    """6 spec gốc của packs-draft (v1) không lọt qua compiler v2."""
    out = {}
    for f in sorted((FIX / "v1_processes").glob("*.json")):
        with pytest.raises(SpecError) as e:
            compile_process(json.loads(f.read_text()), OPS)
        out[f.name] = e.value.errors
    assert len(out) == 6
    assert any("role: khóa lạ 'role'" in x for x in out["de_xuat_phe_duyet.json"])
    assert any(
        "trigger.cron: khóa lạ 'cron'" in x or "trigger: loại 'schedule'" in x
        for x in out["ban_tin_tuan.json"]
    )


def test_json_schema_export_is_strict_and_accepts_v2_packs() -> None:
    schema = json_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    objs = [d for d in schema["$defs"].values() if d.get("type") == "object"] + [schema]
    assert objs and all(o.get("additionalProperties") is False for o in objs)
    v = jsonschema.Draft202012Validator(schema)
    for n in PACK_NAMES:
        assert not list(v.iter_errors(load_pack(n))), n
    bad = load_pack("giao_viec_thuong")
    bad["steps"][1]["slaa"] = bad["steps"][1].pop("sla")
    assert any("slaa" in e.message for e in v.iter_errors(bad))


def test_all_v2_packs_compile_and_parse() -> None:
    for n in PACK_NAMES:
        c = compile_process(load_pack(n), OPS)
        spec, _ = load_specs(c.bpmn, c.process_id)
        assert spec.name == c.process_id


def test_dynamic_assignee_requires_fallback() -> None:
    spec = load_pack("nv_onboarding")
    del spec["fallback_assignee"]
    assert any("phải khai fallback_assignee" in e for e in errors_of(spec))


def test_return_for_revision_forbidden_inside_parallel_and_timer() -> None:
    spec = load_pack("nv_onboarding")
    spec["steps"][2]["branches"][0][0] = {
        "id": "it", "type": "approve", "name": "IT", "assignee": "role(it_admin)",
        "outcomes": ["approve", "return_for_revision"],
    }  # fmt: skip
    assert any("không dùng được trong nhánh song song" in e for e in errors_of(spec))
    t = load_pack("ban_tin_tuan")
    t["roles"] = ["director"]
    t["steps"].append(
        {"id": "gd", "type": "approve", "name": "GĐ", "assignee": "role(director)",
         "outcomes": ["approve", "return_for_revision"]}
    )  # fmt: skip
    assert any("không có người đề nghị" in e for e in errors_of(t))
    t["steps"][-1] = {"id": "gd", "type": "approve", "name": "GĐ", "assignee": "requester.manager"}
    assert any("phải là input kiểu user" in e for e in errors_of(t))


# ------------------------------------------------------------------ giá trị độc
def _svc(i: str, msg: str = "'x'") -> dict[str, Any]:
    return {"id": i, "type": "service", "name": i.upper(), "op": "notify", "params": {"msg": msg}}


MALICIOUS: dict[str, Callable[[dict[str, Any]], Any] | None] = {
    "role có ký tự XML": lambda s: s["roles"].append('x"/><bpmn:scriptTask id="evil'),
    "id process có chữ hoa/ký tự lạ": lambda s: s.update(id="Proc'><x"),
    "id bước homoglyph Cyrillic": lambda s: s["steps"][1].update(id="d\u0435pt"),
    "name có ký tự điều khiển": lambda s: s["steps"][1].update(name="TP\x00\x1b[2J"),
    "name quá dài": lambda s: s["steps"][1].update(name="x" * 201),
    "when gọi __import__": lambda s: s["steps"][5].update(when="__import__('os').system('id')"),
    "when lambda": lambda s: s["steps"][5].update(when="(lambda: 1)()"),
    "when thuộc tính dunder": lambda s: s["steps"][5].update(when="amount.__class__"),
    "when lũy thừa DoS": lambda s: s["steps"][5].update(when="9**9**9"),
    "when quá nhiều node": lambda s: s["steps"][5].update(when=" + ".join(["amount"] * 200)),
    "when ngoặc lồng sâu": lambda s: s["steps"][5].update(when="(" * 600 + "1" + ")" * 600),
    "param service độc": lambda s: s["steps"][6]["params"].update(
        amount="open('/etc/passwd').read()"
    ),
    "param thừa": lambda s: s["steps"][6]["params"].update(extra="amount"),
    "op ngoài allowlist": lambda s: s["steps"][6].update(op="os_system"),
    "SLA không phải ISO": lambda s: s["steps"][1]["sla"].update(after="'+__import__('os')+'"),
    "SLA quá dài": lambda s: s["steps"][1]["sla"].update(after="P999D"),
    "SLA bằng 0": lambda s: s["steps"][1]["sla"].update(after="PT0S"),
    "assignee là biểu thức": lambda s: s["steps"][2].update(assignee="os.system('id')"),
    "assignee chèn thêm lệnh": lambda s: s["steps"][2].update(
        assignee="role(director); drop table x"
    ),
    "assignee role lạ": lambda s: s["steps"][2].update(assignee="role(hacker)"),
    "assignee biến không khai": lambda s: s["steps"][2].update(assignee="user(ai_do)"),
    "assignee kind lạ": lambda s: s["steps"][2].update(
        assignee={"kind": "python", "user_var": "requester"}
    ),
    "outcome lạ": lambda s: s["steps"][2].update(outcomes=["approve", "approve_all"]),
    "thiếu approve": lambda s: s["steps"][2].update(outcomes=["reject"]),
    "type script": lambda s: s["steps"][2].update(type="script"),
    "input tên dành riêng": lambda s: s["inputs"].update(requester={"type": "user"}),
    "input hậu tố _decision": lambda s: s["inputs"].update(dept_head_decision={"type": "str"}),
    "distinct_from bước lạ": lambda s: s["steps"][4]["sod"].update(distinct_from=["khong_co"]),
    "to_step không phải bước người đứng trước": lambda s: s["steps"][5]["on_return"].update(
        to_step="ghiso", resume="restart"
    ),
    "parallel lồng parallel": lambda s: s["steps"].insert(
        1,
        {
            "id": "p",
            "type": "parallel",
            "name": "P",
            "branches": [
                [{"id": "p2", "type": "parallel", "name": "P2", "branches": [[], []]}],
                [_svc("z")],
            ],
        },
    ),
    "parallel 1 nhánh": lambda s: s["steps"].insert(
        1, {"id": "p", "type": "parallel", "name": "P", "branches": [[_svc("z")]]}
    ),
    "parallel mode any": lambda s: s["steps"].insert(
        1,
        {
            "id": "p",
            "type": "parallel",
            "name": "P",
            "mode": "any",
            "branches": [[_svc("z")], [_svc("y", "'y'")]],
        },
    ),
    "trigger rrule COUNT": lambda s: s.update(
        trigger={"kind": "timer", "rrule": "FREQ=MINUTELY;COUNT=99999", "tz": "Asia/Ho_Chi_Minh"}
    ),
    "trigger tz đường dẫn": lambda s: s.update(
        trigger={
            "kind": "timer",
            "rrule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
            "tz": "../../etc/passwd",
        }
    ),
    "trigger rrule chèn ;": lambda s: s.update(
        trigger={"kind": "timer", "rrule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0;X-EVIL=1", "tz": "UTC"}
    ),
    "version âm": lambda s: s.update(version=-1),
    "steps rỗng": lambda s: s.update(steps=[]),
    "quá 60 bước": lambda s: s.update(steps=[_svc(f"s{i}") for i in range(61)]),
    "id bước trùng": lambda s: s["steps"].append(copy.deepcopy(s["steps"][1])),
    "spec không phải object": None,
}


@pytest.mark.parametrize("label", list(MALICIOUS))
def test_malicious_spec_values_rejected(label: str) -> None:
    mutate = MALICIOUS[label]
    spec: Any
    if mutate is None:
        spec = ["not", "an", "object"]
    else:
        spec = load_pack("de_xuat_phe_duyet")
        mutate(spec)
    with pytest.raises(SpecError) as e:
        compile_process(spec, OPS)
    assert e.value.errors and all(isinstance(x, str) and x for x in e.value.errors)
    assert e.value.code == "rule_violation"


def test_vietnamese_name_with_xml_chars_is_escaped_not_injected() -> None:
    s = load_pack("de_xuat_phe_duyet")
    s["name"] = "Đề xuất \"A\" & <B> 'C'"
    s["steps"][1]["name"] = (
        '"/><bpmn:scriptTask id="evil"><bpmn:script>x</bpmn:script></bpmn:scriptTask><x a="'
    )
    c = compile_process(s, OPS)
    harden_bpmn(c.bpmn)  # không có scriptTask
    root = etree.fromstring(c.bpmn.encode())
    assert not root.xpath("//*[local-name()='scriptTask']")
    name = root.xpath("//*[local-name()='userTask'][@id='ut_dept_head']/@name")
    assert name[0] == s["steps"][1]["name"]


BPMN_NS = 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'


@pytest.mark.parametrize(
    "xml",
    [
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>',
        f'<bpmn:definitions {BPMN_NS}><bpmn:process id="p"><bpmn:scriptTask id="s"/>'
        "</bpmn:process></bpmn:definitions>",
        f'<bpmn:definitions {BPMN_NS} xmlns:s="http://spiffworkflow.org/bpmn/schema/1.0/core">'
        '<bpmn:process id="p"><bpmn:userTask id="u"><bpmn:extensionElements><s:preScript>'
        "import os</s:preScript></bpmn:extensionElements></bpmn:userTask></bpmn:process>"
        "</bpmn:definitions>",
    ],
)
def test_harden_rejects_doctype_scripttask_prescript(xml: str) -> None:
    with pytest.raises(UnsafeExpression):
        harden_bpmn(xml)


def test_check_expr_limits() -> None:
    check_expr("amount > 5000000 and kind in ('a', 'b')")
    for bad in ["x" * 501, "a" + "+a" * 130, "'" + "x" * 201 + "'", "b'x'", "f'{x}'"]:
        with pytest.raises(UnsafeExpression):
            check_expr(bad)


def test_published_json_schema_matches_code() -> None:
    """docs/schema/process-spec-v2.schema.json phải khớp model (chống drift). Sinh lại:
    uv run python -c "import json;from xt.domain.process.spec import json_schema;
    print(json.dumps(json_schema(), ensure_ascii=False, indent=2))" > docs/schema/..."""
    path = FIX.parents[2] / "docs" / "schema" / "process-spec-v2.schema.json"
    assert json.loads(path.read_text()) == json_schema()
