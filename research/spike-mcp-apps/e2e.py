# SPDX-License-Identifier: Apache-2.0
import sys
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1000, "height": 900})
    logs = []; pg.on("console", lambda m: logs.append(f"[{m.type}] {m.text}"))
    pg.goto("http://localhost:18201/"); pg.wait_for_selector("select")
    def inner(): return pg.frame_locator("iframe").last.frame_locator("iframe")
    def call(tool):
        pg.select_option("select >> nth=1", tool); pg.click("button[type=submit]")
    call("show_org_chart"); inner().locator("g.node").first.wait_for(timeout=15000)
    pg.screenshot(path=f"{OUT}/1-orgchart.png")
    inner().locator("g.node[data-id='4']").click(); pg.wait_for_timeout(1500)
    print("DETAIL:", inner().locator("#detail").inner_text())
    pg.screenshot(path=f"{OUT}/2-orgchart-clicked.png")
    pg.goto("http://localhost:18201/"); pg.wait_for_selector("select"); pg.wait_for_timeout(1500); [i.click() for i in pg.locator("button:has-text(\"×\")").all()[:0]]; call("show_approval_card")
    inner().locator("#ok").wait_for(timeout=15000); pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/3-approval.png")
    inner().locator("#ok").click(); pg.wait_for_timeout(1500)
    print("STATUS:", inner().locator("#status").inner_text())
    pg.screenshot(path=f"{OUT}/4-approval-approved.png")
    print("\n".join(l for l in logs if "[HOST]" in l or "error" in l.lower())[:3000])
    b.close()
