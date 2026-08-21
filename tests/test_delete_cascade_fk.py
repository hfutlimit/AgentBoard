"""delete_epic / delete_story FK 防御级联回归测试（v7.3 e2e 收尾修复）。

**根因**：Epic 140 切片 1/3 引入 ``task_outcome`` / ``episode_embedding`` /
``project_playbook*`` / ``project_playbook_episode`` 后（FK → tasks.id，NO ACTION），
旧 ``delete_epic`` / ``delete_story`` 走裸批量 delete 绕过了中央 ``delete_task``
的防御性清理。task 走过 done（触发 ``_record_learning_outcome`` 落库）后再删
epic/story，SQLite 抛 ``FOREIGN KEY constraint failed`` → HTTP 500。

**修复**：``delete_epic`` / ``delete_story`` 改为逐 task 调中央 ``delete_task``，
同时清 story 级 ``ReviewVote`` 锚点 + 解绑 ``agent_schedules.epic_id``。
中央 ``delete_task`` 同步补 ``ReviewVote.comment_id`` 防御（删 task comment
前先 NULL 化引用）。

**测试覆盖**：
- 删已 done 的 task 所属 epic → 不 500，task_outcome / episode / playbook 同步清
- 删已 done 的 task 所属 story → 不 500
- 删 task + 含评审 comment 的 task → 不 500（ReviewVote.comment_id 解绑）
- 删绑定了 agent_schedule 的 epic → schedule.epic_id 被 NULL 化、保留 schedule
"""
from __future__ import annotations

import os
import sys
import tempfile

# 隔离 DB（避免污染 dev 库 / 跨测试串扰）
_DB = tempfile.mktemp(prefix="delete_cascade_fk_", suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
# 禁用后台 judge daemon 线程
os.environ["AGENTBOARD_JUDGE_AUTO"] = "0"

# 强制 reload agentboard 模块以拾取新的 AGENTBOARD_DB_URL
for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.core.common.enums import Status, StatusReason
from agentboard.database import SessionLocal, init_db


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    yield s
    s.close()


def _mk_env(s):
    """建 user + project + epic + story（v7.3 默认自动编排 Story/Task），返回 chain。"""
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"u_{suffix}", password="password123")
    p = service.create_project(s, name=f"P_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E_{suffix}")
    # create_epic 会自动 create_story（默认 needs_design），story 也会自动
    # 建 "设计：" + "开发：" 2 个子 task。直接 list 取。
    stories = service.list_stories(s, e.id)
    assert stories, "create_epic should auto-create one story"
    st = stories[0]
    return u, p, e, st


def _list_tasks(s, story_id: int):
    return s.query(service.Task).filter(service.Task.story_id == story_id).all()


def _move_to_done(s, t) -> None:
    """模拟真实生产路径：todo → in_progress → done（带 status_reason）。"""
    service.update_task(s, t.id, status=Status.IN_PROGRESS)
    s.commit()
    service.update_task(
        s, t.id, status=Status.DONE, status_reason=StatusReason.COMPLETED,
    )
    s.commit()


# ---------------------------------------------------------------------------
# 核心回归
# ---------------------------------------------------------------------------

def test_delete_epic_with_done_task_outcome_succeeds(session):
    """**根因回归**：task 走 done（落 task_outcome + episode + playbook）后
    删所属 epic → 不 500，outcome/episode/playbook_episode 全部联动清空。
    """
    u, p, e, st = _mk_env(session)
    tasks = _list_tasks(session, st.id)
    assert len(tasks) == 2, f"expected 2 auto-generated tasks, got {len(tasks)}"
    # 选一个 task 走到 done（落 learning outcome）
    _move_to_done(session, tasks[0])
    done_task_id = tasks[0].id
    ep_id = e.id
    st_id = st.id

    # 落 outcome / episode / playbook_episode 三张表（NO ACTION FK 指向 task）
    from agentboard.features.learning.models import (
        EpisodeEmbedding, ProjectPlaybookEpisode, TaskOutcome,
    )
    assert session.query(TaskOutcome).filter(TaskOutcome.task_id == done_task_id).count() == 1
    assert session.query(EpisodeEmbedding).filter(
        EpisodeEmbedding.episode_id == done_task_id
    ).count() == 1
    assert session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.episode_id == done_task_id
    ).count() == 1

    # 删 epic：旧实现会 500，新实现必须成功
    assert service.delete_epic(session, ep_id) is True

    # 全部联动清理：learning 引用 + story + tasks
    assert session.query(TaskOutcome).filter(TaskOutcome.task_id == done_task_id).count() == 0
    assert session.query(EpisodeEmbedding).filter(
        EpisodeEmbedding.episode_id == done_task_id
    ).count() == 0
    assert session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.episode_id == done_task_id
    ).count() == 0
    assert session.query(service.Epic).filter(service.Epic.id == ep_id).count() == 0
    assert session.query(service.Story).filter(service.Story.id == st_id).count() == 0
    assert session.query(service.Task).filter(
        service.Task.story_id == st_id
    ).count() == 0


