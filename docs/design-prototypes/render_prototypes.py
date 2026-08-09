import pathlib
from playwright.sync_api import sync_playwright

BASE = pathlib.Path(r"E:\Projects\WorkBuddy\AgentBoard\docs\design-prototypes")
TARGETS = {
    "epic-detail.html": "epic-detail.png",
    "story-detail.html": "story-detail.png",
    "task-detail.html": "task-detail.png",
    "doc-detail.html": "doc-detail.png",
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    for html_name, png_name in TARGETS.items():
        url = BASE.joinpath(html_name).as_uri()
        page.goto(url)
        page.wait_for_timeout(400)
        out = BASE.joinpath(png_name)
        page.screenshot(path=str(out), full_page=True)
        print(f"rendered {png_name} ({out.stat().st_size} bytes)")
    browser.close()
print("ALL DONE")
