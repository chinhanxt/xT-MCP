// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { listenHost } from '../src/server.ts';

test('Studio listens on loopback unless HOST is set', () => {
  assert.equal(listenHost(undefined), '127.0.0.1');
  assert.equal(listenHost('  '), '127.0.0.1');
  assert.equal(listenHost('0.0.0.0'), '0.0.0.0');
  assert.equal(listenHost('::'), '::');
  assert.throws(() => listenHost('0.0.0.0; rm'), /HOST/);
});
