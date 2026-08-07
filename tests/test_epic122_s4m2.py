"""Epic 122 切片 4 M2（Story 233 / Task 1017）：多数决评审投票进度提示。

覆盖（对应任务验收）：
1. get_review_stats 顶层新增 review_mode / review_quorum（默认 single / 3）；
2. majority 模式 votes 结构：pending_review Story 与 in_review Task 各自列出
   approve/reject/cast/quorum/kind/id/title/status；非 pending 不出现；
3. _review_vote_counts 统计正确：一人一票 upsert 后 approve/reject 计数正确；
4. single 模式 votes 恒为空数组（零行为变化）；
5. API 透传：/api/review-stats 返回含 review_mode/review_quorum/votes 字段
   （真实 uvicorn 子进程 + httpx，REQUIRE_AUTH=0 匿名可读）。

运行：
    PYTHONPATH=. python -m pytest tests/test_epic122_s4m2.py -q
"""
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)
# 单测内用 monkeypatch 切换模式；此处确保默认 single
os.environ.pop("AGENTBOARD_REVIEW_MODE", None)
os.environ.pop("AGENTBOARD_REVIEW_QUORUM", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402

init_db()

import uuid


def _seed():
    """1 项目 + dev + 3 个 reviewer Agent（r1/r2/r3，均在线）+ 1 epic。"""
    tag = uuid.uuid4().hex[:8]
    with SessionLocal() as s:
        p = service.create_project(s, name=f"S4M2 P{tag}")
        dev = service.register_user(s, username=f"s4m2-dev-{tag}", password="password123")
        r1 = service.register_user(s, username=f"s4m2-r1-{tag}", password="password123")
        r2 = service.register_user(s, username=f"s4m2-r2-{tag}", password="password123")
        r3 = service.register_user(s, username=f"s4m2-r3-{tag}", password="password123")
        for uid in (dev.id, r1.id, r2.id, r3.id):
            service.add_project_member(s, project_id=p.id, user_id=uid, role="member")
        for i, uid in ((1, r1.id), (2, r2.id), (3, r3.id)):
            aid = f"s4m2-a{i}-{tag}"
            service.register_agent(s, agent_id=aid, name=f"A{i}",
                                   roles='["reviewer"]', user_id=uid)
            service.agent_heartbeat(s, aid, user_id=uid)
        epic = service.create_epic(s, project_id=p.id, title=f"S4M2 Epic{tag}")
        s.commit()
        return p.id, dev.id, r1.id, r2.id, r3.id, epic.id


@pytest.fixture(scope="function")
def seeded():
    return _seed()


def _pending_story(s, epic_id, *, reviewer_id, round_=0):
    """直建 pending_review Story（绕开 assign_reviewer 的随机性）。"""
    from agentboard.models import Story
    st = Story(epic_id=epic_id, title="S4M2 pending story", status="pending_review",
               reviewer_id=reviewer_id, review_round=round_)
    s.add(st)
    s.flush()
    return st


def _inreview_task(s, project_id, *, reviewer_id, assignee_id=None, round_=0):
    from agentboard.models import Task
    t = Task(project_id=project_id, title="S4M2 inreview task", type="task",
             status="in_review", reviewer_id=reviewer_id,
             assignee_id=assignee_id, review_round=round_)
    s.add(t)
    s.flush()
    return t


def _ready_story(s, epic_id, *, reviewer_id):
    """非 pending（ready）Story —— 不应出现在 votes 中。"""
    from agentboard.models import Story
    st = Story(epic_id=epic_id, title="S4M2 done story", status="ready",
               reviewer_id=reviewer_id, review_round=0)
    s.add(st)
    s.flush()
    return st


# ---------- 1. review_mode / review_quorum ----------

def test_review_mode_default_single(monkeypatch):
    monkeypatch.delenv("AGENTBOARD_REVIEW_MODE", raising=False)
    assert service.get_review_mode() == "single"


def test_review_mode_env(monkeypatch):
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    assert service.get_review_mode() == "majority"
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "bogus")
    assert service.get_review_mode() == "single"


def test_review_quorum_default_and_env(monkeypatch):
    monkeypatch.delenv("AGENTBOARD_REVIEW_QUORUM", raising=False)
    assert service.get_review_quorum() == 3
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "5")
    assert service.get_review_quorum() == 5
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "99")  # 超范围 → 回退 3
    assert service.get_review_quorum() == 3
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "abc")
    assert service.get_review_quorum() == 3


# ---------- 2. majority 模式 votes 结构 ----------

def test_stats_majority_votes_structure(seeded, monkeypatch):
    pid, dev, r1, r2, r3, epic_id = seeded
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    with SessionLocal() as s:
        _pending_story(s, epic_id, reviewer_id=r1)                 # pending story
        _ready_story(s, epic_id, reviewer_id=r2)                   # 非 pending 不应出现
        _inreview_task(s, pid, reviewer_id=r1, assignee_id=dev)  # in_review task
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        assert stats["review_mode"] == "majority"
        assert stats["review_quorum"] == 3
        votes = stats["votes"]
        assert len(votes) == 2
        by_kind = {v["kind"]: v for v in votes}
        assert set(by_kind) == {"story", "task"}
        st_v = by_kind["story"]
        assert st_v["status"] == "pending_review"
        assert st_v["approve"] == 0 and st_v["reject"] == 0 and st_v["cast"] == 0
        assert st_v["quorum"] == 3
        assert st_v["title"] == "S4M2 pending story"
        tk_v = by_kind["task"]
        assert tk_v["status"] == "in_review"
        assert tk_v["quorum"] == 3
        # ready story 未出现在 votes
        assert all(v["status"] != "ready" for v in votes)


