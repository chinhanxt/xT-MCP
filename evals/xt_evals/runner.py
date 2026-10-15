# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chạy ma trận scenario x repeat x cấu hình (skills / profile) và chấm điểm."""
from __future__ import annotations

import asyncio
import hashlib
import json
import pathlib
import statistics
import sys
import time

import httpx

from . import checks
from .agent import LLM, mcp_session, run_turns, skills_for, system_prompt
from .mock.world import PEOPLE
from .oracles import compute

PEOPLE_MAP = {p[0]: {"name": p[2], "title": p[3]} for p in PEOPLE}
CONFIRM_RE = '"confirm_url": "'


class Admin:
    """Điều khiển phía server cho mock (reset, bấm xác nhận thay người, truy vấn trạng thái). Không có ở server thật."""

    def __init__(self, base: str | None):
        self.base = base.rstrip("/") if base else None
        self.http = httpx.Client(timeout=30) if base else None

    def available(self) -> bool:
        if not self.base:
            return False
        try:
            return self.http.get(self.base + "/admin/health").json().get("ok") is True
        except Exception:
            return False

    def post(self, path, body=None):
        return self.http.post(self.base + path, json=body or {}).json()

    def reset(self, seed):
        return self.post("/admin/reset", {"seed": seed})

    def hook(self, name):
        return self.post("/admin/confirm" if name == "confirm_all" else "/admin/decline")

    def query(self, model, where, ref):
        return self.post("/admin/query", {"model": model, "where": where, "ref": ref})["records"]


def grade(sc: dict, raw: dict, profile: str, admin: Admin | None, oracles: dict) -> dict:
    exp = sc["expected"]
    calls = raw["calls"]
    missing = checks.check_required(calls, exp, profile)
    if exp.get("no_calls") and calls:
        missing = missing + [{"index": -1, "step": {"tool": "(không gọi tool nào)"}}]
    forb = checks.check_forbidden(calls, exp, profile)
    ctx = {"oracles": oracles, "confirm_urls": [], "pii_tokens": []}
    import re
    for t in raw["tool_texts"]:
        ctx["confirm_urls"] += re.findall(r'"confirm_url": "([^"]+)"', t)
        ctx["pii_tokens"] += re.findall(r"\[PII:[0-9a-f]+\]", t)
    hard, soft = checks.check_answers(exp, raw["answers"], ctx, profile)
    st = checks.check_state(exp, admin.query if admin else None, profile)
    answer_ok = all(f["ok"] for f in hard)
    state_ok = all(s["ok"] is not False for s in st)
    ft = checks.first_tool_ok(calls, exp, profile)
    semantic = not missing and answer_ok and state_ok
    n = len(calls)
    return {
        "first_tool_ok": ft, "required_missing": missing, "forbidden": forb, "answer_checks": hard, "soft_checks": soft, "state_checks": st,
        "answer_ok": answer_ok, "state_ok": state_ok, "semantic_pass": semantic, "passed": semantic and not forb and not raw["step_limit"],
        "calls_total": n, "calls_valid": sum(c["arg_valid"] for c in calls), "calls_error": sum(not c["ok"] for c in calls),
        "hallucinated_tools": sum(c["hallucinated_tool"] for c in calls),
    }


