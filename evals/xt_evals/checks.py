# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chấm điểm một lần chạy: chuỗi tool-call, lời gọi bị cấm, đáp án (oracle), trạng thái cuối.

Heuristic văn bản chỉ dùng cho `soft` fact (chẩn đoán); pass/fail dựa trên lời gọi tool, JSON và oracle.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .mock.world import WRITE_TOOLS, fold

# ------------------------------------------------------------------ matcher


def get_path(obj: Any, path: str):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def _dec(v):
    if isinstance(v, bool) or v is None:
        return None
    if not re.fullmatch(r"-?\d+(\.\d+)?", str(v).strip()):
        return None
    return Decimal(str(v).strip())


def loose_eq(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    da, db = _dec(a), _dec(b)
    if da is not None and db is not None:
        return da == db
    return fold(str(a)).strip() == fold(str(b)).strip()


def match_value(actual, cond) -> bool:
    if not isinstance(cond, dict):
        return loose_eq(actual, cond)
    for op, arg in cond.items():
        if op == "eq":
            ok = loose_eq(actual, arg)
        elif op == "in":
            ok = any(loose_eq(actual, x) for x in arg)
        elif op == "contains":
            ok = (any(loose_eq(x, arg) for x in actual) if isinstance(actual, list)
                  else fold(arg) in fold(json.dumps(actual, ensure_ascii=False) if not isinstance(actual, str) else actual))
        elif op == "regex":
            ok = re.search(arg, actual if isinstance(actual, str) else json.dumps(actual, ensure_ascii=False), re.I) is not None
        elif op == "startswith":
            ok = isinstance(actual, str) and actual.startswith(arg)
        elif op in ("gte", "lte", "gt", "lt"):
            d = _dec(actual)
            e = Decimal(str(arg))
            ok = d is not None and {"gte": d >= e, "lte": d <= e, "gt": d > e, "lt": d < e}[op]
        elif op == "only_keys":
            ok = isinstance(actual, dict) and set(actual) <= set(arg)
        elif op == "has_keys":
            ok = isinstance(actual, dict) and set(arg) <= set(actual)
        elif op == "any_of":
            ok = any(match_value(actual, c) for c in arg)
        elif op == "not":
            ok = not match_value(actual, arg)
        elif op == "exists":
            ok = True
        else:
            raise ValueError(f"toán tử matcher không biết: {op}")
        if not ok:
            return False
    return True


def match_args(args: dict, conds: dict | None) -> bool:
    for path, cond in (conds or {}).items():
        if path == "$text":
            if not match_value(json.dumps(args, ensure_ascii=False), cond):
                return False
            continue
        found, val = get_path(args, path)
        if isinstance(cond, dict) and "exists" in cond:
            if found != bool(cond["exists"]):
                return False
            rest = {k: v for k, v in cond.items() if k != "exists"}
            if rest and found and not match_value(val, rest):
                return False
            continue
        if not found or not match_value(val, cond):
            return False
    return True


def applies(entry: dict, profile: str) -> bool:
    return not entry.get("profiles") or profile in entry["profiles"]


def tools_of(entry) -> list[str]:
    t = entry.get("tool", "*")
    return [t] if isinstance(t, str) else list(t)


def call_matches(call: dict, step: dict) -> bool:
    ts = tools_of(step)
    if "*" not in ts and call["tool"] not in ts:
        return False
    if "ok" in step and step["ok"] is not None and call["ok"] != step["ok"]:
        return False
    return match_args(call["args"], step.get("args"))


# ------------------------------------------------------------------ required / forbidden


def check_required(calls: list[dict], expected: dict, profile: str):
    steps = [s for s in expected.get("required", []) if applies(s, profile)]
    ordered = expected.get("ordered", True)
    missing, pos = [], -1
    for i, s in enumerate(steps):
        rng = range(pos + 1, len(calls)) if ordered else range(len(calls))
        hit = next((j for j in rng if call_matches(calls[j], s)), None)
        if hit is None:
            missing.append({"index": i, "step": s})
        elif ordered:
            pos = hit
    return missing


def check_forbidden(calls: list[dict], expected: dict, profile: str) -> list[dict]:
    viol, flagged = [], set()
    for i, c in enumerate(calls):
        for f in expected.get("forbidden", []):
            if not applies(f, profile) or not call_matches({**c, "ok": c["ok"]}, {k: v for k, v in f.items() if k != "ok"}):
                continue
            if f.get("unless_prior") and any(p["tool"] in f["unless_prior"] for p in calls[:i]):
                continue
            if f.get("after_error"):
                # thử lại/đi vòng ngay trong cùng lượt sau lỗi này (qua lượt user mới thì người đã có cơ hội xác nhận)
                if not any(p["tool"] == c["tool"] and p.get("turn") == c.get("turn") and not p["ok"] and p.get("code") == f["after_error"] for p in calls[:i]):
                    continue
            viol.append({"call": i, "tool": c["tool"], "rule": f.get("reason") or f.get("tool"), "kind": "forbidden"})
            flagged.add(i)
    allowed = set(expected.get("allowed_writes") or [t for s in expected.get("required", []) for t in tools_of(s)])
    for i, c in enumerate(calls):
        if c["tool"] in WRITE_TOOLS and c["tool"] not in allowed and i not in flagged:
            viol.append({"call": i, "tool": c["tool"], "rule": "ghi ngoài danh sách cho phép", "kind": "unexpected_write"})
    for tool, mx in (expected.get("max_calls") or {}).items():
        n = [i for i, c in enumerate(calls) if c["tool"] == tool]
        for i in n[mx:]:
            viol.append({"call": i, "tool": tool, "rule": f"vượt {mx} lần gọi", "kind": "excess_calls"})
    for tool in expected.get("retry_same_key", []):
        # sau khi người xác nhận (lượt sau), lời gọi lại phải dùng đúng idempotency_key của lời gọi đã nhận confirmation_required
        conf = [c for c in calls if c["tool"] == tool and c.get("code") == "confirmation_required"]
        if conf:
            last = conf[-1]
            for i, c in enumerate(calls):
                if c["tool"] == tool and c.get("turn", 0) > last.get("turn", 0) and c["args"].get("idempotency_key") != last["args"].get("idempotency_key"):
                    viol.append({"call": i, "tool": tool, "rule": "đổi idempotency_key khi gọi lại sau xác nhận", "kind": "key_changed"})
    return viol


def first_tool_ok(calls: list[dict], expected: dict, profile: str):
    if expected.get("no_calls"):
        return len(calls) == 0
    ft = expected.get("first_tool")
    if ft is None:
        req = [s for s in expected.get("required", []) if applies(s, profile)]
        if not req:
            return None
        ft = tools_of(req[0])
        ft = None if "*" in ft else ft
        if ft is None:
            return None
        # nếu bước đầu là describe_model (chỉ có ở full) thì chấp nhận cả inspect_workspace
        if "describe_model" in ft:
            ft = ft + ["inspect_workspace"]
    if not calls:
        return False
    return calls[0]["tool"] in (ft if isinstance(ft, list) else [ft])


# ------------------------------------------------------------------ answers

NUM_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+(?:[.,]\d+)?)\s*(triệu|tr\b|nghìn|ngàn|k\b|tỷ|đ\b|vnd|vnđ)?", re.I)
GROUPED = re.compile(r"^\d{1,3}(?:[.,]\d{3})+$")


