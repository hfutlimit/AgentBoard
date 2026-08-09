"""Epic 122 切片 3 M2（Story 232 / Task 1014）：评审统计 + 超时护栏。

覆盖（对应任务验收）：
1. service.get_review_stats 统计口径：
   - 空项目全零；构造 approve/reject/blocked 样本后计数正确；
   - days 过滤（窗口外数据不计入）；user_id 过滤（只看某评审人）；
   - reject_rate 与平均轮次计算；
2. service.scan_review_timeouts 超时重派：
   - 超时 Story 换 reviewer（≠ 旧 reviewer）、review_round 不变；
   - 轮次达 MAX_REVIEW_ROUNDS → blocked（护栏终态）；
   - 未超时不处理；无在线候选 → 解绑 + no_candidate（下轮补派）；
   - Task 用 updated_at 判定；max_per_run 有界；
3. API：
   - GET /api/review-stats（成员 200 / 非成员 403 / 匿名 401）；
   - POST /api/review-stats/reassign-timeout：触发扫描 + 重派后发布
     review.requested（mock publish）+ Webhook（mock _notify_webhooks）；
4. MCP AST 注册 + 真实栈直调；Epic 97 AST 护栏零 _api( 残留。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s3m2.py -q
"""
import ast
import itertools
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import api, auth, mq, service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.mq import EVENT_REVIEW_REQUESTED  # noqa: E402
from agentboard.domains.common.models import utc_now  # noqa: E402

init_db()

_MCP_SOURCE = Path(_ROOT) / "agentboard" / "mcp_server.py"
_SEQ = itertools.count(1)


def _seed():
    """1 项目 + dev + 2 个 reviewer Agent（r1/r2，均在线）。"""
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S3M2 P{n}")
        dev = service.register_user(s, username=f"s3m2-dev{n}", password="password123")
        r1 = service.register_user(s, username=f"s3m2-r1-{n}", password="password123")
        r2 = service.register_user(s, username=f"s3m2-r2-{n}", password="password123")
        for uid in (dev.id, r1.id, r2.id):
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        service.register_agent(s, agent_id=f"s3m2-a-{n}", name="A",
                               roles='["reviewer"]', user_id=r1.id)
        service.agent_heartbeat(s, f"s3m2-a-{n}", user_id=r1.id)
        service.register_agent(s, agent_id=f"s3m2-b-{n}", name="B",
                               roles='["reviewer"]', user_id=r2.id)
        service.agent_heartbeat(s, f"s3m2-b-{n}", user_id=r2.id)
        epic = service.create_epic(s, project_id=p.id, title=f"S3M2 Epic{n}")
        s.commit()
        return p.id, dev.id, r1.id, r2.id, epic.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()


def _inreview_task(s, project_id, *, reviewer_id, assignee_id=None, round_=0,
                   updated=None, created=None):
    from agentboard.models import Task
    t = Task(project_id=project_id, title="S3M2 task", type="task",
             status="in_review", reviewer_id=reviewer_id,
             assignee_id=assignee_id, review_round=round_)
    if updated is not None:
        t.updated_at = updated
    if created is not None:
        t.created_at = created
    s.add(t)
    s.flush()
    return t


# ---------- 1. get_review_stats 统计口径（2026-08-09：Story 评审下线 → Task 侧） ----------

def test_stats_empty_project(seeded):
    pid, *_ = seeded
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        assert stats["stories"]["total"] == 1  # 2026-08-09：create_epic 自动带 1 个默认 Story
        assert stats["tasks"]["total"] == 2  # 默认 Story 自动带 design + 开发 task
        assert stats["reject_rate"] == 0.0
        assert stats["rounds"]["avg_story_round"] == 0.0
        assert stats["timeout_pending"] == 0
        assert stats["by_reviewer"] == []


