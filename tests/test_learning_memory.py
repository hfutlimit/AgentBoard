"""Epic 140 切片 3 验收测试：Episode RAG recall + Project Playbook（Story 268）。

覆盖：
- embed_text 确定性 + 归一化（零依赖）
- cosine_similarity 基本正确性
- build_episode_text 聚合 spec / 状态历史 / 评论
- store_episode 幂等 upsert（episode_id=task_id 唯一）
- recall_episodes：按 project 收敛、成功优先排序、空 spec fallback
- build_recall_section：长度预算 + 分组标记
- update_playbook 增改幂等
- get_playbook 读取（空模板 / 有内容）
- 终态自动落 episode + playbook（经 set_status 全链路）
- API：GET /api/learning/project-playbook、GET /api/learning/recall、POST playbook append
- Worker prompt 注入（build_story_prompt 含 recall 段；无 recall 时不注入）
"""
import os
import sys
import tempfile

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_JUDGE_AUTO"] = "0"  # 测试禁用后台 judge 线程

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.api import app  # noqa: F401  顶部绑定 app
from agentboard.database import SessionLocal, init_db
from agentboard.core.common.enums import Status, StatusReason
# 预导入 learning 模块（批量跑时避免 del sys.modules 重载导致延迟导入失败）
from agentboard.features.learning import memory as lm
from agentboard.features.learning.models import EpisodeEmbedding, ProjectPlaybook
from agentboard.features.workers.handlers.story import build_story_prompt


@pytest.fixture
def session():
    init_db()
    import agentboard.features.learning.memory  # noqa: F401
    s = SessionLocal()
    yield s
    s.close()


def _mk(s, name="u1", proj="p1"):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"{name}_{suffix}", password="password123")
    p = service.create_project(s, name=f"{proj}_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E-{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S-{suffix}")
    return u, p, st


def _mk_task(s, u, p, st, title="T1", assignee=True):
    t = service.create_task(s, project_id=p.id, story_id=st.id, title=title)
    if assignee:
        t.assignee_id = u.id
        s.commit()
        s.refresh(t)
    return t


def _done(s, t, u, reason=StatusReason.COMPLETED):
    service.set_status(s, t.id, Status.IN_PROGRESS, changed_by=u.id)
    return service.set_status(s, t.id, Status.DONE, changed_by=u.id, status_reason=reason)


# ---------- 向量化 ----------

def test_embed_text_deterministic_normalized():
    v1 = lm.embed_text("修复登录页 CSRF 漏洞并补充测试")
    v2 = lm.embed_text("修复登录页 CSRF 漏洞并补充测试")
    assert v1 == v2
    assert len(v1) == lm.VECTOR_DIM
    norm = sum(x * x for x in v1) ** 0.5
    assert abs(norm - 1.0) < 1e-3


def test_embed_text_similarity_orders():
    a = lm.embed_text("实现 agent leaderboard 多维聚合接口")
    b = lm.embed_text("agent leaderboard 聚合 接口")
    c = lm.embed_text("修复登录页 CSRF 漏洞")
    sim_ab = lm.cosine_similarity(a, b)
    sim_ac = lm.cosine_similarity(a, c)
    assert sim_ab > sim_ac
    assert sim_ab > 0.1


def test_cosine_similarity_identity():
    v = lm.embed_text("same text 相同的文本")
    assert lm.cosine_similarity(v, v) > 0.999
    assert lm.cosine_similarity(v, []) == 0.0


# ---------- Episode builder / store ----------

def test_build_episode_text_aggregates_context(session):
    from agentboard.features.work_items.models import Comment
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="写单元测试")
    t.spec = "为 recall 模块补充 pytest 用例"
    session.commit()
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    session.add(Comment(task_id=t.id, author="agent-a", content="实现完成，含 3 个测试用例"))
    session.commit()
    service.set_status(session, t.id, Status.DONE, changed_by=u.id,
                       status_reason=StatusReason.COMPLETED)
    search_text, summary = lm.build_episode_text(session, t)
    assert "写单元测试" in search_text
    assert "为 recall 模块补充 pytest 用例" in search_text
    assert "todo->in_progress" in search_text
    assert "in_progress->done" in search_text
    assert "实现完成" in summary
    assert "done" in summary


def test_store_episode_idempotent(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st)
    _done(session, t, u)
    ep = lm.store_episode(session, t, score=0.9, outcome="success")
    session.commit()
    assert ep is not None
    rows = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).all()
    assert len(rows) == 1
    # 再次落库：仍 1 条（upsert）
    ep2 = lm.store_episode(session, t, score=0.5, outcome="fail")
    session.commit()
    rows = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).all()
    assert len(rows) == 1
    assert rows[0].score == 0.5
    assert rows[0].outcome == "fail"


# ---------- Recall ----------

def test_recall_episodes_project_scoped_and_success_first(session):
    u, p, st = _mk(session)
    # 项目内：两个 success + 一个 fail
    for i, (title, outcome) in enumerate([
        ("实现 agent leaderboard 接口", "success"),
        ("实现 agent leaderboard 接口", "success"),
        ("实现 agent leaderboard 接口", "fail"),
    ]):
        t = _mk_task(session, u, p, st, title=title)
        _done(session, t, u)
        lm.store_episode(session, t, score=0.9 if outcome == "success" else 0.3,
                         outcome=outcome)
        session.commit()
    hits = lm.recall_episodes(session, project_id=p.id,
                              task_spec="agent leaderboard 接口")
    assert len(hits) == 3
    assert all(h["project_id"] == p.id for h in hits)
    # 成功优先：前 2 条均为 success
    assert hits[0]["outcome"] == "success"
    assert hits[1]["outcome"] == "success"
    assert hits[2]["outcome"] == "fail"