def test_delete_story_with_done_task_outcome_succeeds(session):
    """delete_story 根因回归：task done → 落 outcome → 删 story 不 500。"""
    u, p, e, st = _mk_env(session)
    tasks = _list_tasks(session, st.id)
    _move_to_done(session, tasks[0])
    st_id = st.id
    ep_id = e.id

    # 删 story
    assert service.delete_story(session, st_id) is True
    assert session.query(service.Story).filter(service.Story.id == st_id).count() == 0
    # epic 仍保留（只删 story）
    assert session.query(service.Epic).filter(service.Epic.id == ep_id).count() == 1


def test_delete_task_with_review_vote_comment_unbinds_fk(session):
    """**根因回归**（v7.3 收尾 fix-2）：删 task 时若其 comment 被 ``ReviewVote`` 引用
    （FK comment_id NO ACTION），必须先 NULL 化 vote.comment_id，否则撞 FK。
    """
    u, p, e, st = _mk_env(session)
    tasks = _list_tasks(session, st.id)
    t = tasks[0]
    t_id = t.id

    # 手动建 task comment + ReviewVote（模拟评审环节）
    c = service.create_comment(
        session, author=u.username, content="评审意见：reject", task_id=t_id,
    )
    c_id = c.id
    # 注册一个评审用户（避免 user 表外键问题）
    import uuid
    reviewer = service.register_user(
        session, username=f"rev_{uuid.uuid4().hex[:8]}", password="password123",
    )
    # 直接插 ReviewVote（entity_type=task 锚定本 task）
    from agentboard.features.projects.models import ReviewVote
    vote = ReviewVote(
        entity_type="task", entity_id=t_id, reviewer_user_id=reviewer.id,
        verdict="reject", comment_id=c_id, round=1,
    )
    session.add(vote); session.commit()
    vote_id = vote.id
    assert session.query(ReviewVote).filter(ReviewVote.comment_id == c_id).count() == 1

    # 删 task：旧实现会 500（先删 comment 撞 vote.comment_id FK）
    assert service.delete_task(session, t_id) is True
    # vote.comment_id 已被 NULL 化
    vote_after = session.query(ReviewVote).filter(ReviewVote.id == vote_id).one()
    assert vote_after.comment_id is None


# ---------------------------------------------------------------------------
# 关联清理：agent_schedule / review_votes 锚点
# ---------------------------------------------------------------------------

def test_delete_epic_unbinds_agent_schedule_fk(session):
    """绑了 agent_schedule 的 epic 被删时，schedule.epic_id 必须被 NULL 化
    （NO ACTION FK 防御；schedule 配置保留不丢）。"""
    u, p, e, st = _mk_env(session)
    # 建一个 schedule 绑定到该 epic
    from agentboard.features.scheduling.service import create_schedule
    sched = create_schedule(
        session, project_id=p.id, title="daily dev",
        schedule_type="cron", cron_expr="0 9 * * *", epic_id=e.id,
    )
    sched_id = sched.id
    assert sched.epic_id == e.id
    ep_id = e.id

    # 删 epic
    assert service.delete_epic(session, ep_id) is True
    # 走新 session 验证 schedule 仍存在、epic_id 已 NULL
    s2 = SessionLocal()
    try:
        from agentboard.features.scheduling.models import AgentSchedule
        sched2 = s2.query(AgentSchedule).filter(AgentSchedule.id == sched_id).one()
        assert sched2.epic_id is None
        assert sched2.id == sched_id
    finally:
        s2.close()


def test_delete_epic_unbinds_review_vote_anchor(session):
    """删 epic 时其下 story 的 review_votes 锚点必须被切断（entity_id 置 -1）。"""
    u, p, e, st = _mk_env(session)
    # 手动建一条针对该 story 的评审投票
    from agentboard.features.projects.models import ReviewVote
    import uuid
    voter = service.register_user(
        session, username=f"voter_{uuid.uuid4().hex[:8]}", password="password123",
    )
    c = service.create_comment(
        session, author=u.username, content="story review", story_id=st.id,
    )
    c_id = c.id
    vote = ReviewVote(
        entity_type="story", entity_id=st.id, reviewer_user_id=voter.id,
        verdict="approve", comment_id=c_id, round=1,
    )
    session.add(vote); session.commit()
    vote_id = vote.id
    st_id = st.id
    ep_id = e.id

    assert service.delete_epic(session, ep_id) is True
    # vote 实体仍存在，entity_id 已被置 -1（截断）
    s2 = SessionLocal()
    try:
        vote2 = s2.query(ReviewVote).filter(ReviewVote.id == vote_id).one()
        assert vote2.entity_id == -1
        assert vote2.comment_id is None  # story 删时级联清 comment，vote 引用截断
    finally:
        s2.close()


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------

def test_delete_epic_not_found_returns_false(session):
    """不存在的 epic_id 返回 False（不抛 500）。"""
    assert service.delete_epic(session, 999_999) is False


def test_delete_story_not_found_returns_false(session):
    assert service.delete_story(session, 999_999) is False
