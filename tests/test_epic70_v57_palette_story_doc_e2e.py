"""
Epic 70 (v5.7) 命令面板接入 Story/文档后端搜索 —— 端到端验证
- 登录 admin -> project 59 (AUTODEV70) epic 60 下建种子 story + 种子文档（自清理）
- 断言：
  1) Ctrl+K 打开命令面板
  2) 输入唯一 token -> 后端搜索同时返回「Story」(cat-story) 与「文档」(cat-document) 分类结果；
     标题含 token；点击 Story 结果 -> 跳转到 /story/{id} 且面板关闭；
     重开面板点击文档结果 -> 跳转到 /documents/{id} 且面板关闭
  3) 输入无匹配 token -> 显示「无匹配命令」空态
  4) 0 pageerror / console error / .js+.css 404
- 测试末清理种子，不污染追踪实体（task 1125 / story 109 / epic 60 / project 59）
"""
import json
import random
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8090"
API = "http://127.0.0.1:58125"
PROJECT_ID = 59
EPIC_ID = 60
USER = "admin"
PASS = "admin123"
SEED = "__E2E_V57_" + str(random.randint(100000, 999999))


def api(method, path, token=None, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def login():
    st, u = api("POST", "/api/auth/login", body={"username": USER, "password": PASS})
    assert st == 200, f"login failed {st}"
    return u["token"], u["username"]


def main():
    token, _ = login()
    created = []
    errors = []
    js_css_fail = []
    try:
        # 种子 story（epic 60）+ 种子文档（project 59）
        st, story = api("POST", f"/api/epics/{EPIC_ID}/stories", token=token,
                        body={"title": SEED + "-story", "description": "E2E 种子 story"})
        assert st == 201, f"create story {st} {story}"
        seed_sid = story["id"]
        created.append(("story", seed_sid))
        st, doc = api("POST", "/api/documents", token=token,
                      body={"project_id": PROJECT_ID, "title": SEED + "-doc",
                            "content": "E2E 种子文档", "type": "plan", "status": "draft"})
        assert st == 201, f"create doc {st} {doc}"
        seed_did = doc["id"]
        created.append(("doc", seed_did))
        print(f"[seed] story={seed_sid} doc={seed_did} token={SEED}")

        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-proxy-server"])
            page = browser.new_page()
            init = (
                "localStorage.setItem('agentboard_token','%s');"
                "localStorage.setItem('agentboard_user','admin');" % token
            )
            page.add_init_script(init)
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("requestfailed", lambda r: (
                js_css_fail.append(r.url) if (r.url.endswith(".js") or r.url.endswith(".css")) else None
            ))

            page.goto(WEB + "/projects", wait_until="domcontentloaded")
            page.wait_for_selector("#command-palette-toggle", timeout=20000)

            # 1) Ctrl+K 打开面板
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            print("[OK] Ctrl+K 打开命令面板")

            # 2) Story + 文档 搜索
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-story", timeout=8000)
            page.wait_for_selector(".palette-item-cat.cat-document", timeout=8000)
            story_items = page.locator(".palette-item-cat.cat-story")
            doc_items = page.locator(".palette-item-cat.cat-document")
            assert story_items.count() >= 1, "未出现 Story 搜索结果"
            assert doc_items.count() >= 1, "未出现文档搜索结果"
            print(f"[OK] Story 搜索 {story_items.count()} 条 / 文档搜索 {doc_items.count()} 条")

            # 点击 Story 结果 -> /story/{id}
            story_loc = page.locator(".palette-item", has_text=SEED + "-story").first
            story_title = story_loc.inner_text()
            assert SEED in story_title, f"Story 结果标题不含 token: {story_title}"
            story_loc.click()
            page.wait_for_timeout(600)
            assert f"/story/{seed_sid}" in page.url, f"未跳转到 /story/{seed_sid}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            print(f"[OK] 点击 Story 结果跳转到 {page.url}")

            # 重开面板 -> 点击文档结果 -> /documents/{id}
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", SEED)
            page.wait_for_selector(".palette-item-cat.cat-document", timeout=8000)
            doc_loc = page.locator(".palette-item", has_text=SEED + "-doc").first
            doc_title = doc_loc.inner_text()
            assert SEED in doc_title, f"文档结果标题不含 token: {doc_title}"
            doc_loc.click()
            page.wait_for_timeout(600)
            assert f"/documents/{seed_did}" in page.url, f"未跳转到 /documents/{seed_did}，当前 {page.url}"
            assert page.locator(".command-palette").count() == 0, "跳转后面板未关闭"
            print(f"[OK] 点击文档结果跳转到 {page.url}")

            # 3) 无匹配空态
            page.keyboard.press("Control+k")
            page.wait_for_selector(".command-palette", state="visible", timeout=5000)
            page.fill("#paletteInput", "zzzqqq_nomatch_xyz")
            try:
                page.wait_for_function("!document.querySelector('.command-palette-spinner')", timeout=5000)
            except Exception:
                pass
            page.wait_for_timeout(150)
            empty = page.locator(".command-palette-empty")
            assert empty.count() == 1, "无匹配时未显示空态"
            assert "无匹配" in empty.first.inner_text(), "空态文案异常"
            print("[OK] 无匹配显示空态")

            # 4) 错误检查
            assert not errors, "存在 JS/控制台错误:\n" + "\n".join(errors)
            assert not js_css_fail, "存在 .js/.css 加载失败:\n" + "\n".join(js_css_fail)
            print("[OK] 0 pageerror / console error / .js+.css 404")

            browser.close()
        print("ALL PASS")
    finally:
        for kind, _id in created:
            if kind == "doc":
                api("DELETE", f"/api/documents/{_id}", token=token)
            else:
                api("DELETE", f"/api/stories/{_id}", token=token)
        if errors or js_css_fail:
            print("ERRORS:", errors, js_css_fail)


if __name__ == "__main__":
    main()