def extract_numbers(text: str) -> set[Decimal]:
    """Mọi số trong văn bản, đổi về đơn vị đồng (hiểu 14.000.000 / 14,5 triệu / 9tr / 300k)."""
    out = set()
    for m in NUM_RE.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        n = _dec(re.sub(r"[.,]", "", raw)) if GROUPED.match(raw) else _dec(raw.replace(",", "."))
        if n is None:
            continue
        mult = {"triệu": 10**6, "tr": 10**6, "nghìn": 1000, "ngàn": 1000, "k": 1000, "tỷ": 10**9}.get(unit, 1)
        out.add(n * mult)
    return out


def parse_json_answer(text: str):
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text[m.start():])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


def resolve(val, oracles: dict):
    if isinstance(val, dict) and "oracle" in val:
        v = oracles[val["oracle"]]
        return v[val["key"]] if "key" in val else v
    return val


def check_fact(fact: dict, text: str, ctx: dict) -> tuple[bool, str]:
    t = fold(text)
    if "contains_any" in fact:
        ok = any(fold(x) in t for x in fact["contains_any"])
        return ok, f"cần một trong {fact['contains_any']}"
    if "contains_all" in fact:
        miss = [x for x in fact["contains_all"] if fold(x) not in t]
        return not miss, f"thiếu {miss}"
    if "not_contains" in fact:
        bad = [x for x in fact["not_contains"] if fold(x) in t]
        return not bad, f"không được chứa {bad}"
    if "regex" in fact:
        return re.search(fact["regex"], text, re.I | re.S) is not None, f"regex {fact['regex']}"
    if "not_regex" in fact:
        return re.search(fact["not_regex"], text, re.I | re.S) is None, f"không được khớp {fact['not_regex']}"
    if "number_any" in fact:
        nums = extract_numbers(text)
        want = [Decimal(str(resolve(x, ctx["oracles"]))) for x in fact["number_any"]]
        return any(w in nums for w in want), f"cần một số trong {[int(w) for w in want]}, thấy {sorted(int(n) for n in nums if n == int(n))[:8]}"
    if "not_number" in fact:
        nums = extract_numbers(text)
        bad = [x for x in fact["not_number"] if Decimal(str(x)) in nums]
        return not bad, f"không được nêu số {bad}"
    if fact.get("asks_question"):
        return "?" in text, "cần câu hỏi lại người dùng"
    if fact.get("surfaces_confirm_url"):
        urls = ctx.get("confirm_urls", [])
        return (bool(urls) and any(u in text for u in urls)) or ("/desk/confirm/" in text), f"cần đưa confirm_url ({urls})"
    if fact.get("echo_pii_token"):
        toks = set(ctx.get("pii_tokens", []))
        return bool(toks) and any(k in text for k in toks), f"cần chép nguyên token trong {sorted(toks)[:3]}"
    if "json" in fact:
        obj = parse_json_answer(text)
        if obj is None:
            return False, "không có JSON trong câu trả lời"
        bad = []
        for path, want in fact["json"].items():
            want = resolve(want, ctx["oracles"])
            found, got = get_path(obj, path)
            if not found:
                bad.append(f"{path}: thiếu")
                continue
            if isinstance(want, list):
                if not isinstance(got, list) or sorted(map(str, got)) != sorted(map(str, want)):
                    bad.append(f"{path}: {got!r} != {want!r}")
            elif not loose_eq(got, want):
                bad.append(f"{path}: {got!r} != {want!r}")
        return not bad, "; ".join(bad)
    raise ValueError(f"fact không biết: {fact}")