def test_stats_majority_votes_counts(seeded, monkeypatch):
    """已投票实体：approve/reject/cast 统计正确（经 _vote_majority 写票）。"""
    pid, dev, r1, r2, r3, epic_id = seeded
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    with SessionLocal() as s:
        st = _pending_story(s, epic_id, reviewer_id=r1)
        t = _inreview_task(s, pid, reviewer_id=r2, assignee_id=dev)
        s.commit()
        st_id, t_id = st.id, t.id
    # r1 对 story 投 approve；r2 对 story 投 approve；r3 对 story 投 approve
    # （达 quorum → 结算 ready → 从 pending 消失）；改为只投 2 票保持 pending
    with SessionLocal() as s:
        _vote(s, "story", st_id, r1, "approve")
        _vote(s, "story", st_id, r2, "approve")
    with SessionLocal() as s:
        _vote(s, "task", t_id, r1, "approve")
        _vote(s, "task", t_id, r2, "reject")
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        votes = {v["kind"]: v for v in stats["votes"]}
        assert votes["story"]["approve"] == 2
        assert votes["story"]["reject"] == 0
        assert votes["story"]["cast"] == 2
        assert votes["task"]["approve"] == 1
        assert votes["task"]["reject"] == 1
        assert votes["task"]["cast"] == 2


def _vote(s, entity_type, entity_id, reviewer_user_id, verdict):
    """直插 ReviewVote（复用 service 私有 upsert，模拟真实投票）。"""
    service._upsert_review_vote(s, entity_type=entity_type, entity_id=entity_id,
                                reviewer_user_id=reviewer_user_id, verdict=verdict,
                                comment_id=None, round=0)
    s.commit()


# ---------- 3. single 模式零行为变化 ----------

def test_stats_single_mode_votes_empty(seeded):
    pid, dev, r1, r2, r3, epic_id = seeded
    # 默认 single（模块级已清 env）
    with SessionLocal() as s:
        _pending_story(s, epic_id, reviewer_id=r1)
        _inreview_task(s, pid, reviewer_id=r2, assignee_id=dev)
        s.commit()
    with SessionLocal() as s:
        stats = service.get_review_stats(s, project_id=pid)
        assert stats["review_mode"] == "single"
        assert stats["review_quorum"] == 3
        assert stats["votes"] == []


# ---------- 4. API 透传（真实 uvicorn 子进程 + httpx） ----------

def test_api_review_stats_includes_votes_fields(seeded, monkeypatch):
    import subprocess
    import time

    pid, dev, r1, r2, r3, epic_id = seeded
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")

    port = _free_port()
    env = dict(os.environ)
    env["AGENTBOARD_REVIEW_MODE"] = "majority"
    env["AGENTBOARD_REVIEW_QUORUM"] = "3"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_ready(base)
        import httpx
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{base}/api/review-stats", params={"project_id": pid})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["review_mode"] == "majority"
            assert data["review_quorum"] == 3
            assert isinstance(data["votes"], list)
            # 当前项目无 pending 实体（seed 未建）→ votes 为空数组（结构仍存在）
            assert data["votes"] == []
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def test_api_review_stats_majority_pending_votes(seeded, monkeypatch):
    """真实子进程 + 造 pending 实体 + 投票 → votes 含进度。"""
    import subprocess

    pid, dev, r1, r2, r3, epic_id = seeded
    monkeypatch.setenv("AGENTBOARD_REVIEW_MODE", "majority")
    monkeypatch.setenv("AGENTBOARD_REVIEW_QUORUM", "3")
    with SessionLocal() as s:
        st = _pending_story(s, epic_id, reviewer_id=r1)
        s.commit()
        st_id = st.id
    with SessionLocal() as s:
        _vote(s, "story", st_id, r1, "approve")
        _vote(s, "story", st_id, r2, "approve")

    port = _free_port()
    env = dict(os.environ)
    env["AGENTBOARD_REVIEW_MODE"] = "majority"
    env["AGENTBOARD_REVIEW_QUORUM"] = "3"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agentboard.api:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        _wait_ready(base)
        import httpx
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{base}/api/review-stats", params={"project_id": pid})
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["review_mode"] == "majority"
            votes = data["votes"]
            assert len(votes) == 1
            v = votes[0]
            assert v["kind"] == "story"
            assert v["approve"] == 2 and v["reject"] == 0 and v["cast"] == 2
            assert v["quorum"] == 3
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


# ---------- helpers ----------

def _free_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_ready(base, timeout=15):
    import time
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/api/meta", timeout=2)
            if r.status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.4)
    raise RuntimeError(f"uvicorn not ready at {base}")
