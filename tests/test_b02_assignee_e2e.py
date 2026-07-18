"""B-02 负责人指派（assignee）—— Playwright 真实浏览器 E2E。

验证项（纯前端接入，后端 assignee_id 字段/API 已就绪，不改后端契约）：
1. 注册用户 A 并创建 Project→Epic→Story。
2. 通过 API 注册用户 B 并加入项目成员，使负责人下拉出现两个候选。
3. 在 Story 页打开「新建工作项」弹窗，选择负责人 = 用户 B 并提交。
4. 任务详情页「负责人」元信息显示用户 B；通过 API 确认 assignee_id == B.id。
5. 在详情编辑表单改派为 用户 A，等待「负责人已更新」toast；API 确认 assignee_id == A.id。
6. 看板卡片显示负责人头像（assignee-chip）。
7. 零 pageerror / 零 console error / 零 .js/.css failed request。

运行：
    PYTHONPATH=. python -m pytest tests/test_b02_assignee_e2e.py -q
依赖：playwright + chromium + uvicorn（缺失时用例自动 skip）。
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import pytest

_HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None
_RUN_WEB = importlib.util.find_spec("uvicorn") is not None and _HAS_PLAYWRIGHT

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait(url: str, timeout: float = 15.0) -> None:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"服务在 {url} 启动超时")


@pytest.fixture(scope="module")
def servers():
    api_port = _free_port()
    web_port = _free_port()
    api_proc = _start_server("agentboard.api:app", api_port)
    web_proc = _start_server(
        "agentboard.web_app:app", web_port,
        {"AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}"},
    )
    api_base = f"http://127.0.0.1:{api_port}"
    web_base = f"http://127.0.0.1:{web_port}"
    try:
        _wait(api_base + "/api/meta")
        _wait(web_base + "/")
        yield api_base, web_base
    finally:
        for p in (api_proc, web_proc):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()


@pytest.fixture(scope="module")
def browser():
    if not _HAS_PLAYWRIGHT:
        pytest.skip("playwright 未安装；运行: pip install playwright && playwright install chromium")
    from playwright.sync_api import sync_playwright
    try:
        pw = sync_playwright().start()
        chromium = pw.chromium.launch(headless=True)
    except Exception as e:
        pytest.skip(f"Chromium 不可用（可能未执行 playwright install chromium）：{e}")
    try:
        yield chromium
    finally:
        try:
            chromium.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    try:
        yield pg
    finally:
        pg.close()
        ctx.close()


# ---------------- UI 辅助 ----------------
def _open_auth(page, base: str, mode: str = "register"):
    """打开应用并进入鉴权界面（当前登录组件：.auth-form / .auth-tab / .login-submit）。"""
    page.goto(base + "/")
    page.wait_for_selector(".auth-form", state="visible", timeout=10000)
    page.locator(".auth-tab", has_text=("注册" if mode == "register" else "登录")).first.click()
    page.wait_for_timeout(300)


def ui_register(page, base: str, username: str, password: str):
    """走真实 UI 完成注册并进入应用（#app 渲染即登录态）。"""
    _open_auth(page, base, "register")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click(".login-submit")
    page.wait_for_selector("#app", state="visible", timeout=10000)
    page.wait_for_selector("#home-new-project", state="visible", timeout=10000)


def _open_create(page, trigger_selector: str):
    page.click(trigger_selector)
    page.wait_for_selector("#create-modal", state="visible", timeout=10000)


def _submit_create(page, name: str, type_: str | None = None):
    page.fill("#create-title", name)
    if type_ is not None:
        page.select_option("#create-type", type_)
    page.click('#create-form [type="submit"]')
    page.wait_for_selector("#create-modal", state="detached", timeout=10000)


def _wait_text(page, selector: str, text: str, timeout: float = 10000.0):
    page.wait_for_function(
        "!!document.querySelector(" + json.dumps(selector) + ") && "
        "document.querySelector(" + json.dumps(selector) + ").innerText.includes(" + json.dumps(text) + ")",
        timeout=timeout,
    )


def _wait_toast(page, text: str, timeout: float = 8000.0):
    page.wait_for_function(
        "document.querySelector('#toast') && "
        "document.querySelector('#toast').textContent.includes(" + json.dumps(text) + ")",
        timeout=timeout,
    )


