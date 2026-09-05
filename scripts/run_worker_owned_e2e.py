"""Opt-in REAL CLI E2E on isolated business DB, broker and an existing test worktree.

No fake adapter, direct run insertion, Task status writes, or manual completion.
Only Proposal submission is driven by this harness. Workers do the rest.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
import httpx
from report_worker_owned_e2e import evidence

ROOT = Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--provider", choices=["codex", "workbuddy", "minimax"], default="codex")
    parser.add_argument("--model", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--split-workers", action="store_true", help="One profile per Worker process; shared scoped queues and checkout")
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if subprocess.check_output(["git", "-C", str(workspace), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("Use a clean dedicated test worktree; no user changes may be consumed")
    api_port, broker_port, portal_port = port(), port(), port()
    container = "agentboard-worker-e2e-" + uuid.uuid4().hex[:10]
    password = secrets.token_hex(24)
    environment = os.environ.copy()
    environment.update({"RABBITMQ_DEFAULT_PASS": password,
        "PYTHONPATH": str(ROOT / "src/backend-fastapi"),
        "AGENTBOARD_DB_URL": "sqlite:///" + (output / "business.db").as_posix(),
        "AGENTBOARD_SECRET": secrets.token_hex(32), "AGENTBOARD_REQUIRE_AUTH": "1",
        "AGENTBOARD_ALLOW_REGISTRATION": "1", "AGENTBOARD_JUDGE_AUTO": "0",
        "AGENTBOARD_DURABLE_PROJECT_IDS": "", "AGENTBOARD_WORKER_OWNED_ENABLED": "1",
        "AGENTBOARD_MQ_URL": f"amqp://worker_test:{password}@127.0.0.1:{broker_port}/"})
    processes, logs = [], []

    def start(command, name, env):
        log = (output / name).open("w", encoding="utf-8")
        logs.append(log)
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        processes.append(process)
        return process

    model = args.model or ("gpt-5.6-terra" if args.provider == "codex" else "")
    cli_arguments = {"codex": ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox"],
                     "workbuddy": ["-p", "-y", "--output-format", "text"],
                     "minimax": ["--help"]}[args.provider]
    if args.provider == "minimax":
        raise RuntimeError("Verify MiniMax CLI headless arguments before enabling this provider")
    if model and args.provider == "workbuddy": cli_arguments += ["--model", model]
    report = {"environment": "isolated-local-real-provider", "provider": args.provider, "model": model or "provider-default",
              "workspace": str(workspace), "broker_container": container, "passed": False}
    worker_binary = ROOT / "tmp/worker-owned-e2e/node-bin/AgentBoard.Node.dll"
    report["worker_binary_sha256"] = hashlib.sha256(worker_binary.read_bytes()).hexdigest()
    report["source_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    try:
        subprocess.run(["docker", "run", "-d", "--name", container,
            "--label", "agentboard.test=worker-owned-e2e", "-p", f"127.0.0.1:{broker_port}:5672",
            "-e", "RABBITMQ_DEFAULT_USER=worker_test", "-e", "RABBITMQ_DEFAULT_PASS",
            "rabbitmq:3.13-management-alpine"], env=environment, check=True, capture_output=True)
        print(f"Isolated broker created: {container}", flush=True)
        backend = start([sys.executable, "-m", "uvicorn", "agentboard.api:app", "--host", "127.0.0.1", "--port", str(api_port)], "api.log", environment)
        client = httpx.Client(base_url=f"http://127.0.0.1:{api_port}", timeout=30)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if backend.poll() is not None:
                raise RuntimeError("Test backend failed to start; see api.log")
            try:
                if client.get("/api/health").is_success:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Test backend startup timeout")
        # Initial user/project/Epic fixture only. No Story, Task, assignment,
        # outcome or Run is inserted by the harness.
        os.environ.update(environment)
        sys.path.insert(0, str(ROOT / "src/backend-fastapi"))
        from agentboard.core.infrastructure.database import SessionLocal
        from agentboard.features.identity.service import register_user
        from agentboard.features.projects.service import create_project, create_epic
        from agentboard.features.projects.models import ProjectMember
        with SessionLocal() as s:
            owner = register_user(s, username="worker-e2e", password=password)
            project = create_project(s, name=f"Worker-owned real {args.provider} E2E", key="WE2E")
            s.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
            epic = create_epic(s, project_id=project.id, title="Seven work kinds", description="Isolated real provider acceptance")
            s.commit()
            project_id, epic_id = project.id, epic.id
        login = client.post("/api/auth/login", json={"username": "worker-e2e", "password": password})
        login.raise_for_status()
        token = login.json()["token"]
        client.headers["Authorization"] = "Bearer " + token
        config = {"Worker": {"Id": f"real-{args.provider}-worker", "HistoryDatabasePath": str(output / "node.db")},
            "WorkerOwned": {"Enabled": True, "ReconcileSeconds": 3,
                "Projects": [{"ProjectId": project_id, "LocalPath": str(workspace)}],
                "Agents": [{"Id": args.provider + "-a", "Provider": args.provider,
                    "WorkKinds": ["proposal", "design", "dev", "qa_review"], "Runtime": {
                        "Command": args.cli, "Model": model, "TimeoutMinutes": 12,
                        "MaxCapturedOutputChars": 100000,
                        "Arguments": cli_arguments}},
                    {"Id": args.provider + "-b", "Provider": args.provider,
                    "WorkKinds": ["design_review", "dev_review", "qa"], "Runtime": {
                        "Command": args.cli, "Model": model, "TimeoutMinutes": 12,
                        "MaxCapturedOutputChars": 100000,
                        "Arguments": cli_arguments}}]},
            "DurableExecution": {"Enabled": False},
            "AgentBoard": {"ServerUrl": str(client.base_url).rstrip("/")},
            "Portal": {"Urls": f"http://127.0.0.1:{portal_port}"},
            "ProcessExecutor": {"LogDirectory": str(output / "provider-logs"), "MaxOutputBytes": 2_000_000}}
        # Nonsecret runtime configuration is an evidence artifact. Credentials
        # are passed only through the process environment, never printed.
        (output / "node-config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        groups = [[profile] for profile in config["WorkerOwned"]["Agents"]] if args.split_workers else [config["WorkerOwned"]["Agents"]]
        nodes = []
        for index, profiles in enumerate(groups):
            local = copy.deepcopy(config)
            local["WorkerOwned"]["Agents"] = profiles
            local["Worker"]["Id"] += f"-{index}"
            local["Worker"]["HistoryDatabasePath"] = str(output / f"node-{index}.db")
            local["Portal"]["Urls"] = f"http://127.0.0.1:{port()}"
            node_env = environment.copy()
            def flatten(prefix, value):
                if isinstance(value, dict):
                    for key, child in value.items(): flatten(prefix + [key], child)
                elif isinstance(value, list):
                    for number, child in enumerate(value): flatten(prefix + [str(number)], child)
                else:
                    node_env["__".join(prefix)] = str(value).lower() if isinstance(value, bool) else str(value)
            flatten([], local)
            node_env["AgentBoard__StartupToken"] = token
            node_env["RabbitMq__Uri"] = environment["AGENTBOARD_MQ_URL"]
            nodes.append(start(["dotnet", str(worker_binary)], f"node-{index}.log", node_env))
        proposal = client.post("/api/proposals", json={"project_id": project_id, "target_epic_id": epic_id,
            "title": "AgentBoard E2E local greeting endpoint", "auto_create_ticket": True,
            "content": "这是隔离测试分支上的小功能，不影响 KnowledgeVault 主应用。新增 tools/agentboard_e2e/greeting_server.py：仅用 Python 标准库，在 127.0.0.1 指定端口提供 GET /greet?name=...，返回 JSON {message: Hello, <name>!}；缺少或空 name 用 world，支持中文，其他路径返回 404。提供命令行 --port（默认 18765），可直接启动并关闭。新增 unittest，覆盖默认名、中文和未知路径。设计写 docs/agentboard-e2e-greeting-design.md。新增文件均可提交；测试缓存/运行报告放忽略目录或系统临时目录。不要调用外网、不要改现有主应用、不要 push。QA 必须实际在本机启动该 HTTP 服务，用 HTTP 请求验证并记录部署命令、测试步骤和结果。验收与范围已完整，无需提问；最终 spec 的开发拆单只用一条 - [ ]，设计和独立 QA 由本地工作流补齐。"})
        proposal.raise_for_status()
        proposal_id = proposal.json()["id"]
        queued = client.put(f"/api/proposals/{proposal_id}/status", json={"status": "queued"})
        queued.raise_for_status()
        report.update(project_id=project_id, proposal_id=proposal_id)
        print(f"Submitted Proposal {proposal_id}; two real {args.provider} profiles; other providers disabled", flush=True)
        deadline, previous = time.monotonic() + args.timeout, None
        while time.monotonic() < deadline:
            if any(node.poll() is not None for node in nodes):
                raise RuntimeError("Worker exited; inspect node.log")
            p = client.get(f"/api/proposals/{proposal_id}").json()
            sid = p.get("story_id")
            tasks = [t for t in client.get(f"/api/worker-work/snapshot?project_id={project_id}&entity_type=task").json().get("items", [])
                     if sid and t.get("story_id") == sid]
            state = (p.get("status"), tuple((t["id"], t["type"], t["status"]) for t in tasks))
            if state != previous:
                print(json.dumps({"proposal": state[0], "tasks": state[1]}, ensure_ascii=False), flush=True)
                previous = state
            report.update(proposal_status=p.get("status"), story_id=sid, tasks=tasks)
            if sid and (sum(t["type"] == "design" for t in tasks) != 1
                    or sum(t["type"] == "dev" for t in tasks) != 1
                    or sum(t["type"] == "qa" and not any(label.startswith("qa-source-work:")
                        for label in json.loads(t.get("labels", "[]"))) for t in tasks) != 1
                    or any(t["type"] not in {"design", "dev", "bug", "qa"} for t in tasks)
                    or any(t["type"] == "bug" and not any(label.startswith("qa-source-work:")
                        for label in json.loads(t.get("labels", "[]"))) for t in tasks)):
                raise RuntimeError("This small fixture requires one Design, one Dev and one QA Task; plan over-decomposed")
            business = evidence(output / "business.db", proposal_id)
            if any(count >= 3 for count in business["failure_reasons"].values()):
                raise RuntimeError("Provider reached three failures with the same recorded cause; switch provider")
            if any(work["state"] == "failed" for work in business["works"]):
                raise RuntimeError("Work is terminal or requires manual reconciliation; evidence retained")
            if sid:
                story = client.get(f"/api/stories/{sid}").json()
                report["story_status"] = story.get("status")
                if story.get("status") == "done":
                    assert {"design", "dev", "qa"} <= {t["type"] for t in tasks}
                    assert all(t["status"] == "done" for t in tasks)
                    assert business["passed"], "Seven-kind and independent-Agent evidence is incomplete"
                    report["passed"] = True
                    print("PASS: Proposal -> Story -> Design/review -> Dev/review -> independent QA/review -> Story done", flush=True)
                    break
            if p.get("status") in ("failed", "awaiting") or any(t["status"] == "blocked" for t in tasks):
                raise RuntimeError("Real workflow failed or needs input; evidence retained")
            time.sleep(5)
        if not report["passed"]:
            raise RuntimeError("Real workflow timed out")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        if report.get("proposal_id"):
            try:
                business = evidence(output / "business.db", report["proposal_id"])
                (output / "business-evidence.json").write_text(json.dumps(business, ensure_ascii=False, indent=2), encoding="utf-8")
                if report["passed"] and not business["passed"]:
                    report["passed"] = False
                    report["error"] = "Business evidence did not confirm all seven kinds and independent Agents"
            except Exception as audit_error:
                report["passed"] = False
                report["evidence_error"] = str(audit_error)
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        for process in reversed(processes):
            if process.poll() is None:
                # Only children launched by this harness, never provider-wide
                # process-name termination (desktop apps may also be running).
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                else:
                    process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill()
        for log in logs: log.close()
        subprocess.run(["docker", "stop", container], capture_output=True)
        print(f"Evidence: {output / 'report.json'}; isolated broker stopped and retained", flush=True)


if __name__ == "__main__":
    main()
