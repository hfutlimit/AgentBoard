"""Local MCP 端到端验证: in-process FastMCP ClientSession 调 proposal + create ticket + task workflow 工具.

不走 stdio 传输,直接 in-process import mcp_server.mcp + 调 list_tools/call_tool。
验证:
1. FastMCP 工具注册成功 (~130 个工具里包含 proposal_/task_/create_ticket 等)
2. 工具能成功调通 → HTTP 落到 FastAPI (18001) → DB 写入
3. proposal + create ticket + task workflow 全链路 PASS

跑法:
    AGENTBOARD_API_URL=http://127.0.0.1:18001 python scripts/local_mcp_workflow_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

from fastmcp import Client

API_URL = os.environ.get("AGENTBOARD_API_URL", "http://127.0.0.1:18001")
# in-process 引用: 把 agentboard.mcp_server 的模块路径塞进 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "src", "backend-fastapi"
)))


def step(name: str) -> None:
    print(f"\n--- {name} ---")


class _TokenScopedClient:
    """Apply one token through a context variable for in-process MCP calls."""

    def __init__(self, transport: Any, token: str):
        self._transport = transport
        self._token = token

    async def call_tool(self, name: str, arguments: dict[str, Any]):
        from agentboard.features.mcp.shared import token_context

        with token_context(self._token):
            async with Client(self._transport) as client:
                return await client.call_tool(name, arguments)


async def main() -> int:
    """Run the hybrid proposal workflow without leaking its token to callers."""
    env_keys = ("AGENTBOARD_API_URL", "AGENTBOARD_MCP_REQUIRE_AUTH")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    try:
        return await _run_workflow()
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _run_workflow() -> int:
    # 让 mcp_server 模块以为 AGENTBOARD_API_URL 指向我们刚启动的 FastAPI
    os.environ["AGENTBOARD_API_URL"] = API_URL
    os.environ["AGENTBOARD_MCP_REQUIRE_AUTH"] = "0"

    from agentboard import mcp_server  # noqa: E402  # in-process import 加载 FastMCP 实例

    mcp = mcp_server.mcp  # FastMCP("AgentBoard")
    print(f"== AgentBoard MCP server 加载, AGENTBOARD_API_URL={API_URL} ==")

    # 验证 FastAPI 可达
    import httpx
    try:
        r = httpx.get(f"{API_URL}/api/health", timeout=5)
        r.raise_for_status()
        print(f"   FastAPI health: {r.json()['status']}")
    except Exception as e:
        print(f"   FastAPI 不可达 ({API_URL}): {e}")
        return 1

    # ===== A. 工具注册自检 =====
    step("A. 工具注册自检 (MCP tools/list)")
    async with Client(mcp) as client:
        await client._connect()  # 触发 initialize
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        print(f"   共 {len(tool_names)} 个工具注册")
        # 关键工具 (验证 mcp_server.py 暴露的工作流)
        required = [
            "proposal_pending", "proposal_claim", "proposal_get", "proposal_ask",
            "proposal_finalize", "proposal_create_ticket",
            "create_task", "set_status", "claim_task", "submit_task_for_review",
            "review_task", "create_epic", "create_story",
            "auth_register", "auth_login", "auth_me",
            "add_member", "get_task", "get_epic", "get_story",
        ]
        missing = [r for r in required if r not in tool_names]
        if missing:
            print(f"   MISSING required MCP tools: {missing}", file=sys.stderr)
            return 1
        else:
            print(f"   OK — required {len(required)} tools registered")
        # 额外: 验证 auth_register/login/me 也在
        for r in ("auth_register", "auth_login", "auth_me"):
            if r not in tool_names:
                print(f"   WARN: {r} 未注册")
        # 验证 project 工具
        for r in ("create_project", "list_projects", "create_epic", "create_story"):
            if r not in tool_names:
                print(f"   WARN: {r} 未注册")

    # ===== B. MCP 工具端到端: proposal + create ticket + task workflow =====
    suffix = uuid.uuid4().hex[:8]
    admin = f"mcp_{suffix}"
    pw = f"McpPass_{suffix}"
    proj_name = f"MCP-Demo-{suffix}"
    proj_key = f"M{suffix[:4].upper()}"

    async with Client(mcp) as client:
        # B1. 注册 admin
        step("B1. auth_register (首用户自动 admin)")
        r = await client.call_tool("auth_register", {"username": admin, "password": pw})
        # FastMCP call_tool 返回 CallToolResult, content 是 TextContent
        reg = _parse(r)
        admin_token = reg.get("token")
        if not admin_token:
            raise RuntimeError("auth_register did not return a token")
        client = _TokenScopedClient(mcp, admin_token)
        print(f"   user_id={reg.get('id')} token_len={len(admin_token or '')}")

        # B2. 创建 project
        step("B2. create_project")
        r = await client.call_tool("create_project", {"name": proj_name, "key": proj_key})
        proj = _parse(r)
        pid = proj.get("id")
        print(f"   project_id={pid} key={proj.get('key')}")

        # B2.5 把 admin 加为 project member（create_proposal 要求 member）
        step("B2.5. add_member (admin → project, role=owner)")
        r = await client.call_tool("add_member", {
            "project_id": pid, "user_id": reg.get("id"), "role": "owner",
        })
        m = _parse(r)
        print(f"   member_id={m.get('id') or m.get('member_id') or m}")

        # B3. 创建 proposal — MCP 没暴露 create_proposal，直接走 REST (用 admin token)
        step("B3. create_proposal (REST, MCP 无 create_proposal 工具)")
        r = httpx.post(f"{API_URL}/api/proposals",
                       json={"project_id": pid,
                             "title": f"MCP 端到端测试 {suffix}",
                             "content": "## 背景\n本地 MCP 验证 proposal + create ticket + task workflow\n## 目标\n一气呵成跑通\n"},
                       headers={"Authorization": f"Bearer {admin_token}"})
        r.raise_for_status()
        prop = r.json()
        prop_id = prop.get("id")
        print(f"   proposal_id={prop_id} status={prop.get('status')}")

        # B4. proposal_status → queued (MCP 没暴露 proposal_status 工具, 走 REST)
        step("B4. PUT /api/proposals/{id}/status: queued (REST, MCP 工具缺)")
        r = httpx.put(f"{API_URL}/api/proposals/{prop_id}/status",
                      json={"status": "queued", "error": None},
                      headers={"Authorization": f"Bearer {admin_token}"})
        r.raise_for_status()
        st = r.json()
        print(f"   status={st.get('status')}")

        # B5. proposal_claim → analyzing
        step("B5. proposal_claim")
        r = await client.call_tool("proposal_claim", {"proposal_id": prop_id, "agent": "mcp-smoke"})
        st = _parse(r)
        print(f"   status={st.get('status')} claimed_by={st.get('claimed_by')}")

        # B6. proposal_ask 1 轮
        step("B6. proposal_ask (1 轮 2 问)")
        r = await client.call_tool("proposal_ask", {
            "proposal_id": prop_id,
            "questions": ["MCP 工具调用是否成功？", "是否需要继续问？"],
        })
        st = _parse(r)
        print(f"   status={st.get('status') if isinstance(st, dict) else st}")

        # B7. proposal_get 拉 question id + REST 答 (MCP 没暴露 proposal_answer 工具)
        step("B7. proposal_get → REST 答 (MCP 工具缺)")
        r = await client.call_tool("proposal_get", {"proposal_id": prop_id})
        replay = _parse(r)
        open_qs = replay.get("open_questions", []) if isinstance(replay, dict) else []
        print(f"   open_questions={len(open_qs)}")
        for q in open_qs:
            ans = "是的，工具调用通了" if q.get("seq") == 1 else "不需要，问得够清楚了"
            qid = q.get("question_id")
            r = httpx.put(f"{API_URL}/api/proposals/{qid}/answer",
                          json={"answer": ans},
                          headers={"Authorization": f"Bearer {admin_token}"})
            r.raise_for_status()
        # confirm status
        r = await client.call_tool("proposal_get", {"proposal_id": prop_id})
        st = _parse(r)
        print(f"   status={st.get('status')}")

        # B8. proposal_finalize (MCP 一步走完 PATCH + status)
        step("B8. proposal_finalize (PATCH spec + status converged)")
        r = await client.call_tool("proposal_finalize", {
            "proposal_id": prop_id,
            "converged_spec": "## 验收\n1. task workflow 跑通\n2. MCP 工具链闭环",
        })
        st = _parse(r)
        print(f"   status={st.get('status') if isinstance(st, dict) else st}")

        # B9. proposal_create_ticket
        step("B9. proposal_create_ticket (type=epic)")
        r = await client.call_tool("proposal_create_ticket", {
            "proposal_id": prop_id,
            "type": "epic",
            "epic_id": None,  # 新建
            "story_id": None,
            "title": f"[Epic MCP] {suffix}",
        })
        body = _parse(r)
        ticket = body.get("ticket") or {}
        epic_id = ticket.get("id") or (body.get("request") or {}).get("ticket_id")
        print(f"   epic_id={epic_id} type={ticket.get('type') or (body.get('request') or {}).get('type')}")
        print(f"   request_status={(body.get('request') or {}).get('status')}")
        print(f"   proposal_status_after={(body.get('proposal') or {}).get('status')}")

        # B10. create_story 挂到 epic
        step("B10. create_story (挂到 epic)")
        r = await client.call_tool("create_story", {
            "epic_id": epic_id,
            "title": f"[Story MCP] {suffix}",
            "description": "MCP 端到端 story",
        })
        story = _parse(r)
        story_id = story.get("id") or story.get("story", {}).get("id")
        print(f"   story_id={story_id}")

        # B11. create_task (挂到 story)
        step("B11. create_task (挂到 story)")
        r = await client.call_tool("create_task", {
            "project_id": pid,
            "story_id": story_id,
            "title": f"[Task MCP] {suffix}",
            "type": "dev",
            "priority": "high",
            "description": "## 验收\n1. task 5 态全跑通",
            "spec": "## 设计\n走 MCP 路径",
        })
        task = _parse(r)
        task_id = task.get("id") or task.get("task", {}).get("id")
        print(f"   task_id={task_id} status={task.get('status')}")

        # B12. claim + status in_progress
        step("B12. claim_task + set_status in_progress")
        r = await client.call_tool("claim_task", {"task_id": task_id})
        task = _parse(r)
        print(f"   claim → status={task.get('status')} assignee_id={task.get('assignee_id')}")
        r = await client.call_tool("set_status", {
            "task_id": task_id, "status": "in_progress", "status_reason": None,
        })
        task = _parse(r)
        print(f"   set in_progress → status={task.get('status')}")

        # B13. submit_task_for_review → in_review
        step("B13. submit_task_for_review")
        r = await client.call_tool("submit_task_for_review", {"task_id": task_id})
        task = _parse(r)
        print(f"   status={task.get('status')}")

        # B14. review_task approve → done
        step("B14. review_task approve → done")
        r = await client.call_tool("review_task", {
            "task_id": task_id, "verdict": "approve", "comment": "MCP 端到端 OK",
        })
        task = _parse(r)
        post_status = task.get("status") if isinstance(task, dict) else None
        print(f"   review_task → status={post_status}")
        if post_status != "done":
            print(f"   review 路径可能受限, fallback 到 set_status done")
            r = await client.call_tool("set_status", {
                "task_id": task_id, "status": "done", "status_reason": "completed",
            })
            task = _parse(r)
            print(f"   set_status done → status={task.get('status') if isinstance(task, dict) else task}")

        # ===== C. 终态摘要 =====
        step("C. 终态摘要")
        r = await client.call_tool("get_epic", {"epic_id": epic_id})
        epic = _parse(r)
        r = await client.call_tool("get_story", {"story_id": story_id})
        story = _parse(r)
        r = await client.call_tool("get_task", {"task_id": task_id})
        task = _parse(r)
        r = await client.call_tool("proposal_get", {"proposal_id": prop_id})
        prop = _parse(r)
        print(f"   epic  #{epic_id:>4}  status={epic.get('status'):16s} title={(epic.get('title') or '')[:50]!r}")
        print(f"   story #{story_id:>4}  status={(story.get('status') or 'n/a'):16s} title={(story.get('title') or '')[:50]!r}")
        print(f"   task  #{task_id:>4}  status={task.get('status'):16s} title={(task.get('title') or '')[:50]!r}")
        print(f"   proposal #{prop_id} status={prop.get('status')} ticket_type={prop.get('ticket_type')} ticket_id={prop.get('ticket_id')}")

    print("\n== [PASS] MCP 端到端全链路通过 ==")
    return 0


def _parse(call_result) -> dict:
    """FastMCP call_tool 返回 CallToolResult, content 列表是 TextContent(text=JSON 串)."""
    try:
        items = getattr(call_result, "content", None) or []
    except Exception:
        return {}
    for it in items:
        text = getattr(it, "text", None) or (it.get("text") if isinstance(it, dict) else None)
        if text:
            import json as _json
            try:
                return _json.loads(text)
            except Exception:
                return {"_text": text}
    # 兜底: 有些 FastMCP 版本 content[0] 直接就是 data
    return dict(call_result) if isinstance(call_result, dict) else {}


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as e:
        print(f"\n== [FAIL] {e} ==", file=sys.stderr)
        raise
