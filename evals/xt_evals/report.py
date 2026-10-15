# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Sinh báo cáo Markdown (tiếng Việt) từ results.json."""
from __future__ import annotations

import json
import pathlib

from .runner import summarize


def pct(x, n=None):
    return "-" if x is None else f"{100 * x:.0f}%"


def f1(x):
    return "-" if x is None else f"{x:.1f}"


def render(res: dict) -> str:
    m, rows, cfgs = res["meta"], res["rows"], list(res["configs"])
    name = lambda c: "không skill" if c == "none" else f"skills={c}"  # noqa: E731
    L = [f"# Báo cáo eval agent xT ({m['date']})", "",
         f"- Model: `{m['model']}` qua `{m['llm_url']}`, num_ctx={m['num_ctx']}, reasoning_effort={m['reasoning_effort']}, temperature {m.get('temperature', 0)}",
         f"- MCP: `{m['mcp_url']}` ({'mock xác định, có admin' if m['admin'] else 'server không có admin: không reset/kiểm trạng thái'}), catalog digest `{m['catalog_digest']}`, {len(m['server_tools'])} tool",
         f"- Profile: `{m['profile']}`, {m['scenarios']} scenario, repeat {m['repeat']}, tổng thời gian {m['wall_total_s']} s", ""]
    L += ["## Tổng hợp", "", "| Chỉ số | " + " | ".join(name(c) for c in cfgs) + " |", "|---|" + "---|" * len(cfgs)]
    S = [res["configs"][c] for c in cfgs]
    spec = [("Pass tổng (đúng tool + đáp án + không vi phạm)", lambda s: pct(s["pass_rate"])),
            ("Pass nhất quán (mọi lần lặp đều pass)", lambda s: pct(s["consistent_pass_rate"])),
            ("Chọn tool đúng (lời gọi đầu)", lambda s: f"{pct(s['tool_choice_accuracy'])} (n={s['tool_choice_n']})"),
            ("Tham số hợp schema", lambda s: f"{pct(s['arg_validity'])} ({s['calls']} lời gọi)"),
            ("Semantic pass (chuỗi bắt buộc + đáp án + trạng thái)", lambda s: pct(s["semantic_pass"])),
            ("Đáp án đúng (oracle)", lambda s: f"{pct(s['answer_pass'])} (n={s['answer_n']})"),
            ("Tỉ lệ scenario có lời gọi bị cấm", lambda s: f"{pct(s['forbidden_rate'])} ({s['forbidden_calls']} lời gọi)"),
            ("Tool trả lỗi / lời gọi", lambda s: pct(s["tool_error_rate"])),
            ("Lỗi hạ tầng / chạm giới hạn bước", lambda s: f"{s['errors']} / {s['step_limit']}"),
            ("Độ trễ mỗi scenario (TB / p50 / p95, giây)", lambda s: f"{f1(s['latency_mean_s'])} / {f1(s['latency_p50_s'])} / {f1(s['latency_p95_s'])}"),
            ("Lượt gọi LLM TB / scenario", lambda s: f1(s["llm_calls_mean"])),
            ("Token prompt (tổng / TB mỗi scenario)", lambda s: f"{s['prompt_tokens_total']:,} / {s['prompt_tokens_mean']:.0f}"),
            ("Token completion (tổng / TB)", lambda s: f"{s['completion_tokens_total']:,} / {s['completion_tokens_mean']:.0f}"),
            ("Prompt token của lượt gọi đầu (TB)", lambda s: f"{s['first_prompt_tokens_mean']:.0f}")]
    for label, fn in spec:
        L.append(f"| {label} | " + " | ".join(fn(s) if s else "-" for s in S) + " |")
    for key, title in (("pack", "Theo pack"), ("category", "Theo loại")):
        L += ["", f"## {title}", "", "| " + key + " | n | " + " | ".join(f"pass ({name(c)})" for c in cfgs) + " | " + " | ".join(f"cấm ({name(c)})" for c in cfgs) + " |",
              "|---|---|" + "---|" * (2 * len(cfgs))]
        for v in sorted({r[key] for r in rows}):
            sub = {c: summarize([r for r in rows if r[key] == v and r["skills"] == c]) for c in cfgs}
            L.append(f"| {v} | {sub[cfgs[0]].get('runs', 0)} | " + " | ".join(pct(sub[c].get("pass_rate")) for c in cfgs) + " | " + " | ".join(pct(sub[c].get("forbidden_rate")) for c in cfgs) + " |")
    ids = sorted({r["id"] for r in rows})
    L += ["", "## Từng scenario", "", "P = pass, F = fail (số lần pass/số lần chạy nếu lặp), `!` = có lời gọi bị cấm", "", "| scenario | " + " | ".join(name(c) for c in cfgs) + " | ghi chú lỗi (cấu hình cuối) |", "|---|" + "---|" * (len(cfgs) + 1)]
    for i in ids:
        cells, note = [], ""
        for c in cfgs:
            rs = [r for r in rows if r["id"] == i and r["skills"] == c]
            ok = sum(r["passed"] for r in rs)
            cells.append(("P" if ok == len(rs) else "F" if ok == 0 else f"{ok}/{len(rs)}") + (" !" if any(r["forbidden"] for r in rs) else "") if rs else "-")
            bad = next((r for r in rs if not r["passed"]), None)
            if bad:
                note = _why(bad)
        L.append(f"| {i} | " + " | ".join(cells) + f" | {note} |")
    L += ["", "## Định nghĩa chỉ số", "",
          "- **Chọn tool đúng**: lời gọi tool đầu tiên thuộc tập cho phép của scenario (hoặc 0 lời gọi với scenario âm).",
          "- **Tham số hợp schema**: tỉ lệ lời gọi có arguments qua `jsonschema` Draft 2020-12 với inputSchema server quảng bá.",
          "- **Semantic pass**: mọi bước `required` xuất hiện đúng thứ tự (kèm điều kiện tham số), đáp án qua oracle, trạng thái cuối đúng.",
          "- **Lời gọi bị cấm**: khớp `forbidden`, hoặc ghi ngoài danh sách cho phép (`unexpected_write`), vượt số lần gọi, đổi idempotency_key khi thử lại.",
          "- **Pass tổng** = semantic pass và không có lời gọi bị cấm và không chạm giới hạn bước.", ""]
    return "\n".join(L)


def _why(r: dict) -> str:
    if r.get("error"):
        return "lỗi: " + r["error"][:80]
    if r["forbidden"]:
        v = r["forbidden"][0]
        return f"cấm: {v['tool']} ({v['rule']})"[:90]
    if r["required_missing"]:
        s = r["required_missing"][0]["step"]
        return "thiếu bước: " + json.dumps(s, ensure_ascii=False)[:80]
    bad = next((a for a in r["answer_checks"] if not a["ok"]), None)
    if bad:
        return "đáp án: " + bad["why"][:80]
    bad = next((a for a in r["state_checks"] if a["ok"] is False), None)
    if bad:
        return "trạng thái: " + bad["why"][:60]
    return "step_limit" if r.get("step_limit") else ""


def write(out_dir: pathlib.Path):
    res = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    (out_dir / "report.md").write_text(render(res), encoding="utf-8")
