import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { randomBytes } from 'node:crypto';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
export async function liveMcpFixture() {
  const token = randomBytes(24).toString('hex');
  const calls: { name: string; arguments: unknown }[] = [];
  const http = createServer(async (req, res) => {
    if (req.headers.authorization !== 'Bearer ' + token) {
      res.writeHead(401).end();
      return;
    }
    if (req.method !== 'POST') {
      res.writeHead(405).end();
      return;
    }
    const parts: Buffer[] = [];
    for await (const p of req) parts.push(p);
    const server = new Server({ name: 'Company demo MCP', version: '1.0.0' }, { capabilities: { tools: {} } });
    server.setRequestHandler(ListToolsRequestSchema, async () => ({
      tools: [
        {
          name: 'company_policy',
          description: 'Read the company advance payment policy and its maximum amount in VND. Call to get current rules.',
          inputSchema: { type: 'object', properties: {}, additionalProperties: false },
        },
      ],
    }));
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
      calls.push({ name: request.params.name, arguments: request.params.arguments });
      return { content: [{ type: 'text', text: JSON.stringify({ company: 'Sao Mai', policy: 'SM-2026-Q4', max_advance_vnd: 7500000 }) }] };
    });
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.once('close', () => {
      void transport.close();
      void server.close();
    });
    try {
      await server.connect(transport);
      await transport.handleRequest(req, res, JSON.parse(Buffer.concat(parts).toString()));
    } catch {
      if (!res.headersSent) res.writeHead(500).end();
    }
  });
  await new Promise<void>((r) => http.listen(0, '127.0.0.1', r));
  return {
    url: 'http://127.0.0.1:' + (http.address() as AddressInfo).port + '/mcp',
    token,
    calls,
    close: () => new Promise<void>((r) => http.close(() => r())),
  };
}
