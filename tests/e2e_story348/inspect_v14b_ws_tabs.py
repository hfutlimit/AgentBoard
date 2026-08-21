"""Run33 supplementary: warm recheck of the two workspace tabs (overview, kanban)
that inspect_v14_recheck.py does NOT cover, to reconcile v6's cold-start 0-char
P1 findings. Uses the same settle-measure (poll until stable) pattern.
"""
import json
import os
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:4200"
OUT = os.path.dirname(os.path.abspath(__file__))


def login():
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())["token"]


def settle(page, url, max_wait=20):
    page.goto(BASE + url, wait_until="domcontentloaded")
    page.wait_for_timeout(800)
    last = -1
    last_h1 = ""
    txt = 0
    h1 = ""
    for _ in range(int(max_wait / 0.5)):
        data = page.evaluate(
            "() => {const m=document.querySelector('main')||document.body;"
            "const h=document.querySelector('h1');"
            "return {t:(m?m.innerText:'').trim().length, h:h?h.innerText.trim():''};}"
        )
        txt = data["t"]
        h1 = data["h"]
        if txt == last and txt > 0:
            break
        last = txt
        last_h1 = h1
        page.wait_for_timeout(500)
    return txt, h1


def main():
    token = login()
    report = {"pages": []}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(BASE, wait_until="domcontentloaded")
        pg.wait_for_timeout(500)
        pg.evaluate("t=>localStorage.setItem('agentboard_token', t)", token)
        for url in ["/project/3", "/project/3/kanban"]:
            txt, h1 = settle(pg, url)
            report["pages"].append({"url": url, "txt": txt, "h1": h1,
                                    "blank": txt < 30, "note": "warm settle recheck"})
            print(f"[RECHECK] {url} txt={txt} h1={h1!r} blank={txt<30}")
        b.close()
    with open(os.path.join(OUT, "report_run33_ws_tabs.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