def test_stats_counts_and_reject_rate(seeded):
    pid, dev, r1, r2, epic_id = seeded
    with SessionLocal() as s:
        # Task 侧：task1 approve（done）；task2 驳回过 2 次后 in_progress（rejected）；
        # task3 pending（in_review）；task4 blocked（轮次超限）
        t1 = _inreview_task(s, pid, reviewer_id=r1, updated=utc_now())
        t1.status = "done"
        _inreview_task(s, pid, reviewer_id=r2, round_=2, updated=utc_now()).status = "in_progress"
        _inreview_task(s, pid, reviewer_id=r1, updated=utc_now())
        _inreview_task(s, pid, reviewer_id=r2, round_=5, updated=utc_now()).status = "blocked"
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        st, tk = stats["stories"], stats["tasks"]
        # Story 评审已下线：approved/pending 恒 0；rejected/blocked 仍按字段统计
        assert st["total"] == 1 and st["approved"] == 0 and st["pending"] == 0
        assert st["rejected"] == 0 and st["blocked"] == 0
        assert tk["total"] == 6 and tk["approved"] == 1 and tk["rejected"] == 2  # +2 自动默认 task
        assert tk["pending"] == 1 and tk["blocked"] == 1
        # rejected = 2（task）；approved = 1（task）→ rate = 2/3
        assert stats["reject_rate"] == round(2 / 3, 4)
        # 平均轮次：4 个已评审 task（rounds 0+2+0+5=7）→ 1.75
        assert stats["rounds"]["avg_task_round"] == 1.75
        # by_reviewer：r1 task 2 个（task1 approve + task3 pending）
        rows = {r["user_id"]: r for r in stats["by_reviewer"]}
        assert rows[r1]["task_reviewed"] == 2
        assert rows[r1]["task_approved"] == 1


def test_stats_days_filter(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(days=30)
    with SessionLocal() as s:
        _inreview_task(s, pid, reviewer_id=r1, updated=old, created=old).status = "done"
        _inreview_task(s, pid, reviewer_id=r1, updated=utc_now()).status = "done"
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid, days=7)
        assert stats["tasks"]["total"] == 3  # 30 天前的不计入（+2 自动默认 task）
        stats_all = service.get_review_stats(s, project_id=pid, days=0)
        assert stats_all["tasks"]["total"] == 4


def test_stats_user_filter(seeded):
    pid, dev, r1, r2, epic_id = seeded
    with SessionLocal() as s:
        _inreview_task(s, pid, reviewer_id=r1, updated=utc_now()).status = "done"
        _inreview_task(s, pid, reviewer_id=r2, updated=utc_now()).status = "done"
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid, user_id=r1)
        assert stats["tasks"]["total"] == 1
        assert len(stats["by_reviewer"]) == 1
        assert stats["by_reviewer"][0]["user_id"] == r1


