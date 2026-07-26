"""
Playwright E2E: 验证文档 Mermaid 图表渲染
- 使用端口 8080 (web) + 58125 (API)，内部 route 58124→58125
- 创建含 mermaid 内容的测试文档，导航到详情页，验证 SVG 渲染
"""
import sys
import urllib.request
import json
import time
from playwright.sync_api import sync_playwright

WEB = "http://127.0.0.1:8080"
API = "http://127.0.0.1:58125"
MERMAID_CONTENT = """\
flowchart TD
    A[开始] --> B{判断条件}
    B -->|是| C[执行操作1]
    B -->|否| D[执行操作2]
    C --> E[结束]
    D --> E"""

MERMAID_DOC = {
    "title": "Mermaid渲染测试文档",
    "content": f"""\
# Mermaid 图表测试

这是一个 Mermaid 流程图测试。

```mermaid
{MERMAID_CONTENT}
```

图表下方应有 SVG 渲染结果。
""",
    "type": "design",
    "project_id": None,
}

results = []
console_errors = []
page_errors = []
failed_resources = []


def check(name: str, cond, detail: str = ""):
    results.append((name, bool(cond), detail))
    status = "PASS" if cond else "FAIL"
    msg = f"[{status}] {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)


def api_call(method, path, body=None, token=None):
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def get_api_token():
    """注册或登录获取 token"""
    uname, pwd = "e2e_mermaid_user", "e2epass123"
    st, payload = api_call("POST", "/api/auth/register", {"username": uname, "password": pwd})
    if st not in (200, 201):
        st, payload = api_call("POST", "/api/auth/login", {"username": uname, "password": pwd})
    token = payload.get("token")
    if not token:
        print(f"FATAL: 无法获取 token (status={st}, payload={payload})")
        sys.exit(2)
    return token


def get_or_create_project(token: str):
    """获取或创建测试用项目"""
    st, data = api_call("GET", "/api/projects", token=token)
    projects = data if isinstance(data, list) else data.get("items", [])
    # 找一个有权限的项目
    for p in projects:
        return p["id"]
    # 如果没有，创建一个
    st2, new_proj = api_call("POST", "/api/projects", {
        "name": "Mermaid测试项目",
        "description": "Playwright Mermaid E2E 测试用"
    }, token=token)
    if st2 in (200, 201):
        return new_proj["id"]
    print(f"FATAL: 无法创建项目 (status={st2}, data={new_proj})")
    sys.exit(2)


