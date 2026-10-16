# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit DPoP vòng 2, DPA-03 trên IndexedDB THẬT (Chromium headless, không đụng display):
`desk/src/dpop.ts` được strip type bằng Node (`module.stripTypeScriptTypes`) rồi nạp vào trang.
Khóa non-extractable, `reset()` xóa khỏi IndexedDB, người sau nhận jkt mới; và ca đua DPA-08."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration
DESK = Path(__file__).resolve().parents[3] / "desk"

BUILD = r"""
const {stripTypeScriptTypes}=require('node:module');const fs=require('fs');
let out=stripTypeScriptTypes(fs.readFileSync(process.argv[1],'utf8'),{mode:'transform'});
out=out.replace(/^export /gm,'');
fs.writeFileSync(process.argv[2],
 'var D=(function(){'+out+';return {DpopSigner,indexedDbKeyStore,memoryKeyStore};})();');
"""

RUN = r"""
import { createRequire } from "module";
import { readFileSync } from "fs";
import http from "http";
const require = createRequire(process.argv[2] + "/package.json");
const { chromium } = require("playwright");
const js = readFileSync(process.argv[3], "utf8");
const srv = http.createServer((q, r) => { r.setHeader("content-type", "text/html");
  r.end("<html></html>"); }).listen(0, "127.0.0.1");
await new Promise((r) => srv.on("listening", r));
const url = `http://localhost:${srv.address().port}/`;
const b = await chromium.launch({ headless: true });
try {
  const ctx = await b.newContext();
  const open = async () => { const p = await ctx.newPage(); await p.goto(url);
    await p.addScriptTag({ content: js }); return p; };
  let p = await open();
  const first = await p.evaluate(async () => {
    const s = new D.DpopSigner({ store: D.indexedDbKeyStore() });
    const jkt = await s.thumbprint();
    const raw = await D.indexedDbKeyStore().get();
    let exportable = true;
    try { await crypto.subtle.exportKey("jwk", raw.privateKey); } catch { exportable = false; }
    await s.reset();
    return { jkt, extractable: raw.privateKey.extractable, exportable,
             gone: (await D.indexedDbKeyStore().get()) === undefined };
  });
  await p.close(); p = await open();  // người dùng sau, cùng profile
  const next = await p.evaluate(async () =>
    new D.DpopSigner({ store: D.indexedDbKeyStore() }).thumbprint());
  const race = await p.evaluate(async () => {
    await D.indexedDbKeyStore().clear();
    const s = new D.DpopSigner({ store: D.indexedDbKeyStore() });
    const inflight = s.thumbprint();  // sinh khóa đang chạy
    await s.reset();                  // đăng xuất đúng lúc đó
    const old = await inflight;
    const after = await new D.DpopSigner({ store: D.indexedDbKeyStore() }).thumbprint();
    return { old, after };
  });
  console.log(JSON.stringify({ first, next, race }));
} finally { await b.close(); srv.close(); }
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None or not (DESK / "node_modules/playwright").exists():
        pytest.fail("cần node + desk/node_modules (npm ci) + chromium Playwright")
    d = tmp_path_factory.mktemp("dpop_desk")
    js, run = d / "dpop.js", d / "run.mjs"
    subprocess.run(
        ["node", "-e", BUILD, str(DESK / "src/dpop.ts"), str(js)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    run.write_text(RUN)
    out = subprocess.run(
        ["node", str(run), str(DESK), str(js)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def test_key_non_extractable_and_removed_on_logout(result: dict[str, Any]) -> None:
    f = result["first"]
    assert f["extractable"] is False and f["exportable"] is False
    assert f["gone"] is True
    assert result["next"] != f["jkt"]  # người sau cùng profile: jkt mới


@pytest.mark.xfail(
    strict=True,
    reason="DPA-08: reset() trong lúc sinh khóa lần đầu: put() của lượt cũ ghi lại khóa cũ "
    "vào IndexedDB sau clear(), người sau dùng lại khóa đó",
)
def test_logout_during_key_generation_does_not_resurrect_key(result: dict[str, Any]) -> None:
    assert result["race"]["after"] != result["race"]["old"]
