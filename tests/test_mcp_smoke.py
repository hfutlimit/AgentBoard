"""真实 Streamable HTTP MCP、Bearer 鉴权与完整项目树工具测试。"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from fastmcp import Client


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _result(result):
    data = getattr(result, "data", None)
    if data is not None:
        return data
    return json.loads(result.content[0].text)


def test_remote_mcp_auth_and_full_tree():
    db_path = tempfile.mktemp(suffix=".db")
    api_port, port = _free_port(), _free_port()
    secret = "test-only-secret-with-at-least-32-bytes"
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": ROOT,
        "AGENTBOARD_DB_URL": f"sqlite:///{db_path}",
        "AGENTBOARD_SECRET": secret,
        "AGENTBOARD_API_URL": f"http://127.0.0.1:{api_port}",
        "AGENTBOARD_MCP_TRANSPORT": "http",
        "AGENTBOARD_MCP_HOST": "127.0.0.1",
        "AGENTBOARD_MCP_PORT": str(port),
        "AGENTBOARD_MCP_PATH": "/mcp",
        "AGENTBOARD_MCP_REQUIRE_AUTH": "1",
    })

    bootstrap = subprocess.run(
        [sys.executable, "-c", (
            "from agentboard.database import init_db,SessionLocal; "
            "from agentboard import service,auth; init_db(); "
            "s=SessionLocal(); u=service.register_user(s,username='admin',password='secret123'); "
            "print(auth.make_token(u.id)); s.close()"
        )],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True,
    )
    token = bootstrap.stdout.strip().splitlines()[-1]
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "agentboard.mcp_server"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(80):
            try:
                if httpx.get(f"http://127.0.0.1:{api_port}/api/meta", timeout=0.25).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("REST API did not start")

        for _ in range(80):
            try:
                if httpx.get(url, timeout=0.25).status_code in {401, 404, 405}:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("remote MCP did not start")

        assert httpx.post(url, timeout=2).status_code == 401

        async def exercise():
            async with Client(url, auth=token) as client:
                names = {x.name for x in await client.list_tools()}
                assert {
                    "list_projects", "get_project", "update_project", "delete_project",
                    "list_epics", "list_stories", "list_tasks", "delete_task",
                    "append_task_spec", "auth_login", "auth_me",
                } <= names

                me = _result(await client.call_tool("auth_me", {}))
                assert me["username"] == "admin"
                login = _result(await client.call_tool(
                    "auth_login", {"username": "admin", "password": "secret123"}
                ))
                assert login["token"].startswith("v1.")

                project = _result(await client.call_tool(
                    "create_project", {"name": "Remote", "key": "REM"}
                ))
                project = _result(await client.call_tool(
                    "update_project", {"project_id": project["id"], "description": "remote project"}
                ))
                assert project["description"] == "remote project"
                assert _result(await client.call_tool(
                    "get_project", {"project_id": project["id"]}
                ))["id"] == project["id"]

                epic = _result(await client.call_tool(
                    "create_epic", {"project_id": project["id"], "title": "Epic"}
                ))
                assert _result(await client.call_tool(
                    "list_epics", {"project_id": project["id"]}
                ))[0]["id"] == epic["id"]
                story = _result(await client.call_tool(
                    "create_story", {"epic_id": epic["id"], "title": "Story"}
                ))
                assert _result(await client.call_tool(
                    "list_stories", {"epic_id": epic["id"]}
                ))[0]["id"] == story["id"]
                task = _result(await client.call_tool("create_task", {
                    "project_id": project["id"], "story_id": story["id"],
                    "title": "Task", "spec": "# Spec",
                }))
                assert _result(await client.call_tool(
                    "list_tasks", {"story_id": story["id"]}
                ))[0]["id"] == task["id"]
                task = _result(await client.call_tool(
                    "append_task_spec", {"task_id": task["id"], "text": "## More"}
                ))
                assert "## More" in task["spec"]
                assert _result(await client.call_tool(
                    "delete_task", {"task_id": task["id"]}
                ))["ok"] is True
                assert _result(await client.call_tool(
                    "delete_story", {"story_id": story["id"]}
                ))["ok"] is True
                assert _result(await client.call_tool(
                    "delete_epic", {"epic_id": epic["id"]}
                ))["ok"] is True

        asyncio.run(exercise())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        api.terminate()
        try:
            api.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api.kill()


def test_rest_business_auth_switch():
    db_path = tempfile.mktemp(suffix=".db")
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": ROOT,
        "AGENTBOARD_DB_URL": f"sqlite:///{db_path}",
        "AGENTBOARD_SECRET": "test-only-secret-with-at-least-32-bytes",
        "AGENTBOARD_REQUIRE_AUTH": "1",
    })
    script = """
from fastapi.testclient import TestClient
from agentboard.api import app
with TestClient(app) as c:
    reg = c.post('/api/auth/register', json={'username':'api-agent','password':'secret123'})
    assert reg.status_code == 201, reg.text
    token = reg.json()['token']
    assert c.get('/api/projects').status_code == 401
    headers = {'Authorization': 'Bearer ' + token}
    assert c.post('/api/projects', json={'name':'Protected'}, headers=headers).status_code == 201
    assert c.get('/api/projects', headers=headers).status_code == 200
"""
    subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        check=True, capture_output=True, text=True,
    )


def test_remote_mcp_forwards_bearer_to_protected_api():
    db_path = tempfile.mktemp(suffix=".db")
    api_port, mcp_port = _free_port(), _free_port()
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": ROOT,
        "AGENTBOARD_DB_URL": f"sqlite:///{db_path}",
        "AGENTBOARD_SECRET": "test-only-secret-with-at-least-32-bytes",
        "AGENTBOARD_REQUIRE_AUTH": "1",
    })
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app", "--host", "127.0.0.1", "--port", str(api_port)],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    mcp = None
    api_url = f"http://127.0.0.1:{api_port}"
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{api_url}/api/meta", timeout=0.25).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("protected API did not start")

        reg = httpx.post(f"{api_url}/api/auth/register", json={
            "username": "proxy-agent", "password": "secret123",
        })
        assert reg.status_code == 201
        token = reg.json()["token"]
        assert httpx.get(f"{api_url}/api/projects").status_code == 401

        mcp_env = env | {
            "AGENTBOARD_API_URL": api_url,
            "AGENTBOARD_MCP_TRANSPORT": "http",
            "AGENTBOARD_MCP_HOST": "127.0.0.1",
            "AGENTBOARD_MCP_PORT": str(mcp_port),
            "AGENTBOARD_MCP_REQUIRE_AUTH": "1",
        }
        mcp = subprocess.Popen(
            [sys.executable, "-m", "agentboard.mcp_server"],
            cwd=ROOT, env=mcp_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        mcp_url = f"http://127.0.0.1:{mcp_port}/mcp"
        for _ in range(80):
            try:
                if httpx.get(mcp_url, timeout=0.25).status_code == 401:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError("API-backed MCP did not start")

        async def exercise():
            async with Client(mcp_url, auth=token) as client:
                project = _result(await client.call_tool(
                    "create_project", {"name": "Forwarded", "key": "FWD"}
                ))
                assert project["name"] == "Forwarded"
                rows = _result(await client.call_tool("list_projects", {}))
                assert any(x["id"] == project["id"] for x in rows)

        asyncio.run(exercise())
    finally:
        for process in (mcp, api):
            if process is None:
                continue
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
