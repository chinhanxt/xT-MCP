# SPDX-License-Identifier: Apache-2.0
import asyncio, json
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
    async with streamable_http_client("http://127.0.0.1:18200/mcp") as (r, w, *_):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools: print("TOOL", t.name, json.dumps(t.meta))
            for x in (await s.list_resources()).resources: print("RES", x.uri, x.mime_type, x.meta)
            c = (await s.read_resource("ui://xt/org-chart.html")).contents[0]
            print("READ", c.uri, c.mime_type, len(c.text), c.text[:40].replace("\n"," "))
            r1 = await s.call_tool("show_org_chart", {}); print("CALL", json.dumps(r1.structured_content)[:120])
asyncio.run(main())