def main():
    token = get_api_token()
    print(f"认证成功 (user=e2e_mermaid_user), token len={len(token)}")

    project_id = get_or_create_project(token)
    print(f"使用项目 ID={project_id}")

    MERMAID_DOC["project_id"] = project_id

    # 创建含 mermaid 的文档
    st, doc = api_call("POST", "/api/documents", MERMAID_DOC, token=token)
    if st not in (200, 201):
        print(f"FATAL: 无法创建文档 (status={st}, data={doc})")
        sys.exit(2)
    doc_id = doc["id"]
    print(f"创建文档 ID={doc_id}, title={doc.get('title')}")

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-proxy-server"])
        context = browser.new_context()

        # 内部 route：web 注入的 API URL 是 58124，内部改写为 58125
        def route_handler(route):
            url = route.request.url
            if "127.0.0.1:58124" in url:
                route.continue_(url=url.replace("127.0.0.1:58124", "127.0.0.1:58125"))
            else:
                route.continue_()
        context.route("**/*", route_handler)

        page = context.new_page()

        # Inject auth token
        page.add_init_script(
            f"localStorage.setItem('agentboard_token', '{token}');"
            "localStorage.setItem('agentboard_user', 'e2e_mermaid_user');"
        )

        def on_console(msg):
            if msg.type == "error":
                text = msg.text
                # 忽略 CDN 加载失败（降级由设计决定）
                if any(cdn in text for cdn in ["mermaid", "cdn.jsdelivr", "unpkg", "baomitu"]):
                    return
                console_errors.append(text)

        def on_pageerror(err):
            page_errors.append(str(err))

        def on_request_failed(req):
            url = req.url
            # 只记录本地资源的失败
            if "127.0.0.1" in url or "localhost" in url:
                if url.endswith(".js") or url.endswith(".css"):
                    failed_resources.append(url)

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_request_failed)

        # ── 1. 导航到文档详情页 ──
        doc_url = f"{WEB}/documents/{doc_id}"
        print(f"\n导航到: {doc_url}")
        page.goto(doc_url, wait_until="domcontentloaded")
        page.wait_for_selector(".doc-content", timeout=15000)

        # ── 2. 验证 Markdown 基本渲染 ──
        h1 = page.locator(".doc-content h1").first
        check("Markdown h1 渲染", h1.count() > 0 and "Mermaid" in (h1.inner_text() or ""),
              h1.inner_text() if h1.count() > 0 else "")

        # ── 3. 验证 mermaid 代码块存在 ──
        mermaid_blocks = page.locator("pre.mermaid")
        check("Mermaid 代码块存在", mermaid_blocks.count() > 0,
              f"count={mermaid_blocks.count()}")

        # ── 4. 等待 Mermaid 渲染（最多 20s，CDN 加载 + 渲染） ──
        print("\n等待 Mermaid CDN 加载 + SVG 渲染（最多 20s）...")
        svg_rendered = False
        for attempt in range(20):
            time.sleep(1)
            svg_count = page.locator(".mermaid-svg").count()
            pre_count = page.locator("pre.mermaid").count()
            print(f"  [{attempt+1}/20] .mermaid-svg={svg_count}, pre.mermaid={pre_count}")
            if svg_count > 0:
                svg_rendered = True
                break
            # 如果所有 CDN 都失败了，降级显示 pre.mermaid 代码块（浅色虚线边框）
            if attempt >= 3 and pre_count > 0:
                # 检查是否有 mermaid-script 加载失败记录
                pass

        check("Mermaid SVG 渲染成功（CDN 加载生效）", svg_rendered,
              f".mermaid-svg count={page.locator('.mermaid-svg').count()}")

        # 如果 SVG 未渲染，验证降级：pre.mermaid 仍存在
        if not svg_rendered:
            pre_count = page.locator("pre.mermaid").count()
            check("降级：mermaid 代码块仍显示（CDN 不可用）", pre_count > 0,
                  "离线降级正常（可接受）")
            print("  ⚠️  CDN 全部不可用，使用降级显示")

        # ── 5. 验证 SVG 内容有效 ──
        if svg_rendered:
            svg_el = page.locator(".mermaid-svg").first
            svg_html = svg_el.inner_html()
            check("SVG 包含 <svg> 标签", "<svg" in svg_html,
                  f"SVG HTML 长度={len(svg_html)}")
            check("SVG 包含图形节点（rect/path/text）",
                  any(k in svg_html for k in ["<rect", "<path", "<text", "<ellipse"]),
                  "Mermaid SVG 内容有效")

        # ── 6. 健康检查 ──
        check("无 pageerror", len(page_errors) == 0, "; ".join(page_errors[:3]))
        local_failed = [r for r in failed_resources if "127.0.0.1" in r or "localhost" in r]
        check("无本地 .js/.css 加载失败", len(local_failed) == 0,
              "; ".join(local_failed[:3]))
        real_console = [e for e in console_errors
                        if "/api/" not in e and "ERR_ABORTED" not in e]
        check("无关键 console error", len(real_console) == 0,
              "; ".join(real_console[:3]))

        browser.close()

    # ── 汇总 ──
    print("\n" + "=" * 60)
    passed = sum(1 for _, c, _ in results if c)
    total = len(results)
    print(f"SUMMARY: {passed}/{total} passed")
    if passed != total:
        print("FAILED CHECKS:")
        for n, c, d in results:
            if not c:
                print(f"  ✗ {n}: {d}")
        sys.exit(1)
    print("ALL PASS ✓")
    sys.exit(0)


if __name__ == "__main__":
    main()
