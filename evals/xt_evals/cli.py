# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""CLI: python -m xt_evals {validate,list,run,report}."""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import signal
import subprocess
import sys
import time

import httpx

from . import report, scenario

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="xt_evals", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="kiểm schema mọi scenario")
    ls = sub.add_parser("list", help="liệt kê scenario")
    ls.add_argument("--only", nargs="*")
    r = sub.add_parser("run", help="chạy eval")
    r.add_argument("--mcp-url", default=None, help="URL MCP Streamable HTTP (mặc định: tự bật mock ở --mock-port)")
    r.add_argument("--admin-url", default=None, help="gốc URL có /admin (mặc định: cùng origin với --mcp-url; tự tắt nếu không có)")
    r.add_argument("--mock-port", type=int, default=20180)
    r.add_argument("--llm-url", default="http://127.0.0.1:11434/v1")
    r.add_argument("--model", default="qwen3.5:4b")
    r.add_argument("--profile", choices=["core-10", "full"], default="core-10")
    r.add_argument("--skills", default="none|auto", help="danh sách cấu hình, ngăn bằng '|': none | auto (xt-core + skill của scenario) | all | tên,tên")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--num-ctx", type=int, default=0, help="gửi options.num_ctx; CHỈ có tác dụng với API native của ollama, trên /v1 bị bỏ qua (dùng OLLAMA_CONTEXT_LENGTH). 0 = không gửi")
    r.add_argument("--reasoning-effort", default="none", help="'' để không gửi")
    r.add_argument("--temperature", type=float, default=0.0, help="0 = xác định (lặp lại gần như giống hệt); >0 để đo độ biến thiên giữa các lần lặp")
    r.add_argument("--max-tokens", type=int, default=1024)
    r.add_argument("--max-steps", type=int, default=8, help="số vòng gọi LLM tối đa mỗi lượt user")
    r.add_argument("--only", nargs="*", help="lọc theo id con, pack hoặc category")
    r.add_argument("--tools", default="", help="thay danh sách tool của hồ sơ core-10 (ngăn bằng dấu phẩy), để đo biến thể hồ sơ")
    r.add_argument("--no-server-instructions", dest="server_instructions", action="store_false",
                   help="không đưa `instructions` của server vào system prompt (mặc định có, như client MCP thật)")
    r.add_argument("--out", default=None, help="thư mục kết quả (mặc định results/<thời gian>)")
    rp = sub.add_parser("report", help="sinh lại report.md từ results.json")
    rp.add_argument("dir")
    a = ap.parse_args(argv)

    if a.cmd == "validate":
        sc = scenario.load_all()
        packs = {}
        for s in sc:
            packs[s["pack"]] = packs.get(s["pack"], 0) + 1
        print(f"OK: {len(sc)} scenario hợp lệ", packs)
        return 0
    if a.cmd == "list":
        for s in scenario.load_all(only=a.only):
            print(f"{s['id']:48} {s['category']:13} actor={s['actor']:6} turns={len(s['turns'])} profiles={','.join(s['profiles'])}")
        return 0
    if a.cmd == "report":
        report.write(pathlib.Path(a.dir))
        print(pathlib.Path(a.dir) / "report.md")
        return 0

    from .agent import CORE10
    from .runner import run_all
    scs = scenario.load_all(only=a.only)
    a.profile_tools = [x for x in a.tools.split(",") if x] or (list(CORE10) if a.profile == "core-10" else None)
    proc = None
    if not a.mcp_url:
        proc = subprocess.Popen([sys.executable, "-m", "xt_evals.mock", "--port", str(a.mock_port),
                                 "--profile-tools", ",".join(a.profile_tools or [])], cwd=ROOT)
        a.mcp_url = f"http://127.0.0.1:{a.mock_port}/mcp"
        for _ in range(50):
            try:
                if httpx.get(f"http://127.0.0.1:{a.mock_port}/admin/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.2)
    if a.admin_url is None:
        from urllib.parse import urlparse
        u = urlparse(a.mcp_url)
        a.admin_url = f"{u.scheme}://{u.netloc}"
    out = pathlib.Path(a.out) if a.out else ROOT / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    try:
        res = asyncio.run(run_all(a, scs, out))
    finally:
        if proc:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=10)
    report.write(out)
    print(f"\nKết quả: {out}/results.json, {out}/report.md")
    for c, s in res["configs"].items():
        print(f"  [{c}] pass {s['pass_rate']:.0%} | tool {s['tool_choice_accuracy']} | args {s['arg_validity']} | forbidden {s['forbidden_rate']:.0%} | {s['latency_mean_s']:.1f}s")
    return 0
