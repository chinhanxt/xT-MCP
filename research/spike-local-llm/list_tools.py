import asyncio,sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
async def main():
    async with streamable_http_client("http://127.0.0.1:18220/mcp") as (r,w,*_):
        async with ClientSession(r,w) as s:
            await s.initialize()
            for t in (await s.list_tools()).tools: print(t.name, t.meta)
asyncio.run(main())
