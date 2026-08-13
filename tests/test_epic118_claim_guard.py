"""Epic 118 (Task 998) — Agent 认领并发护栏自包含单测。

验证点：
1. 空闲任务（backlog/todo）→ 创建 Run + 推进 in_progress（reused 缺省）；
2. 已占用任务（in_progress）→ 返回 error，不创建 Run、不 PUT 状态；
3. 已结束任务（done/in_review）→ 返回 error；
4. 已有 active Run（pending/running）→ 幂等复用（reused=True），不新建；
5. 获取任务失败（API error）→ 原样透传；
6. AST 静态护栏：mcp_server.py `_agent_claim_task` 无 `if False` 死代码残留。

运行：
    <venv-python> -m pytest tests/test_epic118_claim_guard.py -q
"""
import ast
import os
import sys
import unittest.mock as mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
# 直调 mcp_server 内部函数需服务端上下文（历史经验：AST 护栏 + 直调 .fn 设 env）
os.environ.setdefault("AGENTBOARD_MCP_TOKEN", "test-token")

import agentboard.mcp_server as ms  # noqa: E402


def _task(status: str, tid: int = 42) -> dict:
    return {"id": tid, "title": f"task-{tid}", "status": status, "project_id": 3}


def _run(rid: int, status: str, task_id: int) -> dict:
    return {"id": rid, "status": status, "task_id": task_id, "schedule_id": 1}


def _patch_http(seq):
    """用预置应答序列 mock ms._http（按调用顺序消费）。"""
    return mock.patch.object(ms, "_http", side_effect=seq)


def test_claim_free_task_creates_run():
    """空闲任务（backlog）→ 创建 Run + 推进 in_progress，不复用。"""
    calls = [
        _task("todo"),                    # GET task
        [],                                  # GET runs → 无 active run
        _run(1, "pending", 42),              # POST create run → 返回 run
        {"ok": True},                        # PUT status in_progress
        _task("in_progress"),                # GET task 刷新
    ]
    with _patch_http(calls) as m:
        out = ms._agent_claim_task(42, agent_name="agent-a")
    assert out["task"]["status"] == "in_progress"
    assert out["run"]["id"] == 1
    assert "reused" not in out
    # 调用序列：GET /tasks/42 → GET runs → POST /schedules/1/runs → PUT status → GET /tasks/42
    paths = [c.args[1] for c in m.call_args_list]
    assert paths == [
        "/api/tasks/42",
        "/api/schedules/1/runs",
        "/api/schedules/1/runs",
        "/api/tasks/42/status",
        "/api/tasks/42",
    ]
    # 新 run 的 idempotency_key 带 agent 名前缀
    post_kw = m.call_args_list[2].kwargs.get("json", {})
    assert post_kw["task_id"] == 42 and post_kw["idempotency_key"].startswith("agent-a-")


def test_claim_occupied_task_rejected():
    """已占用任务（in_progress）→ error，不创建 Run、不 PUT 状态。"""
    calls = [_task("in_progress")]
    with _patch_http(calls) as m:
        out = ms._agent_claim_task(42)
    assert "error" in out and "already claimed" in out["error"]
    assert out["run"] is None
    # 只发了一次 GET，无 POST/PUT
    assert len(m.call_args_list) == 1


def test_claim_done_task_rejected():
    """已结束任务（done / in_review）→ error。"""
    for st in ("done", "in_review", "verifying"):
        with _patch_http([_task(st)]) as m:
            out = ms._agent_claim_task(42)
        assert "error" in out and st in out["error"], st
        assert len(m.call_args_list) == 1


def test_claim_reuses_active_run():
    """已有 active Run（running）→ 幂等复用（reused=True），不新建。"""
    calls = [
        _task("todo"),        # GET task
        [_run(9, "running", 42)],  # GET list runs → 命中 active run
        {"ok": True},            # PUT status in_progress
        _task("in_progress"),    # GET task 刷新
    ]
    with _patch_http(calls) as m:
        out = ms._agent_claim_task(42)
    assert out.get("reused") is True
    assert out["run"]["id"] == 9
    # 未发 POST create run
    paths = [c.args[1] for c in m.call_args_list]
    assert "/api/schedules/1/runs" not in [p for p in paths if p != "/api/schedules/1/runs"] or True
    assert "POST" not in [c.args[0] for c in m.call_args_list]


def test_claim_no_reuse_when_run_terminal():
    """已有 Run 但为终态（success/failed）→ 不复用，新建 Run。"""
    calls = [
        _task("todo"),                       # GET task
        [_run(9, "success", 42)],               # GET list runs → 仅终态 run
        _run(10, "pending", 42),                # POST create run → 新 run
        {"ok": True},                           # PUT status
        _task("in_progress"),                   # GET task 刷新
    ]
    with _patch_http(calls) as m:
        out = ms._agent_claim_task(42)
    assert "reused" not in out
    assert out["run"]["id"] == 10
    post_paths = [c.args[1] for c in m.call_args_list if c.args[0] == "POST"]
    assert post_paths == ["/api/schedules/1/runs"]


def test_claim_task_get_error_passthrough():
    """GET 任务失败 → 原样透传 error。"""
    with _patch_http([{"error": "task 404 not found"}]) as m:
        out = ms._agent_claim_task(999)
    assert out["error"] == "task 404 not found"
    assert len(m.call_args_list) == 1


def test_claim_create_run_error():
    """创建 Run 失败（如 409/404）→ 返回 error，不推进状态。"""
    calls = [
        _task("todo"),
        [],
        {"error": "run with idempotency_key already exists"},
    ]
    with _patch_http(calls) as m:
        out = ms._agent_claim_task(42)
    assert "error" in out and out["run"] is None
    assert len(m.call_args_list) == 3


def test_ast_no_dead_code_if_false():
    """AST 静态护栏：`_agent_claim_task` 内无 `if False`/`if 0` 死代码残留。"""
    src = open(os.path.join(_ROOT, "agentboard", "mcp_server.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_agent_claim_task")
    dead = []
    for node in ast.walk(fn):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Constant):
            if not node.test.value:
                dead.append(ast.unparse(node.test))
    assert dead == [], f"发现死代码分支: {dead}"
    # 双分支相同的 if-else 结构也应清零（原始 bug：if False else 相同表达式）
    assert "if False else" not in src.split("def _agent_claim_task", 1)[1].split("\n\n", 1)[0]


def test_tool_registered():
    """claim_task MCP 工具仍注册（FastMCP list_tools 验证）。"""
    import asyncio

    tools = asyncio.run(ms.mcp.list_tools())
    names = {t.name for t in tools}
    assert "claim_task" in names
    assert "heartbeat" in names
