# SPDX-License-Identifier: Apache-2.0
"""Validate every schema with jsonschema Draft202012Validator, run examples/negatives, derive annotations, measure sizes."""
import copy
import json
import sys

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

import contracts as C

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def minify(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def nbytes(o):
    return len(minify(o).encode("utf-8"))


def compact(schema):
    s = copy.deepcopy(schema)

    def walk(x):
        if isinstance(x, dict):
            for k in ("$schema", "$id", "default", "format", "uniqueItems"):
                x.pop(k, None)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(s)
    return s


COMBINATORS = {"allOf", "anyOf", "oneOf", "if", "then", "else", "not", "$ref", "dependentSchemas", "dependentRequired"}


def missing_descriptions(schema, path="$"):
    """Every property of every object in an INPUT schema must have a description."""
    out = []
    if isinstance(schema, dict):
        for name, sub in (schema.get("properties") or {}).items():
            if "description" not in sub:
                out.append(f"{path}.{name}")
            out += missing_descriptions(sub, f"{path}.{name}")
        for key in ("items", "additionalProperties"):
            if isinstance(schema.get(key), dict):
                out += missing_descriptions(schema[key], f"{path}[{key}]")
        for key in ("anyOf",):
            for i, sub in enumerate(schema.get(key) or []):
                out += missing_descriptions(sub, f"{path}.{key}[{i}]")
    return out


def strict_objects(schema, path="$"):
    """Every object in an INPUT schema is closed (no silent unknown params)."""
    bad = []
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema and schema.get("additionalProperties") is not False:
            bad.append(path)
        for name, sub in (schema.get("properties") or {}).items():
            bad += strict_objects(sub, f"{path}.{name}")
        for key in ("items", "additionalProperties"):
            if isinstance(schema.get(key), dict):
                bad += strict_objects(schema[key], f"{path}[{key}]")
        for i, sub in enumerate(schema.get("anyOf") or []):
            bad += strict_objects(sub, f"{path}.anyOf[{i}]")
    return bad


codes = {c for c, _, _ in C.ERROR_CODES}
for sch, name in ((C.ERROR_ENVELOPE, "error_envelope"), (C.CONFIRMATION_DETAILS, "confirmation_details")):
    try:
        Draft202012Validator.check_schema(sch)
    except SchemaError as e:
        FAIL.append(f"{name}: {e.message}")
ev = Draft202012Validator(C.ERROR_ENVELOPE)
cv = Draft202012Validator(C.CONFIRMATION_DETAILS)
for i, e in enumerate(C.SAMPLE_ERRORS):
    errs = list(ev.iter_errors(e))
    check(not errs, f"sample error {i}: {[x.message for x in errs]}")
check(not list(cv.iter_errors(C.SAMPLE_ERRORS[0]["error"]["details"])), "confirmation details sample invalid")

rows = []
catalog = []
for t in C.T:
    n = t["name"]
    inp, data = t["input_schema"], t["output_schema"]
    env = C.success_envelope(data)
    for sch, label in ((inp, "input"), (env, "output")):
        try:
            Draft202012Validator.check_schema(sch)
        except SchemaError as e:
            FAIL.append(f"{n} {label}: {e.message}")
    check(inp.get("type") == "object" and not (COMBINATORS & set(inp)), f"{n}: root combinator or non-object input")
    md = missing_descriptions(inp)
    check(not md, f"{n}: properties without description: {md}")
    so = strict_objects(inp)
    check(not so, f"{n}: open input objects: {so}")
    check(all(c in codes for c in t["errors"]), f"{n}: unknown error code")
    iv = Draft202012Validator(inp, format_checker=Draft202012Validator.FORMAT_CHECKER)
    for ex in t["examples"]:
        errs = list(iv.iter_errors(ex))
        check(not errs, f"{n}: example invalid {ex}: {[x.message for x in errs]}")
        # compact profile must still accept every example (REA rule)
        check(not list(Draft202012Validator(compact(inp)).iter_errors(ex)), f"{n}: compact rejects example")
    sample = {"ok": True, "untrusted": True, "data": t["sample_output"],
              "meta": {"contract_version": "1.0.0", "trace_id": "tr_x", "as_of": "2026-11-15T10:00:00Z",
                       "effective_level": t["max_autonomy"] if t["max_autonomy"] != "L0" else "L0"}}
    if t["max_autonomy"] != "L0":
        sample["meta"]["receipt"] = {"operation_ref": "operation:01jd", "idempotency_key": "k" * 8, "replayed": False, "commit_state": "committed"}
    errs = list(Draft202012Validator(env).iter_errors(sample))
    check(not errs, f"{n}: sample output invalid: {[x.message + ' @' + str(list(x.path)) for x in errs]}")
    # forward compatibility: unknown key inside data accepted, unknown key in envelope rejected
    s2 = copy.deepcopy(sample); s2["data"]["added_in_v0_2"] = 1
    check(not list(Draft202012Validator(env).iter_errors(s2)), f"{n}: data not open")
    s3 = copy.deepcopy(sample); s3["extra"] = 1
    check(list(Draft202012Validator(env).iter_errors(s3)), f"{n}: envelope not strict")
    # unknown input param rejected
    check(list(iv.iter_errors({**t["examples"][0], "approved": True})), f"{n}: accepts unknown param")
    ann = C.annotations(t["effects"])
    t["annotations"] = ann
    adv_tool = {"name": n, "title": n.replace("_", " ").capitalize(), "description": t["description"],
                "inputSchema": inp, "annotations": ann}
    rows.append({
        "name": n, "since": t["since"], "level": "human-only" if t.get("human_only") else t["max_autonomy"],
        "in": nbytes(inp), "in_compact": nbytes(compact(inp)), "data": nbytes(data), "out_env": nbytes(env),
        "desc_chars": len(t["description"]), "props": len(inp["properties"]), "required": len(inp["required"]),
        "list_entry": nbytes(compact(adv_tool) | {"inputSchema": compact(inp)}),
        "list_entry_with_out": nbytes(adv_tool | {"inputSchema": compact(inp), "outputSchema": compact(env)}),
        **{k: v for k, v in ann.items()},
    })
    catalog.append({**adv_tool, "inputSchema": inp, "outputSchema": env,
                    "_contract": {k: t[k] for k in ("since", "max_autonomy", "effects", "iam", "errors", "ui_resource", "returns_untrusted_content")}
                    | {"human_only": bool(t.get("human_only"))}})

# --- negative cases (things the reconciled contract must reject)
by = {t["name"]: t for t in C.T}
NEG = [
    ("create_record", {"model_id": "advance_request", "values": [{"field": "amount", "value": "8000000"}], "idempotency_key": "abcdefgh"}, "array {field,value} payload"),
    ("create_record", {"model_id": "advance_request", "values": {"Amount": "1"}, "idempotency_key": "abcdefgh"}, "bad field name"),
    ("create_record", {"model_id": "advance_request", "values": {"amount": {"nested": 1}}, "idempotency_key": "abcdefgh"}, "nested object value"),
    ("create_record", {"model_id": "advance_request", "values": {"amount": "1"}}, "missing idempotency_key"),
    ("update_record", {"ref": "task:01jd2q", "set": {}, "expected_revision": 1, "idempotency_key": "abcdefgh"}, "empty set"),
    ("search_records", {"model_id": "x", "where": {"amount": {"between": [1, 2]}}}, "unknown operator"),
    ("search_records", {"model_id": "x", "where": [{"field": "amount", "operator": "eq", "value": 1}]}, "array filter"),
    ("get_record", {"ref": "xrn:t_demo:advance_request:*"}, "wildcard ref"),
    ("aggregate_records", {"model_id": "x", "metrics": ["sum"]}, "sum without field"),
    ("aggregate_records", {"model_id": "x", "group_by": ["a", "b", "c", "d"]}, "4 group_by"),
    ("aggregate_records", {"model_id": "x", "group_by": ["created_at:month"]}, "bucket inside group_by"),
    ("aggregate_records", {"model_id": "x", "period": "11/2026"}, "free-form period"),
    ("decide_request", {"ref": "advance_request:a", "decision": "approve", "expected_revision": 1, "idempotency_key": "abcdefgh", "confirmation_ref": "cf_x"}, "agent-supplied confirmation"),
    ("decide_request", {"ref": "advance_request:a", "decision": "approve", "expected_revision": 1, "idempotency_key": "abcdefgh", "approved": True}, "approved=true"),
    ("create_task", {"title": "x", "assignee": "Bình", "idempotency_key": "abcdefgh"}, "assignee by name"),
    ("create_task", {"title": "x", "assignee": "me", "recurrence": "every monday", "idempotency_key": "abcdefgh"}, "free-text recurrence"),
    ("upload_file", {"name": "a.pdf", "source": "url", "idempotency_key": "abcdefgh"}, "unknown source"),
    ("propose_model_change", {"model_id": "m", "operations": [{"op": "drop_table"}], "rationale": "x", "idempotency_key": "abcdefgh"}, "unknown op"),
    ("propose_model_change", {"model_id": "m", "operations": [{"op": "add_field", "field": {"id": "x; drop", "label": "x", "type": "string"}}], "rationale": "x", "idempotency_key": "abcdefgh"}, "injection in field id"),
    ("transition_record", {"ref": "task:a", "to_state": "done", "expected_revision": 0, "idempotency_key": "abcdefgh"}, "revision 0"),
]
neg_rows = []
for tool, args, why in NEG:
    rejected = bool(list(Draft202012Validator(by[tool]["input_schema"]).iter_errors(args)))
    check(rejected, f"negative not rejected: {tool} {why}")
    neg_rows.append((tool, why, rejected))


# duplicate JSON keys: schema cannot see them (parsers keep the last); server must reject at parse time
def no_dupes(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


dup_text = '{"model_id":"advance_request","values":{"amount":"1","amount":"9000000"},"idempotency_key":"abcdefgh"}'
plain = json.loads(dup_text)
check(not list(Draft202012Validator(by["create_record"]["input_schema"]).iter_errors(plain)), "dup demo: schema unexpectedly rejects")
try:
    json.loads(dup_text, object_pairs_hook=no_dupes)
    dup_ok = False
except ValueError:
    dup_ok = True
check(dup_ok, "duplicate key hook failed")

# annotation sanity per decisions
ann = {t["name"]: t["annotations"] for t in C.T}
v01 = [t for t in C.T if t["since"] == "0.1"]
check(len(v01) <= 17, f"v0.1 has {len(v01)} tools")
check(not any(ann[t["name"]]["destructiveHint"] for t in v01), "v0.1 has destructive tool")
check(not any(ann[t["name"]]["openWorldHint"] for t in C.T), "openWorld tool exists")
check(ann["delete_record"]["destructiveHint"], "delete_record must be destructive")

json.dump({"catalog": catalog, "error_envelope": C.ERROR_ENVELOPE, "confirmation_details": C.CONFIRMATION_DETAILS},
          open("catalog.json", "w"), ensure_ascii=False, indent=2)
json.dump({"rows": rows, "neg": neg_rows, "dup_rejected_by_hook": dup_ok, "dup_passes_schema": True,
           "error_envelope_bytes": nbytes(C.ERROR_ENVELOPE), "confirmation_details_bytes": nbytes(C.CONFIRMATION_DETAILS)},
          open("sizes.json", "w"), ensure_ascii=False, indent=1)

hdr = "| Tool | since | level | in | in compact | data | out (envelope) | tools/list entry | +outputSchema | desc chars | props/req | RO | D | I | OW |"
print(hdr)
print("|" + "---|" * 15)
for r in rows:
    print(f"| `{r['name']}` | {r['since']} | {r['level']} | {r['in']} | {r['in_compact']} | {r['data']} | {r['out_env']} | {r['list_entry']} | {r['list_entry_with_out']} | {r['desc_chars']} | {r['props']}/{r['required']} | "
          + " | ".join("T" if r[k] else "F" for k in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")) + " |")


def tot(sel, key):
    return sum(r[key] for r in rows if sel(r))


for label, sel in (("v0.1", lambda r: r["since"] == "0.1"), ("v1.0 (all)", lambda r: True),
                   ("core profile", lambda r: r["name"] in C.CORE_PROFILE)):
    print(f"TOTAL {label}: n={sum(1 for r in rows if sel(r))} in={tot(sel,'in')} in_compact={tot(sel,'in_compact')} "
          f"data={tot(sel,'data')} out_env={tot(sel,'out_env')} tools_list={tot(sel,'list_entry')} tools_list+out={tot(sel,'list_entry_with_out')}")
print("max input", max(r["in"] for r in rows), "budget 13312")
print("error envelope", nbytes(C.ERROR_ENVELOPE), "confirmation details", nbytes(C.CONFIRMATION_DETAILS))
print("negatives rejected", sum(1 for x in neg_rows if x[2]), "/", len(neg_rows))
print("FAILURES:", len(FAIL))
for f in FAIL:
    print("  -", f)
sys.exit(1 if FAIL else 0)