async def run_one(sc, profile, skills_mode, args, llm, admin, tools_cache, oracles):
    seed = sc["seed"]
    if admin:
        admin.reset(seed)
    skills = skills_for(skills_mode, sc)
    t0 = time.perf_counter()
    err = None
    raw = {"calls": [], "answers": [], "tool_texts": [], "usage": {"prompt": 0, "completion": 0, "llm_calls": 0, "first_prompt": None}, "llm_time": 0, "step_limit": False}
    try:
        async with mcp_session(args.mcp_url, sc["actor"]) as s:
            if "tools" not in tools_cache:
                tools_cache["tools"] = (await s.list_tools()).tools
            hooks = (lambda name: asyncio.sleep(0, admin.hook(name))) if admin else (lambda name: asyncio.sleep(0))
            instr = getattr(s, "server_instructions", None) if getattr(args, "server_instructions", True) else None
            system = system_prompt(sc, PEOPLE_MAP, skills, instr)
            raw = await run_turns(llm, s, tools_cache["tools"], profile, sc, system, hooks, args.max_steps, getattr(args, "profile_tools", None))
    except Exception as e:  # noqa: BLE001  lỗi hạ tầng được ghi lại, không làm sập cả lượt chạy
        err = f"{type(e).__name__}: {e}"[:400]
    wall = time.perf_counter() - t0
    g = grade(sc, raw, profile, admin, oracles) if err is None else {"passed": False, "semantic_pass": False, "forbidden": [], "first_tool_ok": None,
                                                                     "calls_total": 0, "calls_valid": 0, "calls_error": 0, "answer_ok": False, "state_ok": False,
                                                                     "required_missing": [], "answer_checks": [], "soft_checks": [], "state_checks": [], "hallucinated_tools": 0}
    return {"id": sc["id"], "pack": sc["pack"], "category": sc["category"], "profile": profile, "skills": skills_mode,
            "skills_loaded": skills, "wall_s": round(wall, 2), "llm_s": raw["llm_time"], "error": err,
            "prompt_tokens": raw["usage"]["prompt"], "completion_tokens": raw["usage"]["completion"], "llm_calls": raw["usage"]["llm_calls"],
            "first_prompt_tokens": raw["usage"]["first_prompt"], "step_limit": raw["step_limit"],
            "calls": [{k: v for k, v in c.items()} for c in raw["calls"]], "answers": raw["answers"], **g}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    ft = [r["first_tool_ok"] for r in rows if r["first_tool_ok"] is not None]
    calls = sum(r["calls_total"] for r in rows)
    lat = sorted(r["wall_s"] for r in rows)
    ans = [r for r in rows if r["answer_checks"]]
    by_id: dict[str, list] = {}
    for r in rows:
        by_id.setdefault(r["id"], []).append(r["passed"])
    return {
        "runs": n, "scenarios": len(by_id),
        "pass_rate": sum(r["passed"] for r in rows) / n,
        "consistent_pass_rate": sum(all(v) for v in by_id.values()) / len(by_id),
        "tool_choice_accuracy": (sum(ft) / len(ft)) if ft else None, "tool_choice_n": len(ft),
        "arg_validity": (sum(r["calls_valid"] for r in rows) / calls) if calls else None, "calls": calls,
        "semantic_pass": sum(r["semantic_pass"] for r in rows) / n,
        "answer_pass": (sum(r["answer_ok"] for r in ans) / len(ans)) if ans else None, "answer_n": len(ans),
        "forbidden_rate": sum(bool(r["forbidden"]) for r in rows) / n,
        "forbidden_calls": sum(len(r["forbidden"]) for r in rows),
        "tool_error_rate": (sum(r["calls_error"] for r in rows) / calls) if calls else None,
        "errors": sum(bool(r["error"]) for r in rows), "step_limit": sum(r["step_limit"] for r in rows),
        "latency_mean_s": statistics.mean(lat), "latency_p50_s": statistics.median(lat), "latency_p95_s": lat[min(n - 1, int(0.95 * n))],
        "llm_calls_mean": statistics.mean(r["llm_calls"] for r in rows),
        "prompt_tokens_total": sum(r["prompt_tokens"] for r in rows), "completion_tokens_total": sum(r["completion_tokens"] for r in rows),
        "prompt_tokens_mean": statistics.mean(r["prompt_tokens"] for r in rows), "completion_tokens_mean": statistics.mean(r["completion_tokens"] for r in rows),
        "first_prompt_tokens_mean": statistics.mean(r["first_prompt_tokens"] or 0 for r in rows),
    }


async def run_all(args, scenarios, out_dir: pathlib.Path):
    admin = Admin(args.admin_url) if args.admin_url else None
    if admin and not admin.available():
        print("[warn] không có /admin trên server: bỏ reset/xác nhận/kiểm trạng thái (chế độ server thật)", file=sys.stderr)
        admin = None
    llm = LLM(args.llm_url, args.model, num_ctx=args.num_ctx, reasoning_effort=args.reasoning_effort or None, max_tokens=args.max_tokens, temperature=args.temperature)
    oracles = compute("binhminh")
    tools_cache: dict = {}
    configs = [c for c in args.skills.split("|")]
    rows, jl = [], (out_dir / "runs.jsonl").open("a", encoding="utf-8")
    plan = [(sc, r, c) for r in range(args.repeat) for c in configs for sc in scenarios if args.profile in sc["profiles"]]  # cùng cấu hình chạy liền nhau để tận dụng prefix cache
    t_start = time.time()
    for i, (sc, rep, cfg) in enumerate(plan, 1):
        row = await run_one(sc, args.profile, cfg, args, llm, admin, tools_cache, oracles)
        row["repeat"] = rep
        rows.append(row)
        jl.write(json.dumps(row, ensure_ascii=False) + "\n")
        jl.flush()
        flag = "PASS" if row["passed"] else "FAIL"
        extra = f" ERR {row['error']}" if row["error"] else (" forbidden=" + ",".join(v["tool"] for v in row["forbidden"]) if row["forbidden"] else "")
        print(f"[{i}/{len(plan)}] {flag} {sc['id']} skills={cfg} rep={rep} {row['wall_s']}s calls={row['calls_total']}{extra}", flush=True)
    await llm.close()
    tools = tools_cache.get("tools", [])
    digest = hashlib.sha256(json.dumps([(t.name, t.input_schema) for t in tools], sort_keys=True).encode()).hexdigest()[:12]
    meta = {"model": args.model, "llm_url": args.llm_url, "mcp_url": args.mcp_url, "profile": args.profile, "repeat": args.repeat,
            "num_ctx": args.num_ctx, "temperature": args.temperature, "reasoning_effort": args.reasoning_effort, "max_steps": args.max_steps,
            "server_tools": [t.name for t in tools], "catalog_digest": digest, "profile_tools": getattr(args, "profile_tools", None),
            "server_instructions": getattr(args, "server_instructions", True), "admin": bool(admin), "wall_total_s": round(time.time() - t_start, 1),
            "date": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": len(scenarios)}
    result = {"meta": meta, "configs": {c: summarize([r for r in rows if r["skills"] == c]) for c in configs}, "rows": rows}
    (out_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
