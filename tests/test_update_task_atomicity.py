"""update_task 原子化回归测试（Story 265 + Phase 9 拆分 + codex review 修复）。

旧 bug：``update_task`` 先 setattr 非状态字段 + ``_commit``，再调 ``set_status``
走状态机 —— 一旦状态机抛 ``IllegalTransition``（如 done→todo），非状态字段已
commit，形成 partial commit。

修复：整函数 0 次中间 commit，事务原子边界收口到末位 _commit 一次。
"""
import os
import sys
import tempfile

# 用固定名 + uuid 后缀避免与同目录其它 test 文件 mktemp 碰撞
_DB = tempfile.mktemp(prefix="update_task_atomic_", suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ["AGENTBOARD_JUDGE_AUTO"] = "0"  # 禁用后台 judge 线程，避免测试不稳定

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

import pytest

from agentboard import service
from agentboard.core.common.enums import Status, StatusReason
# 注：异常捕获不用 IllegalTransition 类对象（class identity 在多 test 文件
# del sys.modules 后会分叉），改用 ``pytest.raises(Exception)`` + 名字匹配。
from agentboard.database import SessionLocal, init_db


@pytest.fixture
def session():
    init_db()
    s = SessionLocal()
    yield s
    s.close()


def _mk_env(s):
    import uuid
    suffix = uuid.uuid4().hex[:8]
    u = service.register_user(s, username=f"u_{suffix}", password="password123")
    p = service.create_project(s, name=f"P_{suffix}")
    e = service.create_epic(s, project_id=p.id, title=f"E_{suffix}")
    st = service.create_story(s, epic_id=e.id, title=f"S_{suffix}")
    return u, p, st


def _mk_task(s, p, st, title="T"):
    return service.create_task(s, project_id=p.id, story_id=st.id, title=title)


def test_legal_status_change_with_other_fields(session):
    """合法状态变更 + 其他字段同时改 → 全部生效。"""
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="original")
    # todo → in_progress
    t2 = service.update_task(session, t.id, title="new title", status="in_progress")
    session.commit()
    assert t2.title == "new title"
    assert t2.status == "in_progress"


def test_done_with_reason_legal(session):
    """done 状态必填 reason：合法 path 成功。"""
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="T")
    service.update_task(session, t.id, status="in_progress")
    session.commit()
    t2 = service.update_task(
        session, t.id, status="done", status_reason=StatusReason.COMPLETED,
    )
    session.commit()
    assert t2.status == "done"
    assert t2.status_reason == "completed"


def test_illegal_transition_does_not_partially_commit_other_fields(session):
    """**根因回归**：done→todo 非法 + 同时改 title，title 必须不落地。

    旧实现：title 改 → commit → set_status 抛 IllegalTransition → title 残留。
    新实现：单事务，raise 整体回滚，title 保持原值。
    """
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="original")
    # 走到 done（合法）
    service.update_task(session, t.id, status="in_progress")
    session.commit()
    service.update_task(
        session, t.id, status="done", status_reason=StatusReason.COMPLETED,
    )
    session.commit()
    session.refresh(t)
    assert t.status == "done"
    assert t.title == "original"

    # 现在尝试非法 done→todo + 改 title
    # 注：这里用「type(e).__name__ + module 名」匹配 IllegalTransition，不依赖
    # class identity。多个 test 文件用 ``del sys.modules`` 模式后，
    # pytest.collect 时再 import 会产生不同 class 对象（即使模块路径相同），
    # 导致 isinstance 检查返回 False。匹配类名+模块名更稳。
    with pytest.raises(Exception) as exc_info:
        service.update_task(
            session, t.id, title="should_not_persist", status="todo",
        )
    assert exc_info.value.__class__.__name__ == "IllegalTransition", (
        f"expected IllegalTransition, got {exc_info.value.__class__.__name__}"
    )
    session.rollback()
    session.expire_all()  # 清缓存再 refresh，避免读到旧 identity-map
    t = session.get(type(t), t.id)
    assert t.title == "original", (
        f"partial commit 回归！title={t.title!r}（应为 original）"
    )
    assert t.status == "done", (
        f"partial commit 回归！status={t.status!r}（应为 done）"
    )


def test_invalid_status_reason_does_not_partially_commit(session):
    """done 不带 reason：InvalidValue 抛出，title 也不落地。"""
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="T")
    service.update_task(session, t.id, status="in_progress")
    session.commit()
    # done 但不带 status_reason → InvalidValue
    with pytest.raises(Exception) as exc_info:
        service.update_task(
            session, t.id, title="should_not_persist", status="done",
        )
    assert exc_info.value.__class__.__name__ == "InvalidValue", (
        f"expected InvalidValue, got {exc_info.value.__class__.__name__}"
    )
    session.rollback()
    session.expire_all()
    t = session.get(type(t), t.id)
    assert t.title == "T", f"partial commit 回归：title={t.title!r}"
    assert t.status == "in_progress"


def test_non_status_change_works(session):
    """不传 status：仅改其他字段，不走状态机。"""
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="T")
    t2 = service.update_task(session, t.id, title="renamed", priority="high")
    session.commit()
    assert t2.title == "renamed"
    assert t2.priority == "high"
    assert t2.status == "todo"  # status 未变


def test_blocked_transition_atomic(session):
    """blocked 双向迁移：进 blocked 全向可达，出 blocked 限 4 目标。"""
    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="T")
    service.update_task(session, t.id, status="in_progress")
    session.commit()
    # in_progress → blocked（带 reason，合法）
    t2 = service.update_task(
        session, t.id, status="blocked",
        status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET,
    )
    session.commit()
    assert t2.status == "blocked"
    assert t2.previous_status == "in_progress"
    # blocked → in_progress（合法）
    t3 = service.update_task(session, t2.id, status="in_progress")
    session.commit()
    assert t3.status == "in_progress"
    assert t3.previous_status is None  # 出 blocked 清空


def test_delete_task_cleans_project_playbook_episode(session):
    """delete_task 必须清理 project_playbook_episode 锚点，否则 FK 约束会拒删。

    修 8/15 review P1（playbook DB 幂等）时新增 project_playbook_episode
    关联表（FK episode_id → tasks.id），delete_task 必须跟上清理，否则
    'task 走到 done → 落 episode/playbook → 用户删 task' 路径会在
    project_playbook_episode.episode_id 上撞 FK 约束 → HTTP 422。
    """
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    u, p, st = _mk_env(session)
    t = _mk_task(session, p, st, title="待删除的 task")
    service.update_task(session, t.id, status="in_progress")
    session.commit()
    service.update_task(
        session, t.id, status="done", status_reason=StatusReason.COMPLETED,
    )
    session.commit()

    # 终态触发 _record_learning_outcome → store_episode + update_playbook
    # 关联表应有 (project, episode) 一行
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.episode_id == t.id,
    ).all()
    assert len(rows) == 1, "终态应自动写入 project_playbook_episode 锚点"

    # 删除 task：delete_task 应一并清理关联表（FK 级联清理）
    assert service.delete_task(session, t.id) is True
    session.commit()
    leftover = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.episode_id == t.id,
    ).all()
    assert leftover == [], (
        "delete_task 应清掉 project_playbook_episode 中的引用，否则后续删除撞 FK"
    )