# ---------------- 用例 ----------------
def test_b02_assignee_flow(page, servers):
    """真实浏览器：负责人指派端到端（创建指派 + 详情改派 + 看板显示）。"""
    import httpx
    api_base, web_base = servers
    ts = str(int(time.time()))
    user_a = "b02a" + ts
    user_b = "b02b" + ts

    ui_register(page, web_base, user_a, "secret123")
    token = page.evaluate("localStorage.getItem('agentboard_token')")
    assert token, "注册后未写入 token"
    headers = {"Authorization": f"Bearer {token}"}

    # 错误监听（从交互一开始就收集，结尾统一断言）
    page_errors, console_errors, failed_requests = [], [], []
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.on("console", lambda m: console_errors.append(f"[{m.type}] {m.text}]") if m.type == "error" else None)
    page.on("requestfailed",
            lambda r: failed_requests.append(f"{r.url} - {r.failure}") if r.url.endswith((".js", ".css")) else None)

    # 1) Project -> Epic -> Story
    # 注：侧栏为条件渲染，改用「创建后通过 API 取 id + 直接 navigate」避免依赖侧栏渲染时机
    proj = "B02项目" + ts
    _open_create(page, "#home-new-project")
    _submit_create(page, proj)
    projs = httpx.get(f"{api_base}/api/projects", headers=headers, timeout=10).json()["items"]
    pid = next(p["id"] for p in projs if p["name"] == proj)
    page.goto(web_base + "/project/" + str(pid))
    page.wait_for_selector("#p-new-epic", state="visible", timeout=10000)

    page.wait_for_selector("#p-new-epic", state="visible", timeout=10000)
    epic = "B02史诗" + ts
    _open_create(page, "#p-new-epic")
    _submit_create(page, epic)
    _wait_text(page, "#app", epic)
    page.locator("a.entity-item", has_text=epic).first.click()

    page.wait_for_selector("#e-new-story", state="visible", timeout=10000)
    story = "B02故事" + ts
    _open_create(page, "#e-new-story")
    _submit_create(page, story)
    _wait_text(page, "#app", story)
    page.locator("a.entity-item", has_text=story).first.click()

    story_id = int(page.url.split("/story/")[1].split("?")[0].split("#")[0])
    # 取 project_id（用于加成员）—— 已在创建项目时解析为 pid（Story 序列化不含 project_id 字段）
    proj_id = pid

    # 2) 注册用户 B 并加入项目成员（使下拉有两个候选）
    reg = httpx.post(f"{api_base}/api/auth/register", json={"username": user_b, "password": "secret123"}, timeout=10)
    assert reg.status_code in (200, 201), f"注册用户 B 失败: {reg.status_code} {reg.text[:120]}"
    add = httpx.post(f"{api_base}/api/projects/{proj_id}/members",
                     json={"username": user_b, "role": "member"}, headers=headers, timeout=10)
    assert add.status_code in (200, 201), f"添加成员失败: {add.status_code} {add.text[:120]}"

    memb = httpx.get(f"{api_base}/api/projects/{proj_id}/members", headers=headers, timeout=10).json()["items"]
    user_a_id = next(m["user_id"] for m in memb if m["username"] == user_a)
    user_b_id = next(m["user_id"] for m in memb if m["username"] == user_b)
    assert user_a_id and user_b_id, "未能解析成员 id"

    # 重新进入 Story 页，触发 members() 重新加载
    page.goto(web_base + "/story/" + str(story_id))
    page.wait_for_selector("#s-new-task", state="visible", timeout=10000)

    # 3) 新建任务并指派给用户 B
    task = "B02任务" + ts
    _open_create(page, "#s-new-task")
    # 等待负责人下拉渲染出用户 B 选项（证明 members 已加载）
    # 注：<option> 元素在 select 未展开时被判为 hidden，故用 state="attached"
    page.wait_for_selector(f'#create-assignee option[value="{user_b_id}"]', state="attached", timeout=10000)
    page.fill("#create-title", task)
    page.select_option("#create-assignee", label=user_b)
    page.click('#create-form [type="submit"]')
    page.wait_for_selector("#create-modal", state="detached", timeout=10000)
    _wait_text(page, "#app", task)

    # 打开任务详情（按 task id 直接跳转，避免依赖列表渲染时机/选择器）
    tasks_resp = httpx.get(f"{api_base}/api/stories/{story_id}/tasks", headers=headers, timeout=10)
    td = next((t for t in tasks_resp.json() if t["title"] == task), None)
    assert td, "未在 story 中找到刚创建的任务"
    page.goto(web_base + "/task/" + str(td["id"]))
    page.wait_for_selector("#task-assignee", state="visible", timeout=10000)

    # 4) 详情「负责人」显示用户 B；API assignee_id == B.id
    meta = page.locator(".task-meta-item", has_text="负责人").locator(".meta-value")
    meta_text = meta.text_content() or ""
    assert user_b in meta_text, f"详情负责人未显示用户 B: {meta_text}"
    assert td["assignee_id"] == user_b_id, f"API assignee_id 应为 {user_b_id}，实际 {td['assignee_id']}"

    # 5) 详情编辑表单改派为用户 A
    page.select_option("#task-assignee", label=user_a)
    _wait_toast(page, "负责人已更新")
    # 重新读取 API 确认已改派
    td2 = next((t for t in httpx.get(f"{api_base}/api/stories/{story_id}/tasks", headers=headers, timeout=10).json()
                if t["title"] == task))
    assert td2["assignee_id"] == user_a_id, f"改派后 API assignee_id 应为 {user_a_id}，实际 {td2['assignee_id']}"
    # 详情元信息随之更新
    meta_text2 = (page.locator(".task-meta-item", has_text="负责人").locator(".meta-value").text_content() or "")
    assert user_a in meta_text2, f"改派后详情负责人未显示用户 A: {meta_text2}"

    # 6) 看板视图显示负责人头像（assignee-chip）
    page.goto(web_base + "/story/" + str(story_id))
    page.wait_for_selector("#s-new-task", state="visible", timeout=10000)
    board_btn = page.locator("button:has-text('看板')")
    if board_btn.count() > 0:
        board_btn.first.click()
        page.wait_for_timeout(1200)
    chip = page.locator(".assignee-chip")
    assert chip.count() >= 1, "看板卡片未显示负责人头像（.assignee-chip）"

    # 7) 错误汇总（监听器已在测试开始注册）
    page.wait_for_timeout(800)
    assert not page_errors, f"存在 pageerror: {page_errors[:3]}"
    assert not console_errors, f"存在 console error: {console_errors[:3]}"
    assert not failed_requests, f"存在静态资源加载失败: {failed_requests[:3]}"
