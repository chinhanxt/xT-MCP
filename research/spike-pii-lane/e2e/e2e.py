# SPDX-License-Identifier: Apache-2.0
"""E2E headless (Playwright) trên basic-host: thẻ hiện token -> bấm 'Xem' -> iframe fetch thẳng xT.

Ghi lại MỌI message postMessage giữa iframe và host + mọi request mạng, để chứng minh giá trị thật
chỉ đi trên kết nối iframe -> xT (/pii/reveal), không qua host.
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))
from conftest import raw_corpus  # noqa: E402

OUT = pathlib.Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
SPY = """
window.__xt_msgs = [];
window.addEventListener('message', e => { try { window.__xt_msgs.push(JSON.stringify(e.data)); } catch (_) {} }, true);
"""
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1000, "height": 900})
    ctx.add_init_script(SPY)                       # chạy trong MỌI frame (host, sandbox proxy, app)
    pg = ctx.new_page()
    net, logs = [], []
    ctx.on("request", lambda r: net.append({"url": r.url, "method": r.method, "origin": r.headers.get("origin"),
                                            "body": r.post_data}))
    resp_bodies = []
    def on_resp(r):
        if r.url.startswith("http://localhost:18210/") or r.url.startswith("http://localhost:18211/"):
            try: resp_bodies.append({"url": r.url, "body": r.text()})
            except Exception: pass
    ctx.on("response", on_resp)
    pg.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"))
    pg.goto("http://localhost:18211/"); pg.wait_for_selector("select")
    pg.select_option("select >> nth=1", "get_record")
    pg.fill("textarea", json.dumps({"model": "nhan_vien", "key": "NV-0042"}))
    pg.click("button[type=submit]")
    inner = pg.frame_locator("iframe").last.frame_locator("iframe")
    inner.locator("#reveal").wait_for(timeout=20000)
    pg.wait_for_timeout(500)
    before = inner.locator("#t").inner_text()
    pg.screenshot(path=str(OUT / "1-card-tokens.png"))
    inner.locator("#reveal").click()
    try:
        inner.locator("td.real").first.wait_for(timeout=15000)
    except Exception:
        print("STATUS:", inner.locator("#s").inner_text()); print("\n".join(logs[-30:])); raise
    pg.wait_for_timeout(500)
    after = inner.locator("#t").inner_text()
    pg.screenshot(path=str(OUT / "2-card-revealed.png"))
    # gom postMessage của mọi frame
    msgs = []
    for fr in pg.frames:
        try: msgs += fr.evaluate("window.__xt_msgs || []")
        except Exception: pass
    b.close()

corpus = raw_corpus()
def leaks(s): return sorted({v for v in corpus if v.lower() in s.lower()})
host_traffic = [r for r in resp_bodies if "/pii/reveal" not in r["url"]]
reveal = [r for r in resp_bodies if "/pii/reveal" in r["url"]]
print("BEFORE (iframe):", before.replace("\n", " | ")[:400])
print("AFTER  (iframe):", after.replace("\n", " | ")[:400])
print("REVEAL requests:", [(n["method"], n["url"], n["origin"]) for n in net if "/pii/reveal" in n["url"]])
print("Leak trong postMessage (host<->iframe):", leaks("\n".join(msgs)), f"({len(msgs)} msg)")
print("Leak trong traffic host<->MCP server:", leaks(json.dumps(host_traffic, ensure_ascii=False)), f"({len(host_traffic)} resp)")
print("Giá trị thật trong response /pii/reveal:", leaks(json.dumps(reveal, ensure_ascii=False)))
(OUT / "e2e-postmessages.jsonl").write_text("\n".join(msgs))
(OUT / "e2e-console.log").write_text("\n".join(logs))
