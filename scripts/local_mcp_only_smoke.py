"""Run a real MCP-only project/story/task smoke against the local FastAPI.

Unlike ``local_mcp_workflow_smoke.py``, this script intentionally has no direct
HTTP calls.  Every operation after startup is sent through FastMCP tools.

Usage::

    AGENTBOARD_API_URL=http://127.0.0.1:18001 \
      python scripts/local_mcp_only_smoke.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

from fastmcp import Client

API_URL = os.environ.get("AGENTBOARD_API_URL", "http://127.0.0.1:18001")
SOURCE_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "src", "backend-fastapi"
))
sys.path.insert(0, SOURCE_ROOT)

REQUIRED_TOOLS = {
    "auth_register",
    "auth_me",
    "create_project",
    "create_epic",
    "create_story",
    "create_task",
    "claim_task",
    "get_task",
    "set_status",
}


async def main() -> int:
    env_keys = ("AGENTBOARD_API_URL", "AGENTBOARD_MCP_REQUIRE_AUTH")
    previous_env = {key: os.environ.get(key) for key in env_keys}
    try:
        os.environ["AGENTBOARD_API_URL"] = API_URL
        os.environ["AGENTBOARD_MCP_REQUIRE_AUTH"] = "0"

        from agentboard import mcp_server  # noqa: E402

        async with Client(mcp_server.mcp) as client:
            await client._connect()
            advertised = {tool.name for tool in await client.list_tools()}
            missing = sorted(REQUIRED_TOOLS - advertised)
            if missing:
                print(f"MCP-only smoke blocked; missing required tools: {missing}", file=sys.stderr)
                return 1
            print(f"MCP-only tools/list: {len(advertised)} tools; required tools present")

            suffix = uuid.uuid4().hex[:8]
            username = f"mcp_only_{suffix}"
            password = f"McpOnly_{suffix}"

            registered = await _call(client, "auth_register", {
                "username": username,
                "password": password,
            })
            token = registered.get("token")
            if not token:
                raise RuntimeError(f"auth_register did not return a token: {registered}")

            return await _run_authenticated_workflow(
                mcp_server.mcp, token, suffix
            )
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def _run_authenticated_workflow(
    mcp: Any,
    token: str,
    suffix: str,
) -> int:
    from agentboard.features.mcp.shared import token_context

    # The in-process FastMCP server starts its session in a background task;
    # establish the token context before creating that session.
    with token_context(token):
        async with Client(mcp) as client:
            await client._connect()
            current = await _call(client, "auth_me", {"token": token})
            if current.get("username") != f"mcp_only_{suffix}":
                raise RuntimeError(f"auth_me returned the wrong user: {current}")

            project = await _call(client, "create_project", {
                "name": f"MCP-only smoke {suffix}",
                "key": f"MO{suffix[:5].upper()}",
            })
            project_id = _id(project, "project")

            epic = await _call(client, "create_epic", {
                "project_id": project_id,
                "title": f"MCP-only Epic {suffix}",
            })
            epic_id = _id(epic, "epic")

            story = await _call(client, "create_story", {
                "epic_id": epic_id,
                "title": f"MCP-only Story {suffix}",
                "description": "Created and verified through MCP tools only.",
                "needs_design": False,
            })
            story_id = _id(story, "story")

            task = await _call(client, "create_task", {
                "project_id": project_id,
                "story_id": story_id,
                "title": f"MCP-only Task {suffix}",
                "type": "dev",
                "priority": "high",
            })
            task_id = _id(task, "task")

            claimed = await _call(client, "claim_task", {"task_id": task_id})
            claimed_task = claimed.get("task") if isinstance(claimed.get("task"), dict) else claimed
            if claimed_task.get("status") != "in_progress":
                raise RuntimeError(f"claim_task did not enter in_progress: {claimed}")

            verified = await _call(client, "get_task", {"task_id": task_id})
            if verified.get("id") != task_id or verified.get("story_id") != story_id:
                raise RuntimeError(f"get_task relationship check failed: {verified}")

            done = await _call(client, "set_status", {
                "task_id": task_id,
                "status": "done",
                "status_reason": "completed",
            })
            if done.get("status") != "done":
                raise RuntimeError(f"set_status did not enter done: {done}")

            print(
                f"[PASS] MCP-only project={project_id} epic={epic_id} "
                f"story={story_id} task={task_id} status=done"
            )
            return 0


async def _call(
    client: Client,
    name: str,
    arguments: dict[str, Any],
    *,
    token: str | None = None,
) -> dict[str, Any]:
    from agentboard.features.mcp.shared import token_context

    with token_context(token):
        result = await client.call_tool(name, arguments)
    if getattr(result, "is_error", False):
        raise RuntimeError(f"MCP tool {name} failed: {result}")
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            value = json.loads(text)
            if isinstance(value, dict) and value.get("error"):
                raise RuntimeError(f"MCP tool {name} returned an error: {value}")
            return value if isinstance(value, dict) else {"value": value}
    raise RuntimeError(f"MCP tool {name} returned no JSON content")


def _id(value: dict[str, Any], kind: str) -> int:
    item = value.get("id")
    if item is None and isinstance(value.get(kind), dict):
        item = value[kind].get("id")
    if not isinstance(item, int):
        raise RuntimeError(f"MCP response did not contain {kind} id: {value}")
    return item


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise
