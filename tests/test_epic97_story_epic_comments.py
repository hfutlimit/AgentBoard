"""Epic 97 — Story / Epic 评论功能（后端全链路 + MCP 工具）回归护栏。

背景
----
评论原本只支持 Task 实体（``comments.task_id`` NOT NULL）。本次扩展：
- ``comments`` 新增 ``story_id`` / ``epic_id`` 可空外键，``task_id`` 改可空；
- 新增 ``GET/POST /api/stories/{sid}/comments`` 与 ``GET/POST /api/epics/{eid}/comments``；
- Service 层 ``create_comment`` / ``list_comments`` 泛化为三实体；
- MCP 新增 ``list_story_comments`` / ``add_story_comment`` / ``list_epic_comments`` / ``add_epic_comment``。

本模块沿用 Epic 97 既有模式：真实拉起 uvicorn 子进程（audit_log_middleware 与
TestClient 不兼容），``AGENTBOARD_REQUIRE_AUTH=1`` 同时验证访问控制。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic97_story_epic_comments.py -q
"""
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 独立临时数据库（与其它测试隔离），子进程通过环境变量继承同一个库
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import mcp_server  # noqa: E402
from agentboard.database import init_db  # noqa: E402

init_db()  # 跑完整 alembic 迁移链（含 n1o2p3q4r5s6_add_story_epic_comments）


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_server(port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = _ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["AGENTBOARD_REQUIRE_AUTH"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=_ROOT, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_ready(base: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/api/meta", timeout=1).status_code == 200:
                return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError(f"API 服务在 {base} 启动超时")


def _register_login(base: str, username: str, password: str) -> dict:
    c = httpx.Client(base_url=base, timeout=30)
    c.post("/api/auth/register", json={"username": username, "password": password})
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"{username} 登录失败：{r.text}"
    c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return c


@pytest.fixture(scope="module")
def stack():
    """真实拉起 API（REQUIRE_AUTH=1），预置 project/epic/story/task 与两位用户。"""
    port = _free_port()
    proc = _start_server(port)
    base = f"http://127.0.0.1:{port}"
    prev_url = mcp_server.API_URL
    try:
        _wait_ready(base)
        owner = _register_login(base, "e97owner", "e97owner123")
        outsider = _register_login(base, "e97out", "e97out123")

        r = owner.post("/api/projects", json={"name": "Epic97 Story/Epic 评论验证"})
        assert r.status_code in (200, 201), r.text
        pid = r.json()["id"]
        eid = owner.post(f"/api/projects/{pid}/epics",
                         json={"title": "评论父实体"}).json()["id"]
        sid = owner.post(f"/api/epics/{eid}/stories",
                         json={"title": "可评论的 Story"}).json()["id"]
        tid = owner.post(f"/api/stories/{sid}/tasks",
                         json={"project_id": pid, "title": "可评论的 Task",
                               "type": "dev"}).json()["id"]

        # MCP 工具指向同一真实栈
        mcp_server.API_URL = base
        os.environ["AGENTBOARD_MCP_TOKEN"] = owner.headers["Authorization"].removeprefix("Bearer ")

        yield {"base": base, "owner": owner, "outsider": outsider,
               "project_id": pid, "epic_id": eid, "story_id": sid, "task_id": tid}
        owner.close()
        outsider.close()
    finally:
        mcp_server.API_URL = prev_url
        os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _add_comment(c, url: str, author: str, content: str, expected: int = 201):
    r = c.post(url, json={"author": author, "content": content})
    assert r.status_code == expected, f"{url} 返回 {r.status_code}：{r.text}"
    return r.json()


# ===================== REST 全链路 =====================

def test_story_epic_task_comment_crud(stack):
    """三种实体的评论增/查/删全链路 + 序列化字段正确。"""
    owner, base = stack["owner"], stack["base"]
    sid, eid, tid = stack["story_id"], stack["epic_id"], stack["task_id"]

    sc = _add_comment(owner, f"/api/stories/{sid}/comments", "甲", "Story 评论一")
    assert sc["story_id"] == sid and sc["task_id"] is None and sc["epic_id"] is None, sc

    ec = _add_comment(owner, f"/api/epics/{eid}/comments", "乙", "Epic 评论一")
    assert ec["epic_id"] == eid and ec["task_id"] is None and ec["story_id"] is None, ec

    tc = _add_comment(owner, f"/api/tasks/{tid}/comments", "丙", "Task 评论一（回归）")
    assert tc["task_id"] == tid and tc["story_id"] is None and tc["epic_id"] is None, tc

    stories = owner.get(f"/api/stories/{sid}/comments").json()
    assert [x["content"] for x in stories] == ["Story 评论一"], stories
    epics = owner.get(f"/api/epics/{eid}/comments").json()
    assert [x["content"] for x in epics] == ["Epic 评论一"], epics
    tasks = owner.get(f"/api/tasks/{tid}/comments").json()
    assert [x["content"] for x in tasks] == ["Task 评论一（回归）"], tasks

    # 评论不串实体
    assert owner.get(f"/api/epics/{eid}/comments").json() != stories
    assert owner.get(f"/api/stories/{sid}/comments").json() != epics

    # DELETE 通用端点可删任意实体评论
    assert owner.delete(f"/api/comments/{sc['id']}").json() == {"ok": True}
    assert owner.get(f"/api/stories/{sid}/comments").json() == []
    assert owner.delete(f"/api/comments/{ec['id']}").status_code == 200
    assert owner.delete(f"/api/comments/{tc['id']}").status_code == 200
    assert owner.delete(f"/api/comments/{sc['id']}").status_code == 404  # 已删


def test_story_comment_not_found_and_invalid(stack):
    """不存在的实体 404；空 author/content 422。"""
    owner = stack["owner"]
    r = owner.get("/api/stories/999999/comments")
    assert r.status_code == 404, r.text
    r = owner.post("/api/stories/999999/comments",
                   json={"author": "a", "content": "x"})
    assert r.status_code == 404, r.text
    r = owner.post(f"/api/epics/{stack['epic_id']}/comments",
                   json={"author": "", "content": "x"})
    assert r.status_code == 422, r.text


def test_mention_notification_from_story_epic_comments(stack):
    """Story/Epic 评论里的 @username 也要触发 mentioned 通知。"""
    owner = stack["owner"]
    _add_comment(owner, f"/api/stories/{stack['story_id']}/comments",
                 "甲", "给 @e97out 的 Story 提及")
    _add_comment(owner, f"/api/epics/{stack['epic_id']}/comments",
                 "甲", "给 @e97out 的 Epic 提及")

    outsider = stack["outsider"]
    n = outsider.get("/api/notifications").json()["items"]
    titles = [x.get("title", "") for x in n]
    assert any("在评论中提到了你" in t for t in titles), titles
    assert any("story" in x.get("link", "") for x in n), n
    assert any("epic" in x.get("link", "") for x in n), n


def test_access_control_on_story_epic_comments(stack):
    """私有项目：非成员 403；加入成员后读写放行。"""
    owner, outsider = stack["owner"], stack["outsider"]
    pid, sid, eid = stack["project_id"], stack["story_id"], stack["epic_id"]

    # 未加入成员 → 读/写均 403
    assert outsider.get(f"/api/stories/{sid}/comments").status_code == 403
    assert outsider.get(f"/api/epics/{eid}/comments").status_code == 403
    assert outsider.post(f"/api/stories/{sid}/comments",
                         json={"author": "x", "content": "y"}).status_code == 403
    assert outsider.post(f"/api/epics/{eid}/comments",
                         json={"author": "x", "content": "y"}).status_code == 403

    # owner 添加成员后放行
    r = owner.post(f"/api/projects/{pid}/members",
                   json={"username": "e97out", "role": "member"})
    assert r.status_code in (200, 201), r.text
    assert outsider.get(f"/api/stories/{sid}/comments").status_code == 200
    r = outsider.post(f"/api/epics/{eid}/comments",
                      json={"author": "外", "content": "成员评论"})
    assert r.status_code == 201, r.text


def test_cascade_delete_removes_comments(stack):
    """删除 Story / Epic 时其评论级联清理。"""
    owner = stack["owner"]
    r = owner.post("/api/projects", json={"name": "级联验证"})
    pid = r.json()["id"]
    eid = owner.post(f"/api/projects/{pid}/epics",
                     json={"title": "待删 Epic"}).json()["id"]
    sid = owner.post(f"/api/epics/{eid}/stories",
                     json={"title": "待删 Story"}).json()["id"]
    tid = owner.post(f"/api/stories/{sid}/tasks",
                     json={"project_id": pid, "title": "待删 Task",
                           "type": "dev"}).json()["id"]

    c1 = _add_comment(owner, f"/api/stories/{sid}/comments", "甲", "story 评论")
    c2 = _add_comment(owner, f"/api/epics/{eid}/comments", "乙", "epic 评论")
    c3 = _add_comment(owner, f"/api/tasks/{tid}/comments", "丙", "task 评论")

    # 删 Story → story 评论 + task 评论消失，epic 评论仍在
    assert owner.delete(f"/api/stories/{sid}").status_code == 200
    assert owner.get(f"/api/stories/{sid}/comments").status_code == 404
    # 评论无 GET 端点，存在性用 DELETE 探测：200=存在（已删），404=已不存在
    assert owner.delete(f"/api/comments/{c1['id']}").status_code == 404
    assert owner.delete(f"/api/comments/{c3['id']}").status_code == 404
    assert owner.delete(f"/api/comments/{c2['id']}").status_code == 200

    # 删 Epic → epic 评论消失
    assert owner.delete(f"/api/epics/{eid}").status_code == 200
    assert owner.delete(f"/api/comments/{c2['id']}").status_code == 404


# ===================== MCP 工具 =====================

def test_mcp_story_epic_comment_tools(stack):
    """4 个新 MCP 工具真调：list/add 均可用且落库。"""
    sid, eid = stack["story_id"], stack["epic_id"]

    r = mcp_server.add_story_comment(sid, "agent", "MCP story 评论")
    assert isinstance(r, dict) and "error" not in r, f"add_story_comment 失败：{r!r}"
    assert r.get("story_id") == sid, r

    got = mcp_server.list_story_comments(sid)
    assert isinstance(got, list), f"list_story_comments 异常：{got!r}"
    assert any(c.get("content") == "MCP story 评论" for c in got), got

    r = mcp_server.add_epic_comment(eid, "agent", "MCP epic 评论")
    assert isinstance(r, dict) and "error" not in r, f"add_epic_comment 失败：{r!r}"
    assert r.get("epic_id") == eid, r

    got = mcp_server.list_epic_comments(eid)
    assert isinstance(got, list), f"list_epic_comments 异常：{got!r}"
    assert any(c.get("content") == "MCP epic 评论" for c in got), got

    # 既有 task 评论工具回归
    assert mcp_server.list_comments(stack["task_id"]) == []
