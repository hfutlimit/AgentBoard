import sys
import urllib.request
import json
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8099"
API = "http://127.0.0.1:58125"
results = []
errors = []

def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))

def api_call(method, path, body=None, token=None):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json",
                                           **({"Authorization": f"Bearer {token}"} if token else {})},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

# Obtain a token: register (open local API) or login if exists
_uname, _pass = "e2e_doc_user", "e2epass123"
st, payload = api_call("POST", "/api/auth/register", {"username": _uname, "password": _pass})
if st not in (200, 201):
    st, payload = api_call("POST", "/api/auth/login", {"username": _uname, "password": _pass})
TOKEN = payload.get("token")
if not TOKEN:
    print("FATAL: could not obtain auth token:", st, payload)
    sys.exit(2)
print(f"auth ok (user {_uname}), token len={len(TOKEN)}")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    # Inject token so the SPA does not redirect to /login
    page.add_init_script(
        f"localStorage.setItem('agentboard_token', '{TOKEN}');"
        f"localStorage.setItem('agentboard_user', '{_uname}');"
    )
    console_errors = []
    page_errors = []
    failed_resources = []

    def on_console(msg):
        if msg.type == "error":
            console_errors.append(msg.text)
    def on_pageerror(err):
        page_errors.append(str(err))
    def on_request_failed(req):
        url = req.url
        # Ignore external CDN (e.g. mermaid) — offline degradation is by design
        if "127.0.0.1" not in url and "localhost" not in url:
            return
        if url.endswith(".js") or url.endswith(".css"):
            failed_resources.append(url)
    page.on("console", on_console)
    page.on("pageerror", on_pageerror)
    page.on("requestfailed", on_request_failed)

    # 1) Documents list
    page.goto(f"{BASE}/documents", wait_until="networkidle")
    page.wait_for_selector("text=项目文档", timeout=10000)
    check("documents list renders heading", page.locator("h2", has_text="项目文档").count() > 0)
    check("新建文档 button present", page.get_by_role("button", name="＋ 新建文档").count() > 0)
    check("existing doc 'Epic 15 实施计划' listed",
          page.locator("a.doc-row", has_text="Epic 15 实施计划").count() > 0)

    # 2) Open existing doc (has markdown + mermaid)
    page.locator("a.doc-row", has_text="Epic 15 实施计划").first.click()
    page.wait_for_selector(".doc-content", timeout=10000)
    h1 = page.locator(".doc-content h1").first
    check("markdown h1 rendered", h1.count() > 0 and "计划" in (h1.inner_text() or ""))
    strong = page.locator(".doc-content strong").first
    check("markdown bold rendered", strong.count() > 0 and "加粗" in (strong.inner_text() or ""))
    check("mermaid block present", page.locator("pre.mermaid").count() > 0)

    # 3) Add a comment (multi-agent collaboration)
    page.locator(".comment-form input[name='author']").fill("Agent-A")
    page.locator(".comment-form textarea[name='content']").fill("评审意见：计划可行，建议补充风险章节。")
    page.locator(".comment-form button[type='submit']").click()
    page.wait_for_selector(".comment-item:has-text('评审意见')", timeout=8000)
    check("comment added and rendered", page.locator(".comment-item", has_text="评审意见").count() > 0)

    # 4) Create a new document (exercises create modal + API)
    page.goto(f"{BASE}/documents", wait_until="networkidle")
    page.get_by_role("button", name="新建文档").click()
    page.wait_for_selector("form.doc-create", timeout=5000)
    page.locator("form.doc-create input[maxlength='300']").fill("集成测试文档")
    page.locator("form.doc-create textarea").fill("# 标题\n正文 **重点**\n\n- 项一\n- 项二")
    # ensure project select has a value (default first project)
    page.locator("form.doc-create button[type='submit']").click()
    # should navigate to detail
    page.wait_for_selector(".doc-content", timeout=8000)
    check("new document created + navigated to detail", "集成测试文档" in (page.locator("h2").first.inner_text() or ""))
    # verify markdown list rendered
    check("new doc markdown list rendered", page.locator(".doc-content ul li").count() >= 2)

    # 5) Status transition: draft -> in_review -> approved
    # newly created doc is draft by default
    check("new doc starts as draft badge", page.locator(".docstatus--draft").count() > 0)
    page.get_by_role("button", name="提交评审").click()
    page.wait_for_selector(".docstatus--in_review", timeout=6000)
    check("draft -> in_review", page.locator(".docstatus--in_review").count() > 0)
    page.get_by_role("button", name="批准").click()
    page.wait_for_selector(".docstatus--approved", timeout=6000)
    check("in_review -> approved", page.locator(".docstatus--approved").count() > 0)

    # 6) Resource / error health
    check("no pageerrors", len(page_errors) == 0, "; ".join(page_errors[:3]))
    check("no failed .js/.css resources", len(failed_resources) == 0, f"{len(failed_resources)} failed")
    # console errors that are not benign /api aborts
    real_console = [e for e in console_errors if "/api/" not in e and "ERR_ABORTED" not in e and "Failed to load resource" not in e]
    check("no critical console errors", len(real_console) == 0, "; ".join(real_console[:3]))

    browser.close()

passed = sum(1 for _, c, _ in results if c)
total = len(results)
print(f"\nSUMMARY: {passed}/{total} passed")
if passed != total:
    print("FAILED CHECKS:")
    for n, c, d in results:
        if not c: print(f"  - {n}: {d}")
    sys.exit(1)
print("ALL PASS")
