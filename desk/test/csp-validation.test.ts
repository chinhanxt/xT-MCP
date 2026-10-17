// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { expect, it, vi } from "vitest";
import { createAppBridgeHandlers, createGlobalToolLimiter, createRateLimiter } from "../src/bridge";

it("keeps strict App argument validation when dynamic code execution is disabled", async () => {
  const execute = vi.fn(async () => ({ content: [] }));
  const original = globalThis.Function;
  globalThis.Function = function () { throw new Error("CSP: unsafe-eval forbidden"); } as unknown as FunctionConstructor;
  try {
    const handlers = createAppBridgeHandlers({
      tools: [{ name: "app_read", inputSchema: {
        type: "object", properties: { revision: { type: "integer", minimum: 1 } }, required: ["revision"],
      }, _meta: { ui: { visibility: ["app"] } } }],
      callTool: execute, readResource: async () => ({ contents: [] }),
      limiter: createRateLimiter({ ratePerSec: 10, burst: 10, maxInflight: 4 }),
      globalLimiter: createGlobalToolLimiter({ ratePerSec: 10, burst: 10 }), maxArgsBytes: 32768,
    });
    await handlers.onCallTool({ name: "app_read", arguments: { revision: 1 } });
    for (const args of [{}, { revision: "1" }, { revision: 0 }, { revision: 1, extra: true }]) {
      await expect(handlers.onCallTool({ name: "app_read", arguments: args })).rejects.toThrow();
    }
    expect(execute).toHaveBeenCalledTimes(1);
  } finally {
    globalThis.Function = original;
  }
});