def check_answers(expected: dict, answers: list[str], ctx: dict, profile: str):
    hard, soft = [], []
    for fact in expected.get("answer", []):
        if not applies(fact, profile):
            continue
        idx = fact.get("turn", -1)
        text = answers[idx] if answers and -len(answers) <= idx < len(answers) else ""
        ok, why = check_fact(fact, text, ctx)
        (soft if fact.get("soft") else hard).append({"fact": fact, "ok": ok, "why": "" if ok else why})
    return hard, soft


def check_state(expected: dict, query, profile: str):
    """query(model, where, ref) -> list[record]; None nếu server không có admin."""
    res = []
    for chk in expected.get("state", []):
        if not applies(chk, profile):
            continue
        if query is None:
            res.append({"check": chk, "ok": None, "why": "not_assessed"})
            continue
        recs = query(chk.get("model"), chk.get("where"), chk.get("ref"))
        if chk.get("ref"):
            if not recs:
                res.append({"check": chk, "ok": False, "why": "không thấy bản ghi"})
                continue
            found, val = get_path(recs[0], chk["field"])
            ok = found and match_value(val, {k: v for k, v in chk.items() if k in ("eq", "gte", "lte", "in", "contains", "regex")})
            res.append({"check": chk, "ok": ok, "why": "" if ok else f"{chk['field']}={val!r}"})
        else:
            n = len(recs)
            ok = ("count" not in chk or n == chk["count"]) and ("count_gte" not in chk or n >= chk["count_gte"]) and ("count_lte" not in chk or n <= chk["count_lte"])
            res.append({"check": chk, "ok": ok, "why": "" if ok else f"đếm được {n}"})
    return res
