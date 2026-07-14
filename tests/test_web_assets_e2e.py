"""AgentBoard 前端静态资源加载 E2E 测试。

**目的**：防止 web_app.py 静态资源路径/MIME 类型问题导致页面空白（2026-07-14 真实事故）。

覆盖：
- 首页无 404 资源
- JS 文件 Content-Type 必须是 text/javascript
- CSS 文件 Content-Type 必须是 text/css
- Angular 真实渲染（<app-root> 不再为空）
- index.html 中所有引用的资源都可访问

**与 test_playwright_e2e.py 的区别**：
- 后者启动临时服务、跑完整业务流程
- 本文件直连已部署服务（Docker 容器），只验证 web_app.py 的核心资源契约

**运行**：
    PYTHONPATH=. python -m pytest tests/test_web_assets_e2e.py -q
"""
import json
import re
import socket

import pytest
import urllib.request


def _free_port() -> int:
    """找一个可用端口用于本地回环检查。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str, timeout: float = 5.0) -> tuple[int, dict, str]:
    """简单的 HTTP GET，返回 (status, headers, body_sample)。"""
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body_bytes = resp.read()
        body = body_bytes.decode("utf-8", errors="replace")
        return resp.status, dict(resp.headers), body


# ---------------- Fixtures ----------------
WEB_BASE = "http://localhost:5080"  # 部署服务的 Web 端口


# ---------------- 核心契约测试 ----------------
def test_index_html_served():
    """首页必须返回 200 且 Content-Type 是 text/html。"""
    status, headers, _ = _http_get(WEB_BASE + "/")
    assert status == 200, f"首页应返回 200，实际: {status}"
    ct = headers.get("content-type", "")
    assert "text/html" in ct, f"首页 Content-Type 应含 text/html，实际: {ct}"


def test_index_html_has_app_root():
    """首页 HTML 必须含 <app-root> 元素，Angular 入口。"""
    _, _, body = _http_get(WEB_BASE + "/")
    assert "<app-root" in body, "首页应含 <app-root> 元素"


def test_no_404_on_home_resources(page):
    """真实浏览器：加载首页时所有资源（JS/CSS/SVG/字体）都应 200，不能有 404。

    这是 2026-07-14 「页面空白」事故的回归测试。
    """
    failed = []
    page.on("response", lambda r: failed.append((r.status, r.url))
            if r.status >= 400 else None)

    page.goto(WEB_BASE + "/", timeout=15000)
    page.wait_for_load_state("networkidle", timeout=10000)
    page.wait_for_timeout(1000)

    assert not failed, f"首页不应有 404 资源: {failed}"


def test_angular_actually_boots(page):
    """真实浏览器：<app-root> 必须有内容（Angular 已挂载）。

    防止 web_app.py 返回错误 MIME 类型导致 JS 不执行、Angular 不启动。
    """
    page.goto(WEB_BASE + "/", timeout=15000)
    page.wait_for_timeout(3000)

    inner = page.locator("app-root").inner_html()
    assert len(inner) > 100, \
        f"Angular 应已挂载并渲染内容，<app-root> 当前仅 {len(inner)} 字符"


def test_angular_renders_known_text(page):
    """真实浏览器：首页应出现品牌文案「AgentBoard」。"""
    page.goto(WEB_BASE + "/", timeout=15000)
    page.wait_for_timeout(3000)

    body = page.locator("body").inner_text()
    assert "AgentBoard" in body, f"首页应显示「AgentBoard」，body: {body[:200]}"


# ---------------- 静态资源 MIME 类型测试 ----------------
def test_js_files_have_correct_mime():
    """所有 main-*.js 文件必须以 text/javascript 提供。

    2026-07-14 事故：FileResponse 返回 application/json 导致 JS 不执行。
    """
    import os
    from pathlib import Path
    static_dir = Path(r"E:/Projects/WorkBuddy/AgentBoard/agentboard/web/static")
    js_files = [f.name for f in static_dir.iterdir()
                if f.name.startswith("main-") and f.name.endswith(".js")]

    assert js_files, "应至少存在一个 main-*.js 编译产物"

    for js_name in js_files:
        status, headers, _ = _http_get(f"{WEB_BASE}/static/{js_name}")
        assert status == 200, f"{js_name} 应返回 200，实际: {status}"
        ct = headers.get("content-type", "")
        assert "text/javascript" in ct or "application/javascript" in ct, \
            f"{js_name} Content-Type 应为 text/javascript，实际: {ct}"


def test_css_files_have_correct_mime():
    """所有 styles-*.css 文件必须以 text/css 提供。"""
    from pathlib import Path
    static_dir = Path(r"E:/Projects/WorkBuddy/AgentBoard/agentboard/web/static")
    css_files = [f.name for f in static_dir.iterdir()
                 if f.name.startswith("styles-") and f.name.endswith(".css")]

    assert css_files, "应至少存在一个 styles-*.css 编译产物"

    for css_name in css_files:
        status, headers, _ = _http_get(f"{WEB_BASE}/static/{css_name}")
        assert status == 200, f"{css_name} 应返回 200，实际: {status}"
        ct = headers.get("content-type", "")
        assert "text/css" in ct, \
            f"{css_name} Content-Type 应为 text/css，实际: {ct}"


def test_index_html_resource_paths_resolve():
    """index.html 中所有 <script src=...> 和 <link href=...> 资源必须可访问。

    web_app.py 已用正则把相对路径替换为 /static/ 前缀（修复了 2026-07-14 的 404）。
    """
    _, _, body = _http_get(WEB_BASE + "/")

    # 提取所有 script src 和 link href（排除 #fragment 和 data:）
    script_srcs = re.findall(r'<script[^>]+src="([^"#]+)"', body)
    link_hrefs = re.findall(r'<link[^>]+href="([^"#]+)"(?![^>]*data:)', body)

    resources = [r for r in script_srcs + link_hrefs
                 if r and not r.startswith("data:") and not r.startswith("__")]

    assert resources, "index.html 应至少含 1 个 script 或 link 资源"

    for r in resources:
        # 相对路径：相对根
        url = r if r.startswith("http") else WEB_BASE + (r if r.startswith("/") else "/" + r)
        try:
            status, _, _ = _http_get(url)
        except Exception as e:
            pytest.fail(f"资源 {r} 不可访问: {e}")
        assert status == 200, f"index.html 引用 {r} 应 200，实际: {status}"


# ---------------- API 端点测试 （2026-07-14 补充：防止 CORS/API 调用失败） ----------------
API_BASE = "http://localhost:58125"


def test_api_meta_accessible():
    """API meta 端点应返回 200 且包含 statuses 字段。"""
    status, headers, body = _http_get(f"{API_BASE}/api/meta")
    assert status == 200, f"/api/meta 应 200，实际: {status}"
    assert "statuses" in body, "/api/meta 应含 statuses 字段"


def test_api_cors_headers():
    """跨域请求（Origin: localhost:5080）必须返回 Access-Control-Allow-Origin。"""
    req = urllib.request.Request(f"{API_BASE}/api/meta")
    req.add_header("Origin", "http://localhost:5080")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.headers.get("Access-Control-Allow-Origin"), \
            "缺少 CORS Access-Control-Allow-Origin 头"


def test_api_cors_preflight():
    """OPTIONS 预检请求必须返回正确的 CORS 头（浏览器在 POST 前发的）。"""
    req = urllib.request.Request(f"{API_BASE}/api/meta", method="OPTIONS")
    req.add_header("Origin", "http://localhost:5080")
    req.add_header("Access-Control-Request-Method", "GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.headers.get("Access-Control-Allow-Origin"), \
            "OPTIONS 预检必须返回 Allow-Origin"
        assert resp.headers.get("Access-Control-Allow-Methods"), \
            "OPTIONS 预检必须返回 Allow-Methods"


def test_frontend_can_call_api(page):
    """真实浏览器：从页面 JavaScript 调用 API 应成功（跨域 CORS 验证 + 真实端点测试）。

    这是 2026-07-14 「Http failure response: 0 Unknown Error」的回归测试。
    使用标准 fetch() 验证 API 可达、CORS 无阻止、具体 story 端点工作正常。

    注意：如果标准 fetch() 通过但 Angular HttpClient 仍失败，
    则问题在 Angular 拦截器/重试机制（非 web_app.py 范畴）。
    """
    page.goto(f"{WEB_BASE}/", timeout=15000)
    page.wait_for_timeout(1000)

    # 测试 /api/meta
    result = page.evaluate(f"""async () => {{
        try {{
            const resp = await fetch('{API_BASE}/api/meta');
            if (!resp.ok) return 'HTTP ' + resp.status;
            const data = await resp.json();
            return 'OK: ' + (data.statuses || []).join(', ');
        }} catch(e) {{
            return 'CORS/BLOCK: ' + e.message;
        }}
    }}""")
    assert "OK:" in result, f"fetch /api/meta 应成功，实际: {result}"

    # 测试 /api/stories/9/tasks（用户截图看到的失败端点）
    result2 = page.evaluate(f"""async () => {{
        try {{
            const resp = await fetch('{API_BASE}/api/stories/9/tasks');
            if (!resp.ok) return 'HTTP ' + resp.status;
            const data = await resp.json();
            return 'OK: ' + resp.status + ' tasks=[' + data.length + ']';
        }} catch(e) {{
            return 'FAIL: ' + e.message;
        }}
    }}""")
    assert "OK:" in result2, \
        f"fetch /api/stories/9/tasks 应成功，实际: {result2}"


# ---------------- Fixtures 注入 ----------------
@pytest.fixture
def page():
    """复用 Playwright Chromium 启动（如果可用）。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            pg = ctx.new_page()
            try:
                yield pg
            finally:
                pg.close()
                ctx.close()
                browser.close()
    except ImportError:
        pytest.skip("playwright 未安装")
    except Exception as e:
        pytest.skip(f"Chromium 不可用：{e}")