def test_recall_episodes_empty_spec_fallback(session):
    u, p, st = _mk(session)
    assert lm.recall_episodes(session, project_id=p.id, task_spec="") == []
    assert lm.recall_episodes(session, project_id=p.id, task_spec="   ") == []


def test_recall_episodes_other_project_excluded(session):
    u, p1, st1 = _mk(session, proj="pA")
    u2, p2, st2 = _mk(session, name="uB", proj="pB")
    t = _mk_task(session, u, p1, st1, title="专属项目A的回忆内容")
    _done(session, t, u)
    lm.store_episode(session, t, score=0.8, outcome="success")
    session.commit()
    hits = lm.recall_episodes(session, project_id=p2.id, task_spec="专属项目A的回忆内容")
    assert hits == []


def test_build_recall_section():
    episodes = [
        {"episode_id": 1, "task_type": "dev", "score": 0.9, "outcome": "success",
         "similarity": 0.72, "summary": "实现 leaderboard"},
        {"episode_id": 2, "task_type": "bug", "score": 0.3, "outcome": "fail",
         "similarity": 0.61, "summary": "登录 CSRF"},
    ]
    section = lm.build_recall_section(episodes)
    assert "项目历史经验" in section
    assert "✅ 成功" in section
    assert "❌ 失败" in section
    assert "#1" in section and "#2" in section
    assert len(section) <= lm.RECALL_PROMPT_CHARS
    assert lm.build_recall_section([]) == ""


# ---------- Playbook ----------

def test_update_playbook_append_and_idempotent(session):
    u, p, st = _mk(session)
    lm.update_playbook(session, project_id=p.id, task_type="dev",
                       summary="实现 recall 时注意长度预算", outcome="success")
    session.commit()
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert "成功 pattern" in pb.content_md
    assert pb.version == 1
    # 同内容重复追加 → 幂等不重复
    lm.update_playbook(session, project_id=p.id, task_type="dev",
                       summary="实现 recall 时注意长度预算", outcome="success")
    session.commit()
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert pb.content_md.count("实现 recall 时注意长度预算") == 1
    assert pb.version == 1
    # 新增 fail pattern → 追加 + version+1
    lm.update_playbook(session, project_id=p.id, task_type="bug",
                       summary="别忘验证状态迁移", outcome="fail")
    session.commit()
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert "踩坑 pattern" in pb.content_md
    assert pb.version == 2


def test_get_playbook_empty_and_filled(session):
    u, p, st = _mk(session)
    empty = lm.get_playbook(session, project_id=p.id)
    assert empty["content_md"] == "" and empty["version"] == 0
    lm.update_playbook(session, project_id=p.id, task_type="dev",
                       summary="加油", outcome="success")
    session.commit()
    filled = lm.get_playbook(session, project_id=p.id)
    assert filled["version"] == 1
    assert "加油" in filled["content_md"]


# ---------- 全链路：终态自动落 episode + playbook ----------

def test_set_status_done_auto_records_episode_and_playbook(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="自动落 episode 的验收任务")
    _done(session, t, u)
    ep = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).one_or_none()
    assert ep is not None
    assert ep.project_id == p.id
    assert ep.outcome == "success"
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one_or_none()
    assert pb is not None
    assert "自动落 episode 的验收任务" in pb.content_md


def test_blocked_task_records_fail_episode(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="阻塞的任务")
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                       status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET)
    ep = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).one_or_none()
    assert ep is not None
    assert ep.outcome == "fail"


# ---------- Worker prompt 注入 ----------

def test_story_prompt_injects_recall_section():
    context = {
        "story_id": 99,
        "title": "S99",
        "description": "desc",
        "needs_design": True,
        "tasks": [],
        "recalled": [
            {"episode_id": 1, "task_type": "dev", "score": 0.9, "outcome": "success",
             "similarity": 0.7, "summary": "参考案例"},
        ],
    }
    prompt = build_story_prompt(context)
    assert "项目历史经验" in prompt
    assert "参考案例" in prompt


def test_story_prompt_no_recall_when_empty():
    context = {"story_id": 1, "title": "S", "description": "", "needs_design": True,
               "tasks": [], "recalled": []}
    prompt = build_story_prompt(context)
    assert "项目历史经验" not in prompt


# ---------- API ----------

def test_api_project_playbook_and_recall(session):
    from fastapi.testclient import TestClient

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="api 验收任务")
    _done(session, t, u)
    session.commit()
    client = TestClient(app)
    # playbook 读取
    r = client.get(f"/api/learning/project-playbook?project_id={p.id}")
    assert r.status_code == 200
    assert r.json()["project_id"] == p.id
    assert "api 验收任务" in r.json()["content_md"]
    # recall 调试
    r2 = client.get(f"/api/learning/recall?project_id={p.id}&spec=api 验收任务")
    assert r2.status_code == 200
    data = r2.json()
    assert data["count"] >= 1
    assert "injectable" in data
    # 不存在项目 → 404
    r3 = client.get("/api/learning/project-playbook?project_id=999999")
    assert r3.status_code == 404


def test_api_playbook_append(session):
    from fastapi.testclient import TestClient

    u, p, st = _mk(session)
    session.commit()
    client = TestClient(app)
    r = client.post(
        f"/api/learning/playbook/{p.id}/append",
        json={"task_type": "dev", "summary": "手动追加的 pattern", "outcome": "success"},
    )
    assert r.status_code == 200
    assert r.json()["version"] >= 1
    # 非法 outcome → 422（项目约定 InvalidValue → 422）
    r2 = client.post(
        f"/api/learning/playbook/{p.id}/append",
        json={"task_type": "dev", "summary": "x", "outcome": "unknown"},
    )
    assert r2.status_code == 422
