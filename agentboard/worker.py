"""Proposal clarification worker using REST plus optional RabbitMQ wake-ups."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
import subprocess
import threading
import time

import httpx

from .mq import MQConfig, PikaBroker


log = logging.getLogger("agentboard.proposal-worker")


@dataclass(frozen=True)
class WorkerConfig:
    api_url: str
    token: str
    username: str
    password: str
    agent_name: str
    command: str
    poll_seconds: float
    lease_seconds: int

    @classmethod
    def from_env(cls) -> "WorkerConfig":
        return cls(
            api_url=os.getenv("AGENTBOARD_API_URL", "http://127.0.0.1:8000").rstrip("/"),
            token=os.getenv("AGENTBOARD_WORKER_TOKEN", "").strip(),
            username=os.getenv("AGENTBOARD_WORKER_USERNAME", "").strip(),
            password=os.getenv("AGENTBOARD_WORKER_PASSWORD", ""),
            agent_name=os.getenv("AGENTBOARD_WORKER_NAME", "workbuddy").strip() or "workbuddy",
            command=os.getenv("AGENTBOARD_PROPOSAL_AGENT_COMMAND", "").strip(),
            poll_seconds=max(1.0, float(os.getenv("AGENTBOARD_WORKER_POLL_SECONDS", "10"))),
            lease_seconds=max(30, int(os.getenv("AGENTBOARD_WORKER_LEASE_SECONDS", "1800"))),
        )


class AgentCommand:
    def __init__(self, command: str):
        if not command:
            raise RuntimeError("AGENTBOARD_PROPOSAL_AGENT_COMMAND is required")
        self.command = command

    def run(self, context: dict) -> dict:
        prompt = {
            "instruction": (
                "Clarify this proposal and return JSON only. Use "
                "{action:'ask',questions:[...],summary:'...'} or "
                "{action:'finalize',converged_spec:'markdown with optional - [ ] task checklist'}."
            ),
            "context": context,
        }
        completed = subprocess.run(
            self.command,
            input=json.dumps(prompt, ensure_ascii=False),
            text=True,
            shell=True,
            capture_output=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"agent command exited {completed.returncode}")
        output = completed.stdout.strip()
        try:
            result = json.loads(output)
        except json.JSONDecodeError:
            lines = [line.strip() for line in output.splitlines() if line.strip()]
            if not lines:
                raise RuntimeError("agent command returned empty output")
            result = json.loads(lines[-1])
        if not isinstance(result, dict):
            raise RuntimeError("agent command must return a JSON object")
        return result


class ProposalWorker:
    def __init__(self, config: WorkerConfig):
        self.config = config
        self.client = httpx.Client(base_url=config.api_url, timeout=30)
        self.agent = AgentCommand(config.command)
        self._authenticate()

    def _authenticate(self) -> None:
        token = self.config.token
        if not token and self.config.username and self.config.password:
            response = self.client.post(
                "/api/auth/login",
                json={"username": self.config.username, "password": self.config.password},
            )
            response.raise_for_status()
            token = response.json()["token"]
        if not token:
            raise RuntimeError("set AGENTBOARD_WORKER_TOKEN or worker username/password")
        self.client.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs):
        response = self.client.request(method, path, **kwargs)
        if response.status_code == 409:
            return None
        response.raise_for_status()
        return response.json() if response.content else None

    def reclaim_stale(self) -> list[int]:
        result = self._request(
            "POST", "/api/proposals/reclaim-stale", json={"lease_seconds": self.config.lease_seconds}
        ) or {"items": []}
        return [item["id"] for item in result["items"]]

    def pending(self) -> list[int]:
        result = self._request("GET", "/api/proposals/pending", params={"limit": 100}) or []
        return [item["id"] for item in result]

    def process(self, proposal_id: int) -> bool:
        claimed = self._request(
            "POST", f"/api/proposals/{proposal_id}/claim", json={"agent": self.config.agent_name}
        )
        if claimed is None:
            return False
        try:
            context = self._request("GET", f"/api/proposals/{proposal_id}/context")
            decision = self.agent.run(context)
            action = decision.get("action")
            if action == "ask":
                if claimed["current_round"] >= claimed["max_rounds"]:
                    raise RuntimeError("agent requested questions after max_rounds")
                self._request(
                    "POST",
                    f"/api/proposals/{proposal_id}/questions",
                    json={
                        "round_number": claimed["current_round"] + 1,
                        "questions": decision.get("questions") or [],
                        "summary": decision.get("summary", ""),
                        "agent": self.config.agent_name,
                    },
                )
            elif action == "finalize":
                self._request(
                    "POST",
                    f"/api/proposals/{proposal_id}/finalize",
                    json={"converged_spec": decision.get("converged_spec", "")},
                )
            else:
                raise RuntimeError(f"unsupported agent action: {action!r}")
            return True
        except Exception as exc:
            log.exception("proposal %s failed", proposal_id)
            self._request("POST", f"/api/proposals/{proposal_id}/fail", json={"error": str(exc)[:20000]})
            return False

    def run_once(self) -> int:
        self.reclaim_stale()
        return sum(int(self.process(proposal_id)) for proposal_id in self.pending())

    def run_loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("proposal polling iteration failed")
            time.sleep(self.config.poll_seconds)

    def run_mq(self) -> None:
        mq_config = MQConfig.from_env()
        if mq_config is None:
            raise RuntimeError("AGENTBOARD_MQ_URL is required for --mq")
        self.reclaim_stale()
        for proposal_id in self.pending():
            self.process(proposal_id)
        # Keep database recovery active while the blocking AMQP consumer waits.
        # A second Worker instance owns its own HTTP client and relies on CAS to
        # avoid duplicate work with the message-driven instance.
        recovery = threading.Thread(
            target=lambda: ProposalWorker(self.config).run_loop(),
            name="proposal-db-recovery",
            daemon=True,
        )
        recovery.start()
        PikaBroker(mq_config).consume(lambda message: self.process(message.proposal_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentBoard Proposal worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="process the current backlog once")
    mode.add_argument("--mq", action="store_true", help="consume RabbitMQ wake-up messages")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    worker = ProposalWorker(WorkerConfig.from_env())
    if args.once:
        worker.run_once()
    elif args.mq:
        worker.run_mq()
    else:
        worker.run_loop()


if __name__ == "__main__":
    main()
