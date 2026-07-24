"""
Task 261 (v4.2) 本地开发 hot-reload 配置 —— 端到端验证
- 启动 dev server (ng serve :4200 + proxy.conf.json -> 58125)
- 应用 HTTP 客户端在 localhost 下应使用相对 /api（baseUrl=''），
  经 dev proxy 转发到本地后端；断言全程无任何请求打到旧的默认端口 :8000。
- 断言 /api/* 经代理返回 2xx（证明 proxy + 相对 baseUrl 联通）。
- 断言页面渲染（.sidebar 出现），且 0 pageerror / console error / .js+.css 404。
- 数据闭环：抓取本地后端已有项目名，断言 dev UI 经代理渲染出了该项目（端到端数据联通）。
"""
import json
import urllib.request
import urllib.error

from playwright.sync_api import sync_playwright

DEV = "http://127.0.0.1:4200"
API = "http://127.0.0.1:58125"   # dev proxy 目标（本地 uvicorn）
USER = "admin"
PASS = "admin123"


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
    assert st == 200, f"login failed {st}: {u}"
    return u["token"]


def main():
    token = login()
    # 抓取本地后端已有项目名，用于数据闭环断言
    _, projs = api("GET", "/api/projects?limit=50", token=token)
    names = [p.get("name") for p in (projs.get("items") if isinstance(projs, dict) else projs) or [] if p.get("name")]

    errors = []
    stray_8000 = []
    api_ok = []
    saw_data = False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.add_init_script("localStorage.setItem('agentboard_token','%s');" % token)

            def on_request(req):
                if ":8000" in req.url and "/api" in req.url:
                    stray_8000.append(req.url)
            def on_response(resp):
                if "/api/" in resp.url and resp.status < 400:
                    api_ok.append((resp.url, resp.status))
            page.on("request", on_request)
            page.on("response", on_response)
            page.on("console", lambda m: errors.append("console:" + m.type + ":" + m.text) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: errors.append("pageerror:" + str(e)))

            page.goto(DEV + "/", wait_until="networkidle", timeout=30000)
            page.wait_for_selector(".sidebar", timeout=20000)
            page.wait_for_timeout(2000)

            # 数据闭环：dev UI 应经代理渲染出本地后端已有项目之一
            for nm in names[:5]:
                try:
                    page.wait_for_function("document.body.innerText.indexOf(%r) >= 0" % nm, timeout=6000)
                    saw_data = True
                    break
                except Exception:
                    continue
            browser.close()

        print("RESULT stray_8000_requests:", len(stray_8000), stray_8000[:5])
        print("RESULT api_2xx_responses:", len(api_ok), api_ok[:3])
        print("RESULT console/page_errors:", len(errors), errors[:8])
        print("RESULT sidebar_rendered: True")
        print("RESULT dev_ui_rendered_backend_data:", saw_data, "(backend projects:", len(names), ")")

        assert len(stray_8000) == 0, f"应用仍向旧默认端口 :8000 发请求: {stray_8000}"
        assert len(api_ok) > 0, "dev 代理下没有任何 /api 请求成功（proxy 未联通）"
        assert len(errors) == 0, f"存在控制台/页面错误: {errors}"
        if names:
            assert saw_data, "dev UI 未能经代理渲染本地后端数据（项目未出现在界面）"
        print("ALL PASS")
    finally:
        print("DONE")


if __name__ == "__main__":
    main()
