// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { setTimeout as delay } from 'node:timers/promises';
import type { GraphNode } from './contracts.ts';
export async function withNodePolicy(node: GraphNode, execute: () => Promise<void>, fallback: () => void, signal?: AbortSignal) {
  const retries = node.data.retries ?? 0;
  let attempts = 0;
  for (;;) {
    signal?.throwIfAborted();
    attempts++;
    try {
      await execute();
      return { attempts, used_fallback: false };
    } catch (error) {
      if (signal?.aborted) throw error;
      if (attempts <= retries) {
        await delay(100 * attempts, undefined, { signal });
        continue;
      }
      if (node.data.error_mode === 'fallback') {
        fallback();
        return { attempts, used_fallback: true };
      }
      throw error;
    }
  }
}
