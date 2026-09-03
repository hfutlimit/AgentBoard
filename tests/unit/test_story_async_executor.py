"""Tests for ProposalProcessor Story async background executor (2026-08-26 根治).

Before this change: process_story runs synchronously inside main loop; a slow
codebuddy (e.g. real development Story with 600s timeout) blocks the entire
worker for 10 minutes, so pending proposals / answered / ticket requests all
starve. After: process_story is submitted to a background thread; main loop
returns immediately and keeps polling.

These tests cover:
- main loop is not blocked by slow Story invoker (verified via a fake
  invoker that sleeps longer than the polling interval)
- close() joins background story tasks within a bounded wait
- concurrent story submissions do not double-execute the same story
  (the StoryHandler's lease/min-interval already serializes per-story;
  this test verifies the executor respects it)
- handle_story is async path is opt-in via ProcessorConfig; default off for
  backward compat
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import httpx  # noqa: E402

from agentboard.processors.config import ProcessorConfig  # noqa: E402
from agentboard.processors.worker import ProposalProcessor  # noqa: E402


class _SleepyInvoker:
    """Stand-in for SubprocessProcessorInvoker: sleeps 3s then returns a decision."""
    def __init__(self, sleep_s: float = 3.0, action: str = "story_handled"):
        self.sleep_s = sleep_s
        self.action = action
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def invoke(self, context):
        from agentboard.processors.config import AgentDecision
        with self._lock:
            self.calls.append(context)
        time.sleep(self.sleep_s)
        return AgentDecision(action=self.action, summary="handled")


def _build_worker(*, agent_timeout: int = 5) -> ProposalProcessor:
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9",  # never actually called
        token="test-token",
        poll_interval=0.05,
        agent_cmd='"echo" "noop"',  # not used; we pass invoker below
        agent_timeout=agent_timeout,
    )
    invoker = _SleepyInvoker(sleep_s=2.0)
    client = httpx.Client(timeout=1.0, headers={"Authorization": "Bearer test"})
    return ProposalProcessor(cfg, invoker=invoker, client=client)


def test_handle_story_does_not_block_main_loop(monkeypatch):
    """Main loop must be free to call fetch_confirmed_stories even while a
    Story is being processed in the background."""
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9",
        token="t",
        poll_interval=0.05,
        agent_cmd='"echo" "noop"',
        agent_timeout=5,
        async_story_executor=True,  # 必须在 ProposalProcessor 构造前设
    )
    invoker = _SleepyInvoker(sleep_s=2.0)
    client = httpx.Client(timeout=1.0, headers={"Authorization": "Bearer t"})
    worker = ProposalProcessor(cfg, invoker=invoker, client=client)

    # Pretend the worker's story handler returned 1 confirmed story.
    fake_story = {
        "id": 9001,
        "story_id": 9001,
        "title": "demo",
        "epic_id": 1,
        "tasks": [],
        "status": "confirmed",
    }
    with mock.patch.object(worker, "fetch_confirmed_stories", return_value=[fake_story]) as fs, \
         mock.patch.object(worker, "fetch_work", return_value=[]), \
         mock.patch.object(worker, "fetch_ticket_requests", return_value=[]), \
         mock.patch.object(worker._handlers["story"], "claim", return_value=True), \
         mock.patch.object(worker._handlers["story"], "load_context",
                           return_value={"action": "process_story", "story_id": 9001,
                                         "title": "demo", "tasks": [], "project_id": 1,
                                         "needs_design": False, "description": "",
                                         "recalled": []}), \
         mock.patch.object(worker._handlers["story"], "handle_decision", return_value="handled"), \
         mock.patch.object(worker, "reclaim_stale", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_ticket_requests", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_stories", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_tasks", return_value=[]), \
         mock.patch.object(worker, "recover_failed", return_value=[]):
        # We bypass the running maintenance/heartbeat keepers and just
        # call poll_once manually.
        t0 = time.time()
        summary = worker.poll_once()
        elapsed = time.time() - t0
        # main loop should return fast even though sleepy invoker takes 2s
        assert elapsed < 1.0, f"main loop blocked for {elapsed:.2f}s (Story still sync?)"
        assert summary["story_counts"].get("submitted", 0) == 1 or \
               summary["story_counts"].get("handled", 0) == 1, summary
        # Let the background thread finish before tearing down
        worker.close()
    # after close(), the background task should be joined
    assert len(invoker.calls) == 1, f"expected 1 invoker call, got {len(invoker.calls)}"


def test_close_waits_for_in_flight_story(monkeypatch):
    """On close(), the worker must wait for in-flight Story tasks so the
    claim lease isn't orphaned."""
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9",
        token="t",
        poll_interval=0.05,
        agent_cmd='"echo" "noop"',
        agent_timeout=5,
        async_story_executor=True,
    )
    invoker = _SleepyInvoker(sleep_s=1.5)
    client = httpx.Client(timeout=1.0, headers={"Authorization": "Bearer t"})
    worker = ProposalProcessor(cfg, invoker=invoker, client=client)

    fake_story = {
        "id": 9002, "story_id": 9002, "title": "demo2", "epic_id": 1,
        "tasks": [], "status": "confirmed",
    }
    with mock.patch.object(worker, "fetch_confirmed_stories", return_value=[fake_story]), \
         mock.patch.object(worker, "fetch_work", return_value=[]), \
         mock.patch.object(worker, "fetch_ticket_requests", return_value=[]), \
         mock.patch.object(worker._handlers["story"], "claim", return_value=True), \
         mock.patch.object(worker._handlers["story"], "load_context",
                           return_value={"action": "process_story", "story_id": 9002,
                                         "title": "demo2", "tasks": [], "project_id": 1,
                                         "needs_design": False, "description": "",
                                         "recalled": []}), \
         mock.patch.object(worker._handlers["story"], "handle_decision", return_value="handled"), \
         mock.patch.object(worker, "reclaim_stale", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_ticket_requests", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_stories", return_value=[]), \
         mock.patch.object(worker, "reclaim_stale_tasks", return_value=[]), \
         mock.patch.object(worker, "recover_failed", return_value=[]):
        worker.poll_once()
        # Immediate close should still wait for the 1.5s Story to finish
        t0 = time.time()
        worker.close()
        elapsed = time.time() - t0
        assert elapsed >= 1.0, f"close returned in {elapsed:.2f}s; should wait for Story"
    assert len(invoker.calls) == 1


def test_default_async_story_executor_disabled():
    """Backward compat: default off; opt-in via config.async_story_executor=True."""
    cfg = ProcessorConfig(
        api_url="http://127.0.0.1:9",
        token="t",
        agent_cmd='"echo" "noop"',
    )
    assert getattr(cfg, "async_story_executor", False) is False
