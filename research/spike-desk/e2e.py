# SPDX-License-Identifier: Apache-2.0
import sys, time, json
from playwright.sync_api import sync_playwright

url, out, tag = sys.argv[1], sys.argv[2], sys.argv[3]
sel_box, sel_send = sys.argv[4], sys.argv[5]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1000, "height": 900})
    pg.on("console", lambda m: print("CONSOLE", m.type, m.text[:200]))
    pg.goto(url)
    pg.wait_for_selector(sel_box, timeout=30000)
    time.sleep(2)
    pg.screenshot(path=f"{out}/{tag}-1-start.png")
    t0 = time.time()
    pg.fill(sel_box, "Cho tôi xem sơ đồ tổ chức")
    pg.click(sel_send)
    fl = None
    for _ in range(90):
        fl = [f for f in pg.frames if f.url.startswith("about:srcdoc") or "sandbox" in f.url]
        found = None
        for f in pg.frames:
            try:
                if f.query_selector("g.node"):
                    found = f
                    break
            except Exception:
                pass
        if found:
            break
        time.sleep(1)
    print("RENDER_SECONDS", round(time.time() - t0, 1), "frame ok:", bool(found))
    if tag == "A":  # Chainlit remount iframe khi tin nhắn cuối tới: đợi LLM xong
        pg.wait_for_function("!document.querySelector('#stop-button')", timeout=60000)
        time.sleep(2)
        found = next(f for f in pg.frames if f.query_selector("g.node"))
    time.sleep(1)
    pg.screenshot(path=f"{out}/{tag}-2-orgchart-inline.png")
    if found:
        found.click("g.node[data-id='4']")
        time.sleep(2)
        print("DETAIL:", found.inner_text("#detail"))
    pg.screenshot(path=f"{out}/{tag}-3-clicked.png")
    b.close()
