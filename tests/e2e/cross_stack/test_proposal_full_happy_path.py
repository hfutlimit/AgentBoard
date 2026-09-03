"""Real cross-stack Golden Happy Path.

This opt-in gate launches a real FastAPI process, the Python workflow
allocator, three .NET ProposalProcessor processes and uses a real RabbitMQ and
MariaDB supplied by the test environment.  The test body never calls Task
submit/review endpoints and never writes workflow state directly; the
DeterministicScenarioAdapter performs those actions after consuming the same
RabbitMQ messages used by production CLI adapters.

Required environment::

    AGENTBOARD_RUN_GOLDEN_CROSS_STACK=1
    AGENTBOARD_GOLDEN_DB_URL=mysql+pymysql://.../dedicated_test_database
    AGENTBOARD_MQ_URL_TEST=amqp://.../dedicated_test_broker
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "src" / "backend-fastapi"
NODE_DLL = (
    ROOT
    / "src"
    / "nodes"
    / "AgentBoard.Node"
    / "bin"
    / "Release"
    / "net10.0"
    / "AgentBoard.Node.dll"
)
RUN_REAL = os.getenv("AGENTBOARD_RUN_GOLDEN_CROSS_STACK") == "1"
DB_URL = os.getenv("AGENTBOARD_GOLDEN_DB_URL", "")
BROKER_URL = os.getenv("AGENTBOARD_MQ_URL_TEST", "")

pytestmark = pytest.mark.skipif(
    not RUN_REAL or not DB_URL or not BROKER_URL,
    reason=(
        "set AGENTBOARD_RUN_GOLDEN_CROSS_STACK=1, "
        "AGENTBOARD_GOLDEN_DB_URL and AGENTBOARD_MQ_URL_TEST"
    ),
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until(predicate, *, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (httpx.HTTPError, KeyError, ValueError):
            pass
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {description}; last={last!r}")


def _start_process(command: list[str], env: dict[str, str], log_path: Path):
    log = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log


def _stop_process(process: subprocess.Popen | None, log) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if log is not None:
        log.close()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _must(response: httpx.Response, *expected: int):
    assert response.status_code in expected, (
        f"{response.request.method} {response.request.url}: "
        f"HTTP {response.status_code} {response.text}"
    )
    return response.json() if response.content else None


def _worker_env(
    base: dict[str, str],
    *,
    worker_id: str,
    agent_id: str,
    token: str,
    api_url: str,
    namespace: str,
    portal_port: int,
    history_path: Path,
    delay_ms: int = 0,
) -> dict[str, str]:
    env = dict(base)
    env.update({
        "DOTNET_ENVIRONMENT": "Development",
        "ASPNETCORE_ENVIRONMENT": "Development",
        "Worker__Id": worker_id,
        "Worker__HeartbeatSeconds": "5",
        "Worker__HistoryDatabasePath": str(history_path),
        "Worker__MaxConcurrentExecutions": "1",
        "RabbitMq__Uri": BROKER_URL,
        # The Golden flow drives proposal Grill through REST. Keep the legacy
        # proposal consumer on a private, unused namespace so only workflow
        # messages reach this worker and every recorded execution is relevant.
        "RabbitMq__Namespace": f"{namespace}.unused.{worker_id}",
        "RabbitMq__WorkflowNamespace": namespace,
        "RabbitMq__WorkflowConsumerEnabled": "true",
        "Agents__WorkBuddy__Command": "",
        "Agents__MiniMax__Command": "",
        "Agents__Codex__Command": "",
        "Agents__Fake__Command": "",
        "Agents__Scenario__Command": "enabled",
        "Agents__Scenario__AgentId": agent_id,
        "Agents__Scenario__DelayMilliseconds": str(delay_ms),
        "AgentBoard__ServerUrl": api_url,
        "AgentBoard__StartupToken": token,
        "AgentBoard__HeartbeatUrl": "",
        "AgentBoard__WebSocketUrl": "",
        "Portal__Urls": f"http://127.0.0.1:{portal_port}",
        "Portal__ApiKey": "golden-worker-key",
        "ProcessExecutor__LogDirectory": str(history_path.parent / "logs"),
    })
    return env


def test_proposal_full_happy_path(tmp_path: Path):
    assert NODE_DLL.exists(), (
        "build the node first: dotnet build "
        "src/nodes/AgentBoard.Node/AgentBoard.Node.csproj "
        "-c Release"
    )
    run_id = uuid.uuid4().hex[:10]
    namespace = f"agentboard.golden.{run_id}"
    api_port = _free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    portal_ports = [_free_port() for _ in range(2)]
    processes: list[tuple[subprocess.Popen | None, object]] = []
    logs: dict[str, Path] = {}

    base_env = dict(os.environ)
    python_path = str(BACKEND)
    if base_env.get("PYTHONPATH"):
        python_path += os.pathsep + base_env["PYTHONPATH"]
    base_env.update({
        "PYTHONPATH": python_path,
        "AGENTBOARD_DB_URL": DB_URL,
        "AGENTBOARD_SECRET": "golden-cross-stack-secret-32-bytes-minimum",
        "AGENTBOARD_REQUIRE_AUTH": "1",
        "AGENTBOARD_ALLOW_REGISTRATION": "1",
        "AGENTBOARD_ENV": "test",
        "AGENTBOARD_CORS_ORIGINS": "http://127.0.0.1",
        "AGENTBOARD_MQ_URL": BROKER_URL,
        "AGENTBOARD_MQ_NAMESPACE": f"{namespace}.proposals",
        "AGENTBOARD_WORKFLOW_NAMESPACE": namespace,
        "AGENTBOARD_REALTIME_NOTIFY_URL": "",
        "PYTHONUNBUFFERED": "1",
    })

    def start(name: str, command: list[str], env: dict[str, str]):
        path = tmp_path / f"{name}.log"
        logs[name] = path
        process, handle = _start_process(command, env, path)
        processes.append((process, handle))
        return process

    try:
        api_process = start(
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentboard.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            base_env,
        )
        _wait_until(
            lambda: httpx.get(f"{api_url}/api/meta", timeout=1).status_code == 200,
            timeout=30,
            description="FastAPI /api/meta",
        )
        assert api_process.poll() is None

        client = httpx.Client(base_url=api_url, timeout=10)
        users = []
        for number in (1, 2, 3):
            users.append(_must(client.post(
                "/api/auth/register",
                json={
                    "username": f"golden-{run_id}-{number}",
                    "password": "golden-password-123",
                },
            ), 201))
        owner, second, third = users
        owner_headers = _headers(owner["token"])

        project = _must(client.post(
            "/api/projects",
            json={"name": f"Golden {run_id}", "key": f"G{run_id[:6]}"},
            headers=owner_headers,
        ), 201)
        for member in (second, third):
            _must(client.post(
                f"/api/projects/{project['id']}/members",
                json={"user_id": member["id"], "role": "member"},
                headers=owner_headers,
            ), 201)
        epic = _must(client.post(
            f"/api/projects/{project['id']}/epics",
            json={"title": "Golden target", "description": ""},
            headers=owner_headers,
        ), 201)

        # 2026-09-01 决策 a/b（docs/design/agent-ownership-scoping-plan.md）：
        # 执行和评审都收敛到 owner 名下 agent —— 评审候选要求同 owner 且
        # 排除实现方 agent（agent 维度去重），无第二个同 owner agent 时评审
        # 保持待处理。因此 Golden 场景改为：owner 注册两个 agent，agent-1
        # 实现（design/dev/qa 执行中的实现方），agent-2 评审 design/dev 并
        # 执行 qa（qa 评审回到 agent-1，天然满足互斥）。
        # second/third 仍注册为项目成员，覆盖协作成员的读路径。
        worker_ids = [f"golden-worker-{run_id}-{n}" for n in (1, 2)]
        agent_ids = [f"golden-agent-{run_id}-{n}" for n in (1, 2)]
        instance_ids = []
        for worker_id, agent_id in zip(worker_ids, agent_ids):
            headers = owner_headers
            _must(client.post(
                "/api/workers/register",
                json={
                    "worker_id": worker_id,
                    "hostname": "golden-e2e",
                    "status": "active",
                },
                headers=headers,
            ), 201)
            agent = _must(client.post(
                "/api/agents/register",
                json={
                    "agent_id": agent_id,
                    "name": agent_id,
                    "roles": "[]",
                    "capabilities": "[]",
                    "cli_command": "",
                },
                headers=headers,
            ), 201)
            assert agent["roles"] == "[]"
            instance = _must(client.post(
                f"/api/agents/{agent_id}/instances",
                json={
                    "worker_id": worker_id,
                    "cli_command": "",
                    "executor_type": "scenario",
                    "enabled": True,
                },
                headers=headers,
            ), 201)
            instance_ids.append(int(instance["id"]))

        workflow_env = dict(base_env)
        workflow_env.update({
            "AGENTBOARD_API_URL": api_url,
            "AGENTBOARD_WORKER_TOKEN": owner["token"],
            "AGENTBOARD_WORKFLOW_WORKER_INTERVAL": "1",
        })
        start(
            "workflow-worker",
            [sys.executable, "-m", "agentboard.workflow_processor", "--mq"],
            workflow_env,
        )

        worker_processes: list[subprocess.Popen | None] = [None, None]

        def start_worker(index: int, *, delay_ms: int = 0):
            env = _worker_env(
                base_env,
                worker_id=worker_ids[index],
                agent_id=agent_ids[index],
                token=owner["token"],
                api_url=api_url,
                namespace=namespace,
                portal_port=portal_ports[index],
                history_path=tmp_path / f"worker-{index + 1}.db",
                delay_ms=delay_ms,
            )
            worker_processes[index] = start(
                f"dotnet-worker-{index + 1}",
                ["dotnet", str(NODE_DLL)],
                env,
            )
            _wait_until(
                lambda: httpx.get(
                    f"http://127.0.0.1:{portal_ports[index]}/health",
                    timeout=1,
                ).status_code == 200,
                timeout=30,
                description=f".NET worker {index + 1} health",
            )
            headers = owner_headers
            _must(client.post(
                f"/api/agents/{agent_ids[index]}/heartbeat",
                json={"probe_ok": True, "probe_message": "golden e2e"},
                headers=headers,
            ), 200)
            _must(client.post(
                f"/api/workers/{worker_ids[index]}/agent-instances/"
                f"{instance_ids[index]}/heartbeat",
                json={"probe_ok": True, "probe_message": "golden e2e"},
                headers=headers,
            ), 200)

        # agent-1（实现方）带 4s 执行延迟，agent-2（评审方）即时在线：
        # design 由 agent-1 执行完进 in_review 后，agent-2 立刻可被自动
        # 指派为 reviewer（同 owner、非实现方 agent）。
        start_worker(0, delay_ms=4000)
        start_worker(1)

        proposal = _must(client.post(
            "/api/proposals",
            json={
                "project_id": project["id"],
                "title": "Golden full path",
                "content": "- [ ] Implement the cross-stack golden slice",
                "auto_create_ticket": True,
                "target_epic_id": epic["id"],
            },
            headers=owner_headers,
        ), 201)
        pid = int(proposal["id"])
        _must(client.put(
            f"/api/proposals/{pid}/status",
            json={"status": "queued"},
            headers=owner_headers,
        ), 200)
        _must(client.post(
            f"/api/proposals/{pid}/claim",
            json={"agent": agent_ids[0]},
            headers=owner_headers,
        ), 200)
        questions = _must(client.post(
            f"/api/proposals/{pid}/questions",
            json={
                "questions": ["Confirm the golden happy-path scope?"],
                "round": 1,
                "summary": "deterministic grill",
                "agent": agent_ids[0],
            },
            headers=owner_headers,
        ), 201)
        question_id = int(questions["questions"][0]["id"])
        _must(client.put(
            f"/api/proposal-questions/{question_id}/answer",
            json={"answer": "yes", "unsure": False},
            headers=owner_headers,
        ), 200)
        _must(client.post(
            f"/api/proposals/{pid}/claim",
            json={"agent": agent_ids[0]},
            headers=owner_headers,
        ), 200)
        _must(client.patch(
            f"/api/proposals/{pid}",
            json={"converged_spec": "- [ ] Implement the cross-stack golden slice"},
            headers=owner_headers,
        ), 200)
        _must(client.put(
            f"/api/proposals/{pid}/status",
            json={"status": "converged"},
            headers=owner_headers,
        ), 200)

        materialized = _wait_until(
            lambda: (
                row
                if (row := _must(client.get(
                    f"/api/proposals/{pid}", headers=owner_headers,
                ), 200)).get("story_id")
                else None
            ),
            timeout=30,
            description="AUTO Proposal materialization",
        )
        story_id = int(materialized["story_id"])

        def task_map():
            response = _must(client.get(
                f"/api/stories/{story_id}/tasks", headers=owner_headers,
            ), 200)
            rows = response["items"]
            return {row["type"]: row for row in rows}

        checkpoint = _wait_until(
            lambda: (
                rows
                if len(rows := task_map()) == 3
                and rows["design"]["status"] == "done"
                and rows["dev"]["status"] == "in_progress"
                else None
            ),
            timeout=45,
            description="Design reviewed and Dev assigned",
        )
        assert checkpoint["design"]["assignee_id"] == owner["id"]
        # 决策 a/b：reviewer 与实现方同 owner（user 维度相同，agent 维度互斥，
        # 由路由断言兜底验证）。
        assert checkpoint["design"]["reviewer_id"] == owner["id"]

        final_story = _wait_until(
            lambda: (
                row
                if (row := _must(client.get(
                    f"/api/stories/{story_id}", headers=owner_headers,
                ), 200))["status"] == "done"
                else None
            ),
            timeout=60,
            description="Story done",
        )
        final_tasks = task_map()
        design = final_tasks["design"]
        dev = final_tasks["dev"]
        qa = final_tasks["qa"]
        assert final_story["status"] == "done"
        # 决策 a/b：执行与评审都收敛在 owner 名下 —— user 维度全是 owner，
        # agent 维度的实现方/评审方互斥由下方 routed_workloads 精确断言。
        assert design["assignee_id"] == owner["id"]
        assert design["reviewer_id"] == owner["id"]
        assert dev["assignee_id"] == owner["id"]
        assert dev["reviewer_id"] == owner["id"]
        assert qa["assignee_id"] == owner["id"]
        assert qa["reviewer_id"] == owner["id"]
        assert all(row["status"] == "done" for row in final_tasks.values())

        dependency_count = 0
        for task in final_tasks.values():
            deps = _must(client.get(
                f"/api/tasks/{task['id']}/dependencies",
                headers=owner_headers,
            ), 200)
            dependency_count += len(deps.get("blockers", []))
        assert dependency_count == 2

        worker_executions = []
        for port in portal_ports:
            rows = _must(httpx.get(
                f"http://127.0.0.1:{port}/api/executions",
                headers={"X-AgentBoard-Worker-Key": "golden-worker-key"},
                timeout=5,
            ), 200)
            assert rows, f"worker on port {port} recorded no execution"
            worker_executions.append(rows)
            for execution in rows:
                assert execution["status"] == "Succeeded"
                assert execution["agentType"] == "scenario"
                payload = json.loads(execution["payload"])
                assert payload["agent_id"]
                assert payload["agent_type"] == "scenario"
                assert payload["workload_type"] in {"task", "review"}
                assert payload["correlation_id"]

        routed_workloads = [
            {(row["workloadType"], row["workloadId"]) for row in rows}
            for rows in worker_executions
        ]
        assert routed_workloads == [
            # agent-1（实现方）：design/dev 执行 + qa 评审（qa 执行方是
            # agent-2，评审回到 agent-1，实现方/评审方在 agent 维度互斥）。
            {
                ("task", design["id"]),
                ("task", dev["id"]),
                ("review", qa["id"]),
            },
            # agent-2（评审方）：design/dev 评审 + qa 执行（上游 dev 实现
            # 方 agent-1 被动态排斥，不能自己做 QA）。
            {
                ("review", design["id"]),
                ("review", dev["id"]),
                ("task", qa["id"]),
            },
        ]

        proposal_final = _must(client.get(
            f"/api/proposals/{pid}", headers=owner_headers,
        ), 200)
        assert proposal_final["ticket_type"] == "story"
        assert proposal_final["ticket_id"] == story_id
        assert proposal_final["target_epic_id"] == epic["id"]

        trace = {
            "proposal_id": pid,
            "story_id": story_id,
            "story_status": final_story["status"],
            "tasks": {
                kind: {
                    "id": row["id"],
                    "assignee_id": row["assignee_id"],
                    "reviewer_id": row["reviewer_id"],
                    "status": row["status"],
                }
                for kind, row in final_tasks.items()
            },
            "worker_execution_counts": [len(rows) for rows in worker_executions],
            "workflow_namespace": namespace,
        }
        print("GOLDEN_TRACE=" + json.dumps(trace, sort_keys=True))
    except Exception as exc:
        diagnostics = []
        for name, path in logs.items():
            try:
                diagnostics.append(
                    f"\n--- {name} ---\n"
                    f"{path.read_text(encoding='utf-8', errors='replace')[-120000:]}"
                )
            except OSError:
                pass
        pytest.fail(str(exc) + "".join(diagnostics), pytrace=True)
    finally:
        for process, handle in reversed(processes):
            _stop_process(process, handle)
