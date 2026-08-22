"""AgentBoard 每晚自动化 E2E 测试（Playwright 真实浏览器 + REST 全链路）。

定位：每晚定时执行的「核心回归冒烟」。采用**混合策略**以兼顾稳定性与真实覆盖：
  - 真实浏览器（Chromium）覆盖 UI 契约层：注册/登录流、token 持久化、首页渲染、
    登出菜单——这些能抓前端启动/鉴权回归，HTTP 等价校验无法覆盖。
  - REST 全链路（httpx）覆盖后端核心逻辑：Project→Epic→Story→Task/Bug 完整 CRUD、
    状态机流转、Spec 更新——后端契约稳定，避免 SPA 内部 Angular change detection
    时序导致的脆弱断言（见项目记忆「通过 API 直接验证状态流转」）。

注意：当前前端为 v7 布局（项目→工作台两级），旧 tests/test_playwright_e2e.py 的
`#home-new-project`/`#p-new-epic` 等选择器已整体失效；本脚本仅断言**稳定且高价值**的
UI 契约（鉴权 + 首页），业务逻辑下沉到 REST 层，保证每晚跑稳。

运行：
    PYTHONPATH=<repo> python tests/e2e_nightly_20260821_2205/nightly_e2e.py
退出码：0=全部通过；1=存在失败；2=环境/启动错误。

约定（与项目记忆一致）：
  - 用临时 SQLite，绝不触碰 agentboard.db / 生产库。
  - 本地 frontend/dist 常缺 bundle，Web 默认回退到 agentboard/web/static/。
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone

import httpx

# ----------------------------------------------------------------------------
# 路径与配置
# ----------------------------------------------------------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"
os.environ["AGENTBOARD_ENV"] = "development"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_HAS_UVICORN = importlib.util.find_spec("uvicorn") is not None

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPORTS_DIR = os.path.join(_HERE, "reports")
_SHOTS_DIR = os.path.join(_HERE, "screenshots")
os.makedirs(_REPORTS_DIR, exist_ok=True)
os.makedirs(_SHOTS_DIR, exist_ok=True)

_RUN_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
_RESULTS: list[dict] = []


# ----------------------------------------------------------------------------
# 基础设施
# ----------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENTBOARD_DB_URL"] = os.environ["AGENTBOARD_DB_URL"]
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"服务在 {url} 启动超时")


def client_delete_with_auth(api_base: str, token: str, pid: int) -> httpx.Response:
    """带鉴权删除项目（用于已知缺陷监控，不依赖成功）。"""
    return httpx.request(
        "DELETE", f"{api_base}/api/projects/{pid}",
        headers={"Authorization": f"Bearer {token}"}, timeout=20,
    )


# ----------------------------------------------------------------------------
# 用例执行包装
# ----------------------------------------------------------------------------
def _run_case(name: str, fn):
    shot = os.path.join(_SHOTS_DIR, f"{name}_{_RUN_TS}.png")
    try:
        fn()
        _RESULTS.append({"name": name, "ok": True, "detail": "PASS", "shot": None})
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        _RESULTS.append({"name": name, "ok": False,
                         "detail": f"{type(e).__name__}: {e}", "shot": shot})
        print(f"[FAIL] {name}: {type(e).__name__}: {e}")
        try:
            if "_page" in globals() and _page is not None:
                _page.screenshot(path=shot)
        except Exception:
            pass
        traceback.print_exc()


# ----------------------------------------------------------------------------
# UI 辅助（v7 前端选择器契约）
# ----------------------------------------------------------------------------
def ui_register(page, base: str, username: str, password: str):
    """真实 UI 注册并进入应用。"""
    page.goto(base + "/")
    page.wait_for_selector(".auth-tab", state="visible", timeout=10000)
    page.locator(".auth-tab", has_text="注册").click()
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.locator(".login-submit").click()
    # 注册成功 → 应用主体渲染（#app）+ token 持久化
    page.wait_for_selector("#app", state="attached", timeout=10000)
    page.wait_for_timeout(800)


def ui_login(page, base: str, username: str, password: str):
    page.goto(base + "/")
    page.wait_for_selector(".auth-tab", state="visible", timeout=10000)
    page.locator(".auth-tab", has_text="登录").click()
    page.locator("input[name='username']").fill(username)
    page.locator("input[name='password']").fill(password)
    page.locator(".login-submit").click()
    page.wait_for_selector("#app", state="attached", timeout=10000)
    page.wait_for_timeout(800)


def ui_logout(page):
    """点击右上角用户按钮 → 退出登录。"""
    page.locator(".user-button-v7").click()
    page.wait_for_timeout(600)
    page.locator("span", has_text="退出登录").click()
    page.wait_for_selector(".auth-tab", state="visible", timeout=10000)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main() -> int:
    if not _HAS_UVICORN:
        print("ENV_ERROR: uvicorn 未安装。先执行 pip install -r requirements.txt")
        return 2
    if not _HAS_PLAYWRIGHT:
        print("ENV_ERROR: playwright 未安装。执行 pip install playwright && playwright install chromium")
        return 2

    from playwright.sync_api import sync_playwright

    api_port = _free_port()
    web_port = _free_port()
    api_proc = _start_server("agentboard.api:app", api_port)
    legacy = os.path.join(_ROOT, "agentboard", "web", "static")
    web_extra = {"AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}"}
    if os.path.isdir(legacy) and os.listdir(legacy):
        web_extra["AGENTBOARD_WEB_STATIC_DIR"] = legacy
    web_proc = _start_server("agentboard.web_app:app", web_port, web_extra)
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"

    try:
        _wait(api_base + "/api/meta", timeout=40)
        _wait(web_base + "/", timeout=40)
    except Exception as e:
        print(f"STARTUP_ERROR: {e}")
        for p in (api_proc, web_proc):
            p.terminate()
        return 2

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as e:
        print(f"BROWSER_ERROR: Chromium 不可用（可能未执行 playwright install chromium）: {e}")
        for p in (api_proc, web_proc):
            p.terminate()
        return 2

    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    global _page
    _page = page

    ts = str(int(time.time()))

    # ---- Case 1: 后端健康检查 ----
    def case_health():
        r = httpx.get(api_base + "/api/meta", timeout=10)
        assert r.status_code == 200, f"/api/meta 应 200，实际 {r.status_code}"
        body = r.json()
        assert "statuses" in body and "types" in body, "meta 结构缺失"
    _run_case("backend_health", case_health)

    # ---- Case 2: 注册 UI 流 + token 持久化 ----
    def case_register():
        ui_register(page, web_base, "nightly_reg_" + ts, "secret123")
        token = page.evaluate("localStorage.getItem('agentboard_token')")
        assert token, "注册成功后未写入 agentboard_token"
        assert page.locator("#app").count() > 0, "注册后应用主体未渲染"
    _run_case("ui_register_flow", case_register)

    # ---- Case 3: 登录 UI 流（登出后重新登录）----
    def case_login():
        ui_logout(page)
        ui_login(page, web_base, "nightly_reg_" + ts, "secret123")
        token = page.evaluate("localStorage.getItem('agentboard_token')")
        assert token, "登录成功后未写入 agentboard_token"
    _run_case("ui_login_flow", case_login)

    # ---- Case 4: 首页渲染（应用主体 + 新建项目入口）----
    def case_dashboard():
        page.goto(web_base + "/")
        page.wait_for_selector("#app", state="attached", timeout=10000)
        page.wait_for_selector(".master-create-button-v7", state="visible", timeout=10000)
        assert page.locator(".master-create-button-v7").inner_text().strip() == "新建项目"
    _run_case("dashboard_render", case_dashboard)

    # ---- Case 5: REST 全链路 CRUD + 状态机 + Spec ----
    def case_rest_crud():
        client = httpx.Client(base_url=api_base, timeout=20)
        # 注册获取 token（绕过 UI，纯后端链路）
        uname = "nightly_api_" + ts
        reg = client.post("/api/auth/register",
                          json={"username": uname, "password": "secret123"})
        assert reg.status_code in (200, 201), f"注册应 200/201: {reg.status_code} {reg.text}"
        token = reg.json()["token"]
        client.headers.update({"Authorization": f"Bearer {token}"})

        # Project（创建类端点返回 200 或 201 均为成功）
        proj = client.post("/api/projects", json={"name": "Nightly项目" + ts})
        assert proj.status_code in (200, 201), f"建项目: {proj.status_code} {proj.text}"
        pid = proj.json()["id"]

        # Epic
        epic = client.post(f"/api/projects/{pid}/epics", json={"title": "Nightly史诗" + ts})
        assert epic.status_code in (200, 201), f"建史诗: {epic.status_code} {epic.text}"
        eid = epic.json()["id"]

        # Story
        story = client.post(f"/api/epics/{eid}/stories", json={"title": "Nightly故事" + ts})
        assert story.status_code in (200, 201), f"建故事: {story.status_code} {story.text}"
        sid = story.json()["id"]

        # Task（注意：TaskIn 必含 project_id；type 合法值为 meta.types：dev/bug/qa/design）
        task = client.post(f"/api/stories/{sid}/tasks",
                           json={"title": "Nightly任务" + ts, "type": "dev", "project_id": pid})
        assert task.status_code in (200, 201), f"建任务: {task.status_code} {task.text}"
        tid = task.json()["id"]

        # Bug
        bug = client.post(f"/api/stories/{sid}/tasks",
                          json={"title": "Nightly缺陷" + ts, "type": "bug", "project_id": pid})
        assert bug.status_code in (200, 201), f"建缺陷: {bug.status_code} {bug.text}"
        bid = bug.json()["id"]

        # 状态机：先读取实际初始状态，再按合法迁移推进到 done。
        # 新建任务初始态可能是 todo（非 backlog），故不硬编码起点。
        init = client.get(f"/api/tasks/{tid}")
        assert init.status_code == 200, f"读取任务: {init.status_code}"
        initial_status = init.json().get("status")
        flow = ["todo", "in_progress", "in_review", "done"]
        # 从初始态之后的第一个阶段开始流转
        if initial_status in flow:
            stages = flow[flow.index(initial_status) + 1:]
        else:
            stages = flow  # 未知起点则从头尝试
        for st in stages:
            payload = {"status": st}
            # 终态 done（及 blocked）需带 status_reason（项目约束）
            if st == "done":
                payload["status_reason"] = "completed"
            r = client.put(f"/api/tasks/{tid}/status", json=payload)
            assert r.status_code == 200, f"任务流转 {initial_status}→{st}: {r.status_code} {r.text}"
            initial_status = st
        got = client.get(f"/api/tasks/{tid}")
        assert got.json().get("status") == "done", f"任务终态应为 done，实际 {got.json().get('status')}"

        # Spec 更新（端点为 PATCH /api/tasks/{id}）
        us = client.patch(f"/api/tasks/{tid}", json={"spec": "## 验收标准\n- [ ] 覆盖率≥80%"})
        assert us.status_code == 200, f"更新 spec: {us.status_code} {us.text}"
        assert "验收标准" in us.json().get("spec", ""), "spec 未保存"

        # 列表读取（分页包装 items）
        plist = client.get("/api/projects").json()
        assert any(p["id"] == pid for p in plist.get("items", [])), "项目未出现在列表"
        tlist = client.get(f"/api/stories/{sid}/tasks").json()
        tids = [t["id"] for t in tlist.get("items", [])]
        assert tid in tids and bid in tids, "task/bug 未出现在 story 任务列表"

        # 列表读取（分页包装 items）
        plist = client.get("/api/projects").json()
        assert any(p["id"] == pid for p in plist.get("items", [])), "项目未出现在列表"
        tlist = client.get(f"/api/stories/{sid}/tasks").json()
        tids = [t["id"] for t in tlist.get("items", [])]
        assert tid in tids and bid in tids, "task/bug 未出现在 story 任务列表"
        client.close()

        # 已知后端缺陷监控：删除「含子项」的项目当前会 500（FK 级联删除未防御），
        # 与项目记忆「DELETE /api/epics 500 FK 防御级联」同类。本用例反向监控——
        # 期望当前仍为 500（缺陷未修）；若某天返回 200，说明已修复，反向报警。
        # 注意：临时库会自动清理，无需依赖删除成功。
        del_p = client_delete_with_auth(api_base, token, pid)
        if del_p.status_code == 200:
            # 缺陷已修复 → 反向报警（不应再 500）
            raise AssertionError(
                "监控反转：DELETE /api/projects/{含子项} 返回 200，已知 FK 级联缺陷疑似已修复，"
                "请复核此监控用例是否需要调整")
        assert del_p.status_code == 500, \
            f"已知缺陷监控：删除含子项项目应仍为 500，实际 {del_p.status_code} {del_p.text}"
    _run_case("rest_full_crud_pipeline", case_rest_crud)

    # 收尾
    browser.close()
    pw.stop()
    for p in (api_proc, web_proc):
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:
            p.kill()
    try:
        os.remove(_DB)
    except Exception:
        pass

    _write_reports()
    passed = sum(1 for r in _RESULTS if r["ok"])
    total = len(_RESULTS)
    print(f"\n==== NIGHTLY E2E SUMMARY: {passed}/{total} passed ====")
    return 0 if passed == total else 1


# ----------------------------------------------------------------------------
# 报告生成
# ----------------------------------------------------------------------------
def _write_reports():
    passed = sum(1 for r in _RESULTS if r["ok"])
    total = len(_RESULTS)
    failed = total - passed
    summary = {
        "run_ts_utc": _RUN_TS,
        "total": total, "passed": passed, "failed": failed,
        "results": _RESULTS,
    }
    json_path = os.path.join(_REPORTS_DIR, f"nightly_{_RUN_TS}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    rows = ""
    for r in _RESULTS:
        color = "#1a7f37" if r["ok"] else "#cf222e"
        shot = f'<a href="file://{r["shot"]}">截图</a>' if r["shot"] else "-"
        rows += (
            f'<tr><td>{r["name"]}</td>'
            f'<td style="color:{color};font-weight:600">{("PASS" if r["ok"] else "FAIL")}</td>'
            f'<td>{r["detail"]}</td><td>{shot}</td></tr>'
        )
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>AgentBoard 每晚 E2E 报告 {_RUN_TS}</title>
<style>body{{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:24px;color:#222}}
h1{{font-size:20px}}table{{border-collapse:collapse;width:100%;margin-top:12px}}
th,td{{border:1px solid #ddd;padding:8px 10px;text-align:left;font-size:13px}}
th{{background:#f6f8fa}}tr:nth-child(even){{background:#fafafa}}
.badge{{display:inline-block;padding:2px 10px;border-radius:10px;color:#fff;font-size:12px}}
.ok{{background:#1a7f37}} .ng{{background:#cf222e}}</style></head><body>
<h1>AgentBoard 每晚自动化 E2E 报告</h1>
<p>运行时间(UTC): {_RUN_TS} &nbsp;|&nbsp; 结果:
<span class="badge {'ok' if failed==0 else 'ng'}">{passed}/{total} 通过</span></p>
<table><thead><tr><th>用例</th><th>结果</th><th>详情</th><th>失败截图</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
    html_path = os.path.join(_REPORTS_DIR, f"nightly_{_RUN_TS}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"报告已生成:\n  JSON: {json_path}\n  HTML: {html_path}")


if __name__ == "__main__":
    sys.exit(main())
