"""Implementation Plan T6.6 · 共享记忆闭环 回归测试。

闭环的骨架早已存在（store_episode 幂等写入 / recall_episodes 项目内向量召回 /
Learning typed memory）—— T6.6 的真实增量是三件收紧：

1. **标 source owner**：注入 prompt 的每条记忆能回答「这条经验是谁干出来的」
   （episode 无 owner 列，经 episode_id=task_id join tasks.owner_user_id 溯源）；
2. **派发注入段组装**：build_dispatch_memory_section 一次调用即得
   section + sources，dispatch/prompt 组装方不再各自拼；
3. **记忆不爆炸**：单项目 episode 容量上限（超出删最旧）。

以及一条结构保证：记忆是**只读上下文**——注入内容不携带任何权限/策略字段，
「跨 owner 记忆不能提权」由结构保证而不是靠约定。

运行：
    PYTHONPATH=src/backend-fastapi python -m pytest tests/test_m6_memory_loop.py -q
"""
import itertools
import os
import sys
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "src", "backend-fastapi")
sys.path.insert(0, _BACKEND)

_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
os.environ.pop("AGENTBOARD_REQUIRE_AUTH", None)

for _m in list(sys.modules):
    if _m == "agentboard" or _m.startswith("agentboard."):
        del sys.modules[_m]

from agentboard import service  # noqa: E402
from agentboard.database import SessionLocal, init_db  # noqa: E402
from agentboard.features.learning import memory as learning_memory  # noqa: E402
from agentboard.features.learning.memory import (  # noqa: E402
    MAX_PROJECT_EPISODES, build_dispatch_memory_section, recall_episodes,
)

init_db()

_SEQ = itertools.count(1)


def _seed():
    n = next(_SEQ)
    with SessionLocal() as s:
        p = service.create_project(s, name=f"mem P{n}")
        owner = service.register_user(s, username=f"mem-owner{n}",
                                      password="password123")
        service.add_project_member(s, project_id=p.id, user_id=owner.id,
                                   role="owner")
        epic = service.create_epic(s, project_id=p.id, title=f"mem E{n}")
        st = service.create_story(s, epic_id=epic.id, title=f"mem S{n}",
                                  created_by_user_id=owner.id,
                                  create_default_tasks=False)
        s.commit()
        return {"pid": p.id, "owner": owner.id, "story": st.id}


def _make_task(s, d, title, spec, owner_id):
    t = service.create_task(s, project_id=d["pid"], story_id=d["story"],
                            title=title, description=spec, type="dev",
                            created_by_user_id=owner_id)
    t.spec = spec
    s.commit()
    return t


def test_episode_stamps_source_owner():
    """召回结果标 source owner：注入的记忆可审计（谁干出来的）。"""
    d = _seed()
    with SessionLocal() as s:
        t = _make_task(s, d, "mem T-a", "实现用户登录接口并写单测",
                       d["owner"])
        learning_memory.store_episode(s, t, score=0.9, outcome="success")
        s.commit()
        hits = recall_episodes(s, project_id=d["pid"],
                               task_spec="实现用户登录接口")
        assert hits, "同项目相似 spec 应能召回"
        assert hits[0]["source_owner_user_id"] == d["owner"]


def test_recall_never_leaks_other_projects():
    """项目边界：别的项目的相似 episode 不进召回结果。"""
    d = _seed()
    n = next(_SEQ)
    with SessionLocal() as s:
        p2 = service.create_project(s, name=f"mem P-other-{n}")
        owner2 = service.register_user(s, username=f"mem-o2-{n}",
                                       password="password123")
        epic2 = service.create_epic(s, project_id=p2.id, title="mem E2")
        st2 = service.create_story(s, epic_id=epic2.id, title="mem S2",
                                   created_by_user_id=owner2.id,
                                   create_default_tasks=False)
        t2 = service.create_task(s, project_id=p2.id, story_id=st2.id,
                                 title="mem T-other",
                                 description="实现用户登录接口并写单测",
                                 type="dev", created_by_user_id=owner2.id)
        t2.spec = "实现用户登录接口并写单测"
        s.commit()
        learning_memory.store_episode(s, t2, score=0.95, outcome="success")
        s.commit()
        # d 项目的召回不该带 p2 的 episode
        hits = recall_episodes(s, project_id=d["pid"],
                               task_spec="实现用户登录接口并写单测")
        assert all(h["project_id"] == d["pid"] for h in hits)


def test_dispatch_section_carries_source_annotation():
    """派发注入段：section 可拼 prompt，sources 带 owner 标注。"""
    d = _seed()
    with SessionLocal() as s:
        t = _make_task(s, d, "mem T-b", "配置 CI 流水线并接入覆盖率上报",
                       d["owner"])
        learning_memory.store_episode(s, t, score=0.8, outcome="success")
        t2 = _make_task(s, d, "mem T-c", "配置 CI 流水线缓存依赖加速构建",
                        d["owner"])
        s.commit()
        result = build_dispatch_memory_section(s, t2)
        assert result["count"] >= 1
        assert result["section"], "有可召回记忆时 section 非空"
        assert all("source_owner_user_id" in src for src in result["sources"])
        # 只读上下文：注入结构里没有权限/策略字段（跨 owner 记忆不能提权
        # 是结构保证，不是约定）
        assert all(set(src.keys()) <= {"episode_id", "source_owner_user_id"}
                   for src in result["sources"])


def test_memory_section_empty_when_no_history():
    d = _seed()
    with SessionLocal() as s:
        t = _make_task(s, d, "mem T-cold", "冷启动任务无任何历史", d["owner"])
        result = build_dispatch_memory_section(s, t)
        assert result["section"] == "" and result["count"] == 0


def test_project_episode_cap_prunes_oldest(monkeypatch):
    """记忆不爆炸：单项目 episode 超 cap 裁最旧（同项目内按 id 保新）。"""
    d = _seed()
    cap = 5
    # _prune_project_episodes 的 cap=None 时**运行时**读模块常量，
    # monkeypatch 常量即可生效
    monkeypatch.setattr(learning_memory, "MAX_PROJECT_EPISODES", cap)
    with SessionLocal() as s:
        ids = []
        for i in range(cap + 3):
            t = _make_task(s, d, f"mem T-cap-{i}", f"容量测试任务 {i} 号",
                           d["owner"])
            learning_memory.store_episode(s, t, score=0.5, outcome="success")
            ids.append(t.id)
        s.commit()
        remaining = s.query(learning_memory.EpisodeEmbedding.episode_id).filter(
            learning_memory.EpisodeEmbedding.project_id == d["pid"]
        ).all()
        remaining_ids = {r[0] for r in remaining}
        assert len(remaining_ids) == cap, f"应裁剪到 {cap} 条"
        # 保留的是**最新**的（id 大的）
        assert remaining_ids == set(ids[-cap:])


def test_store_episode_idempotent_per_task():
    """同 task 重跑复写同一行，不翻倍（既有语义，锁定防回退）。"""
    d = _seed()
    with SessionLocal() as s:
        t = _make_task(s, d, "mem T-idem", "幂等写入验证", d["owner"])
        learning_memory.store_episode(s, t, score=0.5)
        learning_memory.store_episode(s, t, score=0.9)
        s.commit()
        rows = s.query(learning_memory.EpisodeEmbedding).filter(
            learning_memory.EpisodeEmbedding.episode_id == t.id).all()
        assert len(rows) == 1
        assert rows[0].score == 0.9