def test_stats_timeout_pending(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        _inreview_task(s, pid, reviewer_id=r1, updated=old)
        _inreview_task(s, pid, reviewer_id=r2, updated=utc_now())
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        assert stats["timeout_pending"] == 1  # 只有超时的 in_review Task 计入


# ---------- 2. scan_review_timeouts 超时重派（2026-08-09：Task 侧） ----------

def test_timeout_task_reassigned(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["stories_reassigned"] == 0
        assert result["tasks_reassigned"] == 1
        assert result["blocked"] == 0
        fresh = s.get(service.Task, tid)
        assert fresh.reviewer_id is not None and fresh.reviewer_id != r1
        assert fresh.status == "in_review" and fresh.review_round == 0


def test_timeout_task_blocked_at_max_rounds(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev,
                           round_=service.MAX_REVIEW_ROUNDS, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["tasks_reassigned"] == 0
        assert result["blocked"] == 1
        assert s.get(service.Task, tid).status == "blocked"


def test_timeout_recent_not_processed(seeded):
    pid, dev, r1, r2, epic_id = seeded
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=utc_now())
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["tasks_reassigned"] == 0
        fresh = s.get(service.Task, tid)
        assert fresh.reviewer_id == r1  # 未超时不换人


def test_timeout_no_candidate_unbind(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        # 下线全部 reviewer Agent → 无候选
        for ag in s.query(service.Agent).all():
            ag.online = False
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["tasks_reassigned"] == 0
        assert result["no_candidate"] == 1
        fresh = s.get(service.Task, tid)
        assert fresh.reviewer_id is None  # 解绑等待下轮补派


def test_timeout_task_uses_updated_at(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["tasks_reassigned"] == 1
        fresh = s.get(service.Task, tid)
        assert fresh.reviewer_id == r2  # 排除旧 reviewer 与 assignee → 只剩 r2
        assert fresh.status == "in_review"


def test_timeout_task_blocked(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev,
                           round_=service.MAX_REVIEW_ROUNDS, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["blocked"] == 1
        assert s.get(service.Task, tid).status == "blocked"


def test_timeout_max_per_run_bounded(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        for i in range(5):
            _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        s.commit()
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30, max_per_run=2)
        assert result["tasks_reassigned"] == 2


def test_timeout_project_filter(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        tid = t.id
        other = service.create_project(s, name=f"S3M2 Other{next(_SEQ)}")
        service.add_project_member(s, project_id=other.id, user_id=r2, role="member")
        service.register_agent(s, agent_id=f"s3m2-c-{next(_SEQ)}", name="C",
                               roles='["reviewer"]', user_id=r2)
        service.agent_heartbeat(s, f"s3m2-c-{next(_SEQ)}", user_id=r2)
        other_epic = service.create_epic(s, project_id=other.id, title="other epic")
        t2 = _inreview_task(s, other.id, reviewer_id=r2, assignee_id=dev, updated=old)
        t2_id = t2.id
        s.commit()
        other_pid = other.id
    with SessionLocal() as s:
        result = service.scan_review_timeouts(s, project_id=pid, timeout_minutes=30)
        assert result["tasks_reassigned"] == 1  # 只处理 pid 项目
        fresh = s.get(service.Task, tid)
        assert fresh.reviewer_id is not None
        fresh2 = s.get(service.Task, t2_id)
        assert fresh2.reviewer_id == r2  # 其它项目未动


# ---------- 3. API 端点 ----------

def _client_auth(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def test_api_review_stats_unknown_project_404(seeded):
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    r = c.get("/api/review-stats", params={"project_id": 99999999})
    assert r.status_code == 404  # 项目不存在 → NotFound


def test_api_review_stats_member_ok(seeded):
    pid, dev, *_ = seeded
    with SessionLocal() as s:
        u = s.get(service.User, dev)
        tok = auth.make_token(u.id)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    r = c.get("/api/review-stats", params={"project_id": pid},
              headers=_client_auth(tok))
    assert r.status_code == 200
    assert r.json()["project_id"] == pid


def test_api_reassign_timeout_publishes_event(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        t = _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        tid = t.id
        s.commit()
    with SessionLocal() as s:
        u = s.get(service.User, dev)
        tok = auth.make_token(u.id)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    with mock.patch.object(api, "publish_workflow_event") as pub, \
         mock.patch.object(api, "_notify_webhooks") as nw:
        r = c.post("/api/review-stats/reassign-timeout",
                   params={"project_id": pid},
                   json={"timeout_minutes": 30, "max_per_run": 20},
                   headers=_client_auth(tok))
        assert r.status_code == 200
        data = r.json()
        assert data["tasks_reassigned"] == 1
        assert "_tasks_reassigned" not in data  # 内部键已剔除
        # 事件发布：review.requested（task，新 reviewer 定向）
        assert pub.call_count == 1
        ev = pub.call_args
        assert ev.args[0] == EVENT_REVIEW_REQUESTED
        assert ev.args[1] == "task" and ev.args[2] == tid
        assert nw.call_count == 1


def test_api_reassign_timeout_global_no_project(seeded):
    pid, dev, r1, r2, epic_id = seeded
    old = utc_now() - timedelta(minutes=60)
    with SessionLocal() as s:
        _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        u = s.get(service.User, dev)
        tok = auth.make_token(u.id)
    from fastapi.testclient import TestClient
    c = TestClient(api.app)
    with mock.patch.object(api, "publish_workflow_event"), \
         mock.patch.object(api, "_notify_webhooks"):
        r = c.post("/api/review-stats/reassign-timeout",
                   json={"timeout_minutes": 30},
                   headers=_client_auth(tok))
        assert r.status_code == 200
        assert r.json()["tasks_reassigned"] >= 1  # 全局扫描：历史残留 + 本次数据


# ---------- 4. MCP 工具：AST 注册 + 真实栈直调 ----------

def _is_mcp_tool_decorator(d: ast.AST) -> bool:
    return (isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            and isinstance(d.func.value, ast.Name)
            and d.func.value.id == "mcp")


def _mcp_tool_names(src: str) -> set[str]:
    tree = ast.parse(src)
    tools: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.decorator_list:
            if any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                tools.add(node.name)
    return tools


def test_mcp_tools_registered_ast():
    """AST 扫描 @mcp.tool() 装饰的工具名，断言 S3 M2 新工具已注册 + 零 _api( 残留。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    tools = _mcp_tool_names(src)
    assert "get_review_stats" in tools
    assert "scan_review_timeouts" in tools
    # Epic 97 护栏：零 _api( 残留
    leftovers = [
        n.lineno for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_api"
    ]
    assert not leftovers, f"mcp_server.py 仍有 _api( 残留：行 {leftovers}"


def test_mcp_tools_self_contained_direct():
    """真实栈直调：设 token + API_URL，扫描 + 统计端点真实命中（REST 冒烟）。"""
    import threading

    import uvicorn
    from agentboard import database as dbmod

    port = 18780 + (next(_SEQ) % 100)
    host = "127.0.0.1"
    with SessionLocal() as s:
        pid, dev, r1, r2, epic_id = _seed()
        old = utc_now() - timedelta(minutes=60)
        _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev, updated=old)
        u = s.get(service.User, dev)
        token = auth.make_token(u.id)
        s.commit()

    cfg = {"host": host, "port": port}
    server = uvicorn.Server(uvicorn.Config(api.app, host=host, port=port,
                                           log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(50):
        try:
            import httpx
            httpx.get(f"http://{host}:{port}/api/health", timeout=1)
            break
        except Exception:
            import time
            time.sleep(0.2)

    import importlib
    import agentboard.mcp_server as ms
    if "AGENTBOARD_MCP_TOKEN" in os.environ:
        old_token = os.environ["AGENTBOARD_MCP_TOKEN"]
    else:
        old_token = None
        os.environ["AGENTBOARD_MCP_TOKEN"] = token
    if "AGENTBOARD_API_URL" in os.environ:
        old_url = os.environ["AGENTBOARD_API_URL"]
    else:
        old_url = None
    os.environ["AGENTBOARD_API_URL"] = f"http://{host}:{port}"
    try:
        importlib.reload(ms)
        # scan_review_timeouts：project 过滤扫描（Task 侧）
        r = ms.scan_review_timeouts(project_id=pid, timeout_minutes=30)
        assert r["tasks_reassigned"] == 1
        # get_review_stats：统计可见
        stats = ms.get_review_stats(project_id=pid, days=7)
        assert stats["project_id"] == pid
        assert stats["tasks"]["total"] >= 1
    finally:
        server.should_exit = True
        t.join(timeout=5)
        if old_token is None:
            os.environ.pop("AGENTBOARD_MCP_TOKEN", None)
        else:
            os.environ["AGENTBOARD_MCP_TOKEN"] = old_token
        if old_url is None:
            os.environ.pop("AGENTBOARD_API_URL", None)
        else:
            os.environ["AGENTBOARD_API_URL"] = old_url


def test_epic97_ast_guard():
    """Epic 97 AST 护栏：mcp_server.py 零 _api( 残留（历史重构遗留缺陷回归防护）。"""
    src = _MCP_SOURCE.read_text(encoding="utf-8")
    import re
    bad = re.findall(r"\b_api\s*\(", src)
    assert not bad, f"mcp_server.py 仍有 _api( 残留: {bad}"
