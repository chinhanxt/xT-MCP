// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { randomUUID } from 'node:crypto';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { discoverTools, callMcp } from '../src/mcp.ts';
import { Store } from '../src/store.ts';
test('stateful MCP preserves sessions across paginated inventory and calls, then sends DELETE', async () => {
  const sessions = new Map<string, { server: Server; transport: StreamableHTTPServerTransport }>();
  const calls: unknown[] = [];
  let deleted = 0;
  const http = createServer(async (req, res) => {
    const parts: Buffer[] = [];
    for await (const chunk of req) parts.push(chunk);
    const value = parts.length ? JSON.parse(Buffer.concat(parts).toString()) : undefined;
    const sessionId = String(req.headers['mcp-session-id'] ?? '');
    if (sessionId) {
      const session = sessions.get(sessionId);
      if (!session) {
        res.writeHead(404).end();
        return;
      }
      await session.transport.handleRequest(req, res, value);
      if (req.method === 'DELETE') {
        deleted++;
        sessions.delete(sessionId);
      }
      return;
    }
    if (value?.method !== 'initialize') {
      res.writeHead(400).end();
      return;
    }
    const server = new Server({ name: 'stateful-fixture', version: '1' }, { capabilities: { tools: {} } });
    server.setRequestHandler(ListToolsRequestSchema, async (request) => ({
      tools: [
        { name: request.params?.cursor ? 'second' : 'first', inputSchema: { type: 'object', properties: {}, additionalProperties: false } },
      ],
      ...(request.params?.cursor ? {} : { nextCursor: 'page2' }),
    }));
    server.setRequestHandler(CallToolRequestSchema, async (request) => {
      calls.push(request.params);
      return { content: [{ type: 'text', text: 'Stateful tool result' }] };
    });
    const transport: StreamableHTTPServerTransport = new StreamableHTTPServerTransport({
      sessionIdGenerator: randomUUID,
      onsessioninitialized: (id): void => {
        sessions.set(id, { server, transport });
      },
    });
    await server.connect(transport);
    await transport.handleRequest(req, res, value);
  });
  const dir = mkdtempSync(join(tmpdir(), 'xt-mcp-session-')),
    store = new Store(dir);
  try {
    await new Promise<void>((r) => http.listen(0, '127.0.0.1', r));
    const url = 'http://127.0.0.1:' + (http.address() as AddressInfo).port + '/mcp';
    const tools = await discoverTools({ url });
    assert.deepEqual(
      tools.map((t) => t.name),
      ['first', 'second'],
    );
    assert.equal(deleted, 1);
    store.db
      .prepare('INSERT INTO mcp_connectors VALUES(?,?,?,?,?,?,?)')
      .run('connector', 'workspace', 'Stateful', url, '', JSON.stringify(tools), 1);
    const result = await callMcp(
      store,
      'workspace',
      { connector_id: 'connector', tool_name: 'second', schema_hash: tools[1].schema_hash },
      {},
    );
    assert.match(result.output, /Stateful tool result/);
    assert.deepEqual(calls, [{ name: 'second', arguments: {} }]);
    assert.equal(deleted, 2);
    assert.equal(sessions.size, 0);
  } finally {
    await Promise.all(
      [...sessions.values()].map(async (s) => {
        await s.transport.close();
        await s.server.close();
      }),
    );
    await new Promise<void>((r) => http.close(() => r()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
