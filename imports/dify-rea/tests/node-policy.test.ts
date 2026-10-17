// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { withNodePolicy } from '../src/node-policy.ts';
import type { GraphNode } from '../src/contracts.ts';
import { emptyConfig } from '../src/contracts.ts';
import { configInput } from '../src/validation.ts';
import { runWorkflow } from '../src/workflow.ts';
import { Store } from '../src/store.ts';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
const node: GraphNode = {
  id: 'http',
  type: 'http',
  position: { x: 0, y: 0 },
  data: { label: 'HTTP', retries: 2, error_mode: 'fallback', fallback: 'unavailable' },
};
test('node policy retries the declared number, recovers, and records fallback after final failure', async () => {
  let count = 0,
    fallbacks = 0;
  const recovered = await withNodePolicy(
    node,
    async () => {
      if (++count === 1) throw new Error('transient');
    },
    () => {
      fallbacks++;
    },
  );
  assert.deepEqual(recovered, { attempts: 2, used_fallback: false });
  assert.equal(fallbacks, 0);
  count = 0;
  const fallback = await withNodePolicy(
    node,
    async () => {
      count++;
      throw new Error('failure');
    },
    () => {
      fallbacks++;
    },
  );
  assert.deepEqual(fallback, { attempts: 3, used_fallback: true });
  assert.equal(count, 3);
  assert.equal(fallbacks, 1);
  await assert.rejects(
    () =>
      withNodePolicy(
        { ...node, data: { label: 'HTTP' } },
        async () => {
          throw new Error('stop');
        },
        () => {},
      ),
    /stop/,
  );
});
test('cancellation prevents retries and fallback', async () => {
  const controller = new AbortController();
  let attempts = 0,
    fallbacks = 0;
  await assert.rejects(
    () =>
      withNodePolicy(
        node,
        async () => {
          attempts++;
          controller.abort();
          throw new Error('cancel');
        },
        () => {
          fallbacks++;
        },
        controller.signal,
      ),
    /cancel/,
  );
  assert.equal(attempts, 1);
  assert.equal(fallbacks, 0);
});
test('workflow traces the fallback value and rejects invalid policies at config boundary', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-policy-')),
    store = new Store(dir);
  try {
    const config = emptyConfig();
    config.graph.nodes[1].data.error_mode = 'fallback';
    config.graph.nodes[1].data.fallback = 'Không gọi được AI: {{input}}';
    const result = await runWorkflow(store, 'w', config, 'hello');
    assert.equal(result.output, 'Không gọi được AI: hello');
    assert.equal(result.trace[1].used_fallback, true);
    const bad = emptyConfig();
    bad.graph.nodes[1].data.retries = 5;
    assert.throws(() => configInput(bad), /0–3/);
    bad.graph.nodes[1].data.retries = 1;
    bad.graph.nodes[1].type = 'template';
    assert.throws(() => configInput(bad), /chỉ áp dụng/);
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
