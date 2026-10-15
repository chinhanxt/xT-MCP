# SPDX-License-Identifier: Apache-2.0
import json
import chainlit as cl
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from openai import AsyncOpenAI

MCP_URL = "http://127.0.0.1:19100/mcp"
llm = AsyncOpenAI(base_url="http://127.0.0.1:11434/v1", api_key="x")
SYSTEM = "Bạn là trợ lý xT Desk. Dùng tool khi cần. Trả lời ngắn gọn bằng tiếng Việt."


async def with_mcp(fn):
    async with streamablehttp_client(MCP_URL) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return await fn(s)


def vis(t):
    return ((t.meta or {}).get("ui") or {}).get("visibility", ["model", "app"])


@cl.on_chat_start
async def start():
    cl.user_session.set("hist", [{"role": "system", "content": SYSTEM}])


@cl.action_callback("mcp_call")  # UI (iframe) -> tools/call
async def mcp_call(action: cl.Action):
    p = action.payload
    res = await with_mcp(lambda s: s.call_tool(p["name"], p.get("arguments") or {}))
    return {
        "content": [c.model_dump() for c in res.content],
        "structuredContent": res.structuredContent,
        "isError": res.isError,
    }


@cl.on_message
async def on_message(msg: cl.Message):
    hist = cl.user_session.get("hist")
    hist.append({"role": "user", "content": msg.content})
    tools = await with_mcp(lambda s: s.list_tools())
    spec = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema},
        }
        for t in tools.tools
        if "model" in vis(t)
    ]
    for _ in range(4):
        r = await llm.chat.completions.create(
            model="qwen3.5:4b", messages=hist, tools=spec, extra_body={"reasoning_effort": "none"}
        )
        m = r.choices[0].message
        hist.append(m.model_dump(exclude_none=True))
        if m.content:
            await cl.Message(content=m.content).send()
        if not m.tool_calls:
            break
        for tc in m.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            res = await with_mcp(lambda s: s.call_tool(tc.function.name, args))
            tool = next(t for t in tools.tools if t.name == tc.function.name)
            uri = ((tool.meta or {}).get("ui") or {}).get("resourceUri")
            if uri:
                html = (await with_mcp(lambda s: s.read_resource(uri))).contents[0].text
                el = cl.CustomElement(
                    name="McpApp",
                    props={
                        "html": html,
                        "toolName": tc.function.name,
                        "toolInput": args,
                        "toolResult": {
                            "content": [c.model_dump() for c in res.content],
                            "structuredContent": res.structuredContent,
                        },
                    },
                    display="inline",
                )
                await cl.Message(content=f"🔧 {tc.function.name}", elements=[el]).send()
            hist.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(
                        res.structuredContent or [c.model_dump() for c in res.content]
                    ),
                }
            )
