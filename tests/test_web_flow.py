"""AgentBoard Web 端到端自动化测试：同时启动【真实 API】与【真实 Web】服务。

覆盖场景（均走 Web SPA 实际消费的 REST 端点）：
- Web 服务正确托管 Angular SPA、注入 AGENTBOARD_API 并提供带哈希的构建资源
- 注册 / 登录 / 重复注册 / 错误密码 / me
- 创建并修改 各种 ticket：project / epic / story / task / bug
- 读取列表（首页项目列表、epic 列表、story 列表、task/bug 列表、搜索）

说明：Web SPA 目前通过 fetch 直接调用 API，本测试按 SPA 的行为对“已启动的 API”发请求，
因此等价于在浏览器里操作页面所触发的网络请求；同时校验 Web 服务真的把 SPA 送到了浏览器。

运行：
    PYTHONPATH=. python -m pytest tests/test_web_flow.py -q
    PYTHONPATH=. python tests/test_web_flow.py        # 直接运行（同样先起 api+web 再测）
"""
import os
import re
import sys
import socket
import subprocess
import tempfile
import time

import httpx
import pytest

# 独立临时数据库（与 test_smoke / test_backend_flow 隔离）
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_MCP_BACKEND"] = "db"

# 强制重载 agentboard，使 engine 绑定到上面的临时库
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard.database import init_db
from agentboard.models import ItemType, Status

init_db()

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(app_import: str, port: int, extra_env: dict | None = None) -> subprocess.Popen:
    """以独立子进程真实拉起 uvicorn 服务（api 或 web）。"""
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    # 重定向到 DEVNULL：避免 Windows 下 pytest 捕获导致子进程 sys.stdout 为 None，
    # 进而触发 uvicorn 日志 formatter 的 sys.stdout.isatty() 崩溃 / 无效句柄继承。
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_import,
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait(url: str, timeout: float = 15.0) -> None:
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
    """同时启动真实 API 与真实 Web 服务，返回 (api_base, web_base)。"""
    api_port = _free_port()
    web_port = _free_port()
    api_proc = _start_server("agentboard.api:app", api_port)
    # Web 服务把页面接到运行中的 API
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


# ---------------- Web 服务托管 SPA ----------------
def test_web_serves_spa(servers):
    api_base, web_base = servers
    html = httpx.get(web_base + "/").text
    assert "AgentBoard" in html, "首页未包含应用标题"
    # 页面必须把 SPA 接到当前运行中的 API
    assert api_base in html, "Web 未把页面注入到运行中的 API 地址"

    script_match = re.search(r'<script src="([^"]+\.js)"', html)
    assert script_match, "Angular 入口脚本未注入"
    js = httpx.get(web_base + "/" + script_match.group(1).lstrip("/"))
    assert js.status_code == 200, "Angular bundle 未提供"
    # CSS 可能以 style.css 或 styles-<hash>.css 提供，优先尝试 style.css（完整未哈希版本）
    css_url = web_base + "/static/style.css"
    css_resp = httpx.get(css_url, timeout=5)
    if css_resp.status_code != 200:
        # 回退：尝试从 HTML 提取哈希 CSS 文件名
        css_match = re.search(r'href="(styles-[A-Za-z0-9]+\.css)"', html)
        if css_match:
            css_url = web_base + "/static/" + css_match.group(1)
            css_resp = httpx.get(css_url, timeout=5)
    assert css_resp.status_code == 200, f"CSS 文件未提供（{css_url}）"
    assert len(js.content) > 100_000, "Angular bundle 内容异常"
    assert "app-root" in html, "Angular 根组件缺失"
    deep_link = httpx.get(web_base + "/project/123")
    assert deep_link.status_code == 200 and "app-root" in deep_link.text, "Angular 深链接未回退到 SPA"
    assert "agentboard_token" in js.text, "Epic 7 token 持久化缺失"
    assert "auth-form" in js.text, "Epic 7 登录表单缺失"
    assert ".auth-card" in css_resp.text and ".user-chip" in css_resp.text, "Epic 7 登录/用户样式缺失"

    assert "--grad:" in css_resp.text and ".crumb-current" in css_resp.text, "品牌 token 或关键样式缺失"
    assert ".entity-item-actions" in css_resp.text and ".ei-act" in css_resp.text, "A-19 hover 操作样式缺失"
    assert ".group-head" in css_resp.text and ".select-sm" in css_resp.text, "B-06 分组标题/下拉样式缺失"


