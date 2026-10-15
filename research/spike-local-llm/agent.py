# SPDX-License-Identifier: Apache-2.0
"""xT Desk core (spike): vòng lặp agent tối thiểu = mcp client + Ollama OpenAI-compatible API."""
import asyncio, json, sys, time, argparse
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from openai import AsyncOpenAI

SYS = ("Bạn là trợ lý của hệ thống xT. Trả lời bằng tiếng Việt, ngắn gọn. "
       "Dùng tool để lấy dữ liệu thật, không bịa. Khi cần dữ liệu hoặc thao tác, hãy gọi tool.")

def visible(t):  # host phải ẩn tool visibility=["app"] khỏi model
    ui = (t.meta or {}).get("ui", {})
    return "model" in ui.get("visibility", ["model", "app"])

async def run(prompt, model, url, think, max_steps=6):
    cli = AsyncOpenAI(base_url="http://127.0.0.1:11434/v1", api_key="ollama")
    out = {"prompt": prompt, "calls": [], "answer": None, "steps": 0}
    t0 = time.time()
    async with streamable_http_client(url) as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                      "parameters": t.input_schema}} for t in (await s.list_tools()).tools if visible(t)]
            msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": prompt}]
            for step in range(max_steps):
                out["steps"] = step + 1
                resp = await cli.chat.completions.create(model=model, messages=msgs, tools=tools,
                        temperature=0.2, reasoning_effort=("medium" if think else "none"), extra_body={"options": {"num_ctx": 8192}})
                m = resp.choices[0].message
                msgs.append(m.model_dump(exclude_none=True))
                if not m.tool_calls:
                    out["answer"] = m.content; break
                for tc in m.tool_calls:
                    try: args = json.loads(tc.function.arguments or "{}")
                    except Exception: args = {"_raw": tc.function.arguments}
                    res = await s.call_tool(tc.function.name, args)
                    txt = "".join(c.text for c in res.content if hasattr(c, "text"))
                    out["calls"].append({"tool": tc.function.name, "args": args, "isError": res.is_error})
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": txt})
    await cli.close()
    out["latency_s"] = round(time.time() - t0, 1)
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt"); ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--url", default="http://127.0.0.1:18220/mcp"); ap.add_argument("--think", action="store_true")
    ap.add_argument("-n", type=int, default=1)
    a = ap.parse_args()
    async def main():
        for i in range(a.n):
            print(json.dumps(await run(a.prompt, a.model, a.url, a.think), ensure_ascii=False), flush=True)
    asyncio.run(main())