# ---------------- 注册 / 登录 ----------------
def test_web_register_login(servers):
    api_base, _ = servers
    with httpx.Client(base_url=api_base) as c:
        r = c.post("/api/auth/register", json={"username": "webuser", "password": "password123"})
        assert r.status_code == 201, r.text
        token = r.json()["token"]

        assert c.post("/api/auth/login", json={"username": "webuser", "password": "password123"}).status_code == 200
        assert c.post("/api/auth/login", json={"username": "webuser", "password": "bad"}).status_code == 401
        assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        # 重复注册 -> 409
        assert c.post("/api/auth/register", json={"username": "webuser", "password": "password456"}).status_code == 409


# ---------------- 创建 / 修改 各种 ticket + 读取列表 ----------------
def test_web_ticket_crud_and_lists(servers):
    api_base, _ = servers
    with httpx.Client(base_url=api_base) as c:
        # 创建
        p = c.post("/api/projects", json={"name": "Web 项目", "key": "WEB"}).json()
        assert p["id"] > 0 and p["key"] == "WEB"
        e = c.post(f"/api/projects/{p['id']}/epics", json={"title": "E1"}).json()
        st = c.post(f"/api/epics/{e['id']}/stories", json={"title": "S1"}).json()
        t = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "T1", "type": "task"}).json()
        b = c.post(f"/api/stories/{st['id']}/tasks",
                   json={"project_id": p["id"], "title": "B1", "type": "bug"}).json()
        assert t["type"] == ItemType.TASK and b["type"] == ItemType.BUG

        # 修改（各类 ticket 的 PATCH）
        assert c.patch(f"/api/projects/{p['id']}", json={"description": "d2"}).json()["description"] == "d2"
        assert c.patch(f"/api/epics/{e['id']}", json={"status": "todo"}).json()["status"] == "todo"
        assert c.patch(f"/api/stories/{st['id']}", json={"title": "S1-x"}).json()["title"] == "S1-x"
        assert c.patch(f"/api/tasks/{t['id']}", json={"spec": "# spec", "title": "T1-x"}).json()["spec"] == "# spec"
        # 状态流转（合法）
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "todo"}).status_code == 200
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "in_progress"}).status_code == 200
        # 状态流转（非法：in_progress -> backlog）
        assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "backlog"}).status_code == 400

        # 读取列表（Web 渲染时拉取的列表接口）
        projects = c.get("/api/projects").json().get("items", [])       # 首页：项目列表
        assert any(x["id"] == p["id"] for x in projects)
        epics = c.get(f"/api/projects/{p['id']}/epics").json()        # 项目页：epic 列表
        if isinstance(epics, dict):
            epics = epics.get("items", [])
        assert any(x["id"] == e["id"] for x in epics)
        stories = c.get(f"/api/epics/{e['id']}/stories").json()      # epic 页：story 列表
        if isinstance(stories, dict):
            stories = stories.get("items", [])
        assert any(x["id"] == st["id"] for x in stories)
        tasks = c.get(f"/api/stories/{st['id']}/tasks").json()       # story 页：task/bug 列表
        if isinstance(tasks, dict):
            tasks = tasks.get("items", [])
        ids = {x["id"] for x in tasks}
        assert t["id"] in ids and b["id"] in ids

        # 搜索（按 type 过滤 bug）
        bugs = c.get("/api/tasks", params={"project_id": p["id"], "type": "bug"}).json()
        if isinstance(bugs, dict):
            bugs = bugs.get("items", [])
        assert len(bugs) == 1 and bugs[0]["id"] == b["id"]


if __name__ == "__main__":
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
        test_web_serves_spa((api_base, web_base))
        test_web_register_login((api_base, web_base))
        test_web_ticket_crud_and_lists((api_base, web_base))
        print("WEB FLOW OK (api + web live)")
    finally:
        for p in (api_proc, web_proc):
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
