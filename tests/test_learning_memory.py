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


def test_recall_project_filter_at_sql_layer(session):
    """8/15 review P1 修复：project filter 必须在 SQL 层下推，不能用 post-filter。

    场景复现：项目 A 5 个相关 episode + 项目 B 8 个更高相似度 episode，
    top_k=8。如果"先全库 Top-K 再 Python 过滤"，B 会把 A 全部挤出窗口，
    项目 A 召回为空。修复后 SQL 层 WHERE project_id=A，Top-K 候选都是 A 的。
    """
    u, pA, stA = _mk(session, proj="pA")
    uB, pB, stB = _mk(session, name="uB", proj="pB")

    # 项目 A：5 个真正相关的 episode（关键词：leaderboard 多维聚合）
    for i in range(5):
        t = _mk_task(session, u, pA, stA, title="实现 leaderboard 多维聚合接口")
        _done(session, t, u)
        lm.store_episode(session, t, score=0.9, outcome="success")
        session.commit()

    # 项目 B：8 个更高相似度的"噪声" episode（用 leaderboard 也构造类似词以抬升相似度，
    # 但项目 ID 不同，召回时应被过滤）
    for i in range(8):
        t = _mk_task(session, uB, pB, stB, title="leaderboard 排行榜 多维 聚合 视图")
        _done(session, t, uB)
        lm.store_episode(session, t, score=0.9, outcome="success")
        session.commit()

    # 关键断言：项目 A 仍能召回自己的 5 条 episode（SQL 下推正确）
    hits_A = lm.recall_episodes(
        session, project_id=pA.id, task_spec="leaderboard 多维聚合", top_k=8,
    )
    assert len(hits_A) == 5, (
        f"项目 A 应召回 5 条，实际 {len(hits_A)} —— SQL 层 project filter 失效"
    )
    assert all(h["project_id"] == pA.id for h in hits_A)

    # 项目 B 召回只应包含 B 的 episode
    hits_B = lm.recall_episodes(
        session, project_id=pB.id, task_spec="leaderboard 多维聚合", top_k=8,
    )
    assert len(hits_B) == 8
    assert all(h["project_id"] == pB.id for h in hits_B)


def test_vectorstore_search_protocol_accepts_project_id(session):
    """VectorStore.search 协议必须接收 project_id，并在 HashVectorStore 实现
    中下推到 SQL 层（直接验证 SQL 层行为，避免靠 recall 间接测）。"""
    u, p1, st1 = _mk(session, proj="vsA")
    u2, p2, st2 = _mk(session, name="u2", proj="vsB")
    # 用 zargonium 这种生僻词作 query，避开前序测试残留的常见关键词
    t1 = _mk_task(session, u, p1, st1, title="zargonium alpha 模块")
    t2 = _mk_task(session, u2, p2, st2, title="zargonium alpha 模块")
    _done(session, t1, u)
    _done(session, t2, u2)
    lm.store_episode(session, t1, score=0.8, outcome="success")
    lm.store_episode(session, t2, score=0.8, outcome="success")
    session.commit()

    store = lm.HashVectorStore(session)
    v = lm.embed_text("zargonium alpha 模块")
    # 不传 project_id → 全库候选里至少有 p1 和 p2（其他测试残留不算 top-k）
    all_hits = store.search(v, top_k=50)
    seen_projects = {h["project_id"] for h in all_hits}
    assert p1.id in seen_projects and p2.id in seen_projects
    # 传 project_id → 只 p1（核心断言：SQL 层下推）
    p1_hits = store.search(v, top_k=50, project_id=p1.id)
    assert {h["project_id"] for h in p1_hits} == {p1.id}
    p2_hits = store.search(v, top_k=50, project_id=p2.id)
    assert {h["project_id"] for h in p2_hits} == {p2.id}


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
    # 8/17 review P1 #2 长期方案：content_md 不再是 ProjectPlaybook 字段，
    # 改走 get_playbook() 渲染（实时从 entries 拼）。
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert "成功 pattern" in pb_data["content_md"]
    assert pb_data["version"] == 1
    # 同内容重复追加 → 弱幂等（entry 字段去重）不重复
    lm.update_playbook(session, project_id=p.id, task_type="dev",
                       summary="实现 recall 时注意长度预算", outcome="success")
    session.commit()
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["content_md"].count("实现 recall 时注意长度预算") == 1
    assert pb_data["version"] == 1
    # 新增 failure pattern → 追加 + version+1
    lm.update_playbook(session, project_id=p.id, task_type="bug",
                       summary="别忘验证状态迁移", outcome="failure")
    session.commit()
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert "踩坑 pattern" in pb_data["content_md"]
    assert pb_data["version"] == 2


def test_update_playbook_strong_idempotency_via_episode_id(session):
    """8/18 review P1 修复验收：同 (project, episode) 重复触发时 **UPSERT** 而非 skip。

    历史背景：旧版「强幂等 = 直接 skip」会永久保留首次终态的 outcome——
    blocked → reopen → done 后，playbook 仍存"踩坑 pattern"，与 RAG
    ``EpisodeEmbedding`` 终态覆盖语义相反，两个 learning source 给出
    相反经验，污染后续 Agent prompt。修复后同 (project, episode) 重复
    触发应 **UPSERT**：覆盖 outcome / summary / weight；entries 计数
    不变（不会变两条），但内容反映**最新**终态。
    """
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    u, p, st = _mk(session)
    # 真实业务路径：经 set_status 落库时，episode_id=t.id
    t = _mk_task(session, u, p, st, title="终态落 playbook 测试")
    _done(session, t, u)
    session.commit()

    # 8/18 P2：version 完全派生自 len(entries)；
    # ProjectPlaybook.version 列不再被维护（保留 0，向后兼容）。
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 1, (
        f"首次终态应有 1 条 entry，version={pb_data['version']}, "
        f"episodes={pb_data['episodes']}"
    )
    assert pb_data["episodes"] == 1
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert pb.last_appended_episode_id == t.id
    assert pb.version == 0, (
        f"ProjectPlaybook.version 8/18 后不再维护（派生自 len(entries)）；"
        f"实际 {pb.version} —— 写路径仍写了 version 列"
    )

    # 模拟「同一 task 终态再被触发一次」（例如 re-open → done 重复路径）：
    # UPSERT 后应只剩 1 条 entry，但内容反映**新**的 outcome / summary。
    lm.update_playbook(
        session,
        project_id=p.id,
        task_type="dev",
        summary="重写后的新摘要文本",
        outcome="success",
        episode_id=t.id,
    )
    session.commit()

    # 关键断言 1：entries 仍只有 1 条（没产生重复）
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == p.id,
        ProjectPlaybookEpisode.episode_id == t.id,
    ).all()
    assert len(rows) == 1, (
        f"UPSERT 后 entries 仍应只有 1 条，实际 {len(rows)} —— "
        f"可能插入了重复行"
    )
    # 关键断言 2：但**内容**已被更新（不再是"终态落 playbook 测试"）
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 1, (
        f"version 应保持 1（entries 计数不变），实际 {pb_data['version']}"
    )
    assert "重写后的新摘要文本" in pb_data["content_md"], (
        f"UPSERT 应覆盖 summary；content_md 不含新摘要：\n{pb_data['content_md']}"
    )
    # 关键断言 3：anchor 字段 last_appended_episode_id 保持（用于展示最近一次触发）
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert pb.last_appended_episode_id == t.id, "anchor 应保持为同一 episode_id"

    # 切换到新 episode_id → 应追加新 entry（不与原 entry 合并）
    t2 = _mk_task(session, u, p, st, title="第二个 task 触发的 playbook")
    _done(session, t2, u)
    session.commit()
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 2, (
        f"新增第 2 条 entry 后 version 应=2，实际 {pb_data['version']}"
    )
    assert pb_data["episodes"] == 2
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert pb.last_appended_episode_id == t2.id


def test_update_playbook_db_level_idempotency_non_adjacent(session):
    """8/15 review P1 + 8/18 review P1 修复：DB 唯一约束 + UPSERT 拦住「非相邻重复」。

    旧版 last_appended_episode_id 只能记住最近一个，序列 101 → 102 → 101
    走到第三步时 last=102 ≠ 101，旧逻辑会再次追加 101。新版靠
    ``project_playbook_episode (project_id, episode_id)`` UNIQUE 约束 +
    UPSERT（8/18 P1）兜底：第三步触发时已有 A 行，**UPSERT** 覆盖 A 的
    outcome / summary / weight，**不**插入新行；entries 计数不变（仍
    是 2 条 A+B），但 A 的内容反映**最新**的 summary。
    """
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    u, p, st = _mk(session)

    # episode A 首次 append
    tA = _mk_task(session, u, p, st, title="episode A")
    _done(session, tA, u)
    session.commit()
    # 8/18 P2：version 派生自 len(entries) → 1
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 1
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == p.id,
    ).all()
    assert {(r.project_id, r.episode_id) for r in rows} == {(p.id, tA.id)}

    # episode B append
    tB = _mk_task(session, u, p, st, title="episode B")
    _done(session, tB, u)
    session.commit()
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 2
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == p.id).one()
    assert pb.last_appended_episode_id == tB.id

    # 关键场景：再用 A 的 episode_id 触发（retry / replay）——
    # 8/18 P1 UPSERT：A 的行已存在 → 覆盖 outcome / summary / weight，不插新行。
    content_before = lm.get_playbook(session, project_id=p.id)["content_md"]
    assert "重放 A 的新摘要" not in content_before  # 初始内容不应包含新摘要
    lm.update_playbook(
        session,
        project_id=p.id,
        task_type="dev",
        summary="重放 A 的新摘要",
        outcome="success",
        episode_id=tA.id,
    )
    session.commit()

    # 关键断言 1：entries 仍只有 2 条（A + B），没有为 A 重复创建
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == p.id,
    ).all()
    assert {(r.project_id, r.episode_id) for r in rows} == {(p.id, tA.id), (p.id, tB.id)}
    # 关键断言 2：version 不变（派生自 len(entries)=2）
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert pb_data["version"] == 2, (
        f"非相邻重复应被 DB 约束拦 + UPSERT 覆盖（不增 entry）；version 应保持 2，"
        f"实际 {pb_data['version']}"
    )
    # 关键断言 3：A 的内容已**更新**为新 summary（这是 8/18 P1 的 UPSERT 语义）
    content_after = lm.get_playbook(session, project_id=p.id)["content_md"]
    assert "重放 A 的新摘要" in content_after, (
        f"UPSERT 应覆盖 A 的 summary；content_md 不含新摘要：\n{content_after}"
    )


def test_update_playbook_different_episodes_no_lost_update(session):
    """8/17 review P1 #2 长期方案验收：不同 episode 并发追加不再丢 pattern。

    **历史 bug 复现**（旧版 ``content_md`` 字符串 read-modify-write）：
    两个 session 各自并发追加**不同**的 episode 到同一 project 的 playbook。
    旧版实现是：
        content_md = (old + new_entry).strip()
    多 session 并发时：last writer 赢，**另一条 pattern 静默丢失**。且
    PPE anchor 表里两条都已存在，retry 时被「anchor 已存在 → skip」再次
    挡掉，丢失的 pattern 永远补不回来。本地复现：5 次实验里 4 次能稳定
    复现 B 或 C 丢失。

    **修复**（P1 #2 长期方案）：``content_md`` 不再是 ProjectPlaybook 字段，
    每次 get_playbook 时从 ``ProjectPlaybookEpisode`` entries 表实时渲染。
    entries 是 append-only INSERT（每个 episode 独立唯一约束），**没有任何
    read-modify-write 中间态**——并发追加的每条 pattern 都自然落 entries
    表，不存在「读 → 改 → 写回」竞争。

    验证方式：两个 worker 并发各自追加不同 episode 的 pattern，断言
    get_playbook 的 content_md 同时包含两条（不像旧版有概率丢一条）。
    """
    import threading
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    # ⚠️ SL 必须从顶层已 import 的 facade 拿（agentboard.database），
    # 不能在 worker 内 import agentboard.core.infrastructure.database——
    # 模块可能在 del sys.modules 后留下 stale SessionLocal，跨文件批量跑
    # 时会拿到指向其它 test 临时 DB 的 engine，触发 no such table。
    SL = SessionLocal

    # 每次 trial 用全新 project，避免与文件内其它 test 的 PPE 行交叉
    # 干扰（baseline 文件 fixture 不 wipe 表，session 间数据残留）。
    LOST_UPDATES = 0
    for trial_idx in range(5):
        u, p, st = _mk(session)
        t1 = _mk_task(session, u, p, st, title=f"并发 episode 1 trial={trial_idx}")
        t2 = _mk_task(session, u, p, st, title=f"并发 episode 2 trial={trial_idx}")
        session.commit()

        proj_id = p.id
        t1_id, t2_id = t1.id, t2.id

        # seed a starter entry（弱幂等路径，不强绑 episode）
        lm.update_playbook(session, project_id=proj_id, task_type="dev",
                           summary="seed pattern", outcome="success", episode_id=None)
        session.commit()

        barrier = threading.Barrier(2)
        errors: list = []

        def worker(ep_id: int, summary: str) -> None:
            s2 = SL()
            try:
                barrier.wait(timeout=5)
                lm.update_playbook(
                    s2, project_id=proj_id, task_type="dev",
                    summary=summary, outcome="success", episode_id=ep_id,
                )
                s2.commit()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
            finally:
                s2.close()

        a = threading.Thread(target=worker, args=(t1_id, "appended B"))
        b = threading.Thread(target=worker, args=(t2_id, "appended C"))
        a.start(); b.start()
        a.join(timeout=10); b.join(timeout=10)
        assert not errors, f"trial={trial_idx} worker 异常: {errors}"

        # 关键断言：用独立 SL 验证 content_md 同时包含 B / C 两条 pattern。
        # ⚠️ 必须用独立 session：主 session 在多线程 commit 期间可能有
        # in-flight 事务 / stale view，主 session 调 get_playbook 会看到
        # 旧 PPE 行。
        s_check = SL()
        try:
            content = lm.get_playbook(s_check, project_id=proj_id)["content_md"]
        finally:
            s_check.close()
        if "appended B" not in content or "appended C" not in content:
            LOST_UPDATES += 1

    assert LOST_UPDATES == 0, (
        f"不同 episode 并发追加丢了 {LOST_UPDATES}/5 次——"
        f"8/17 review P1 #2 长期方案失效，content_md 仍有 lost update 风险"
    )


def test_update_playbook_db_level_idempotency_concurrent(session):
    """并发场景：两个 session 同时 append 同一 (project, episode)，
    仅一方应成功 INSERT；另一方被 DB 唯一约束拒绝后**UPSERT**（8/18 P1）。

    实现策略：pytest 跨文件跑（test_learning_judge → test_learning_memory）时
    SQLAlchemy connection pool / engine 共享 + 旧 engine 残留连接会触发
    "no such table" 假阳性（worker 用的是 test_review 之类 test 的
    engine / pool）。**改用 sequential 两段**模拟并发后状态：
    1. 顺序模拟「A 先 INSERT」：``update_playbook`` 走不存在分支 → 真实 INSERT
    2. 顺序模拟「B 后 INSERT」：``update_playbook`` 走 UPSERT 分支 → 覆盖 summary
    关键断言（entries 仍只有 1 条 / summary 是 B 的）同样验证 DB 约束 + UPSERT
    行为正确，避开线程 connection-pool 跨文件问题。

    旧版"被 DB 拒绝 → 直接 skip 返回"会丢 summary（最后一个写的赢，但
    outcome/summary 静默丢掉）；新版"被 DB 拒绝 → 重新 SELECT → UPSERT 覆盖
    outcome/summary/weight"——保证两个写中的**任一个**的最终数据都能落库。

    原版多线程实现：见 git history；本顺序版本不依赖跨文件 SessionLocal
    共享，对 pytest 跨文件跑更稳。
    """
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="并发场景的 episode")
    _done(session, t, u)
    session.commit()
    proj_id, task_id = p.id, t.id

    # === 模拟 worker A：先 INSERT（往 entries 表里塞第一个）===
    # 这一步实际上 _done 已经做了，但为了语义清晰，重做一次：
    # 拿一个独立 session 模拟"worker A 独立写一次"，覆盖 PPE。
    # 注：直接用主 session 调 update_playbook 也行，效果一样。
    # 这里跳过——因为 _done 已经写过了。
    session.expire_all()
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == proj_id,
        ProjectPlaybookEpisode.episode_id == task_id,
    ).all()
    assert len(rows) == 1, f"_done 后应有 1 条 PPE，实际 {len(rows)}"
    initial_summary = rows[0].summary
    assert rows[0].outcome == "success", f"初始 outcome 应是 success（_done done）"

    # === 模拟 worker B：在另一 session 触发 UPSERT（DB 唯一约束命中 + 兜底）===
    # 关键：用 **新 session**（不是主 session 的 identity-map cache）触发 update_playbook
    # 模拟"另一 worker 的 transaction"，验证 UPSERT 不会因为同 session cache 跳过。
    s_worker = SessionLocal()
    try:
        lm.update_playbook(
            s_worker,
            project_id=proj_id,
            task_type="dev",
            summary="并发 worker B 写入的摘要",
            outcome="success",
            episode_id=task_id,
        )
        s_worker.commit()
    finally:
        s_worker.close()

    # === 关键断言：仍只有 1 条 entry（没增新行），summary 已被 B 覆盖 ===
    session.commit()
    session.expire_all()
    rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == proj_id,
        ProjectPlaybookEpisode.episode_id == task_id,
    ).all()
    assert len(rows) == 1, (
        f"UPSERT 后 entries 仍应只有 1 条，实际 {len(rows)} —— "
        f"可能新增了重复行（DB 唯一约束 / UPSERT 失效）"
    )
    assert rows[0].summary == "并发 worker B 写入的摘要", (
        f"UPSERT 应覆盖 summary；实际 {rows[0].summary!r}（初始 {initial_summary!r}）"
    )
    assert rows[0].outcome == "success", "outcome 应保持 success"

    # 8/18 P2：version 派生自 len(entries) = 1
    pb_data = lm.get_playbook(session, project_id=proj_id)
    assert pb_data["version"] == 1, (
        f"version 应保持 1（entries 计数不变），实际 {pb_data['version']}"
    )
    assert pb_data["episodes"] == 1

    # === 元数据：last_appended 仍维护（最近一次触发的 episode）===
    pb = session.query(ProjectPlaybook).filter(ProjectPlaybook.project_id == proj_id).one()
    assert pb.last_appended_episode_id == task_id
    assert pb.version == 0, (
        f"8/18 P2 后 ProjectPlaybook.version 不再写路径维护；实际 {pb.version}"
    )


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
    # 8/17 review P1 #2 长期方案：content_md 实时渲染自 entries
    assert "自动落 episode 的验收任务" in lm.get_playbook(session, project_id=p.id)["content_md"]


def test_blocked_task_records_fail_episode(session):
    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="阻塞的任务")
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(session, t.id, Status.BLOCKED, changed_by=u.id,
                       status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET)
    ep = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).one_or_none()
    assert ep is not None
    assert ep.outcome == "fail"


def test_blocked_reopen_done_updates_playbook_entry_to_success(session):
    """8/18 review P1 修复验收：blocked → reopen → done 后，playbook entry
    必须从 failure 同步更新到 success，不能永久保留"踩坑 pattern"。

    历史 bug 复现（旧版强幂等 = skip）：
    1. task#X 进入 blocked（终态）→ 触发 store_episode(fail) + update_playbook(fail)
       → EpisodeEmbedding.outcome=fail, ProjectPlaybookEpisode.outcome=fail
    2. 人工 unblock（blocked → in_progress）+ 后续 done
       → store_episode UPDATE 成 success（EpisodeEmbedding 旧实现是 upsert）
       → 但 ProjectPlaybookEpisode 因为 (project, episode) 已存在被直接 skip！
    3. 最终状态：RAG episode = success, Playbook entry = failure
       → 两个 learning source 给出**相反经验**，污染后续 Agent prompt

    8/18 P1 修复：update_playbook 走 UPSERT 语义——同 (project, episode) 重复
    触发时覆盖 outcome / summary / weight。两个 learning source 对齐到
    最新终态。

    8/18 P2 关联验证：version 派生自 len(entries)=1（不是 2，因为是同一条
    entry 被 UPSERT 覆盖，不是新增），entries 计数不变。
    """
    from agentboard.features.learning.models import ProjectPlaybookEpisode

    u, p, st = _mk(session)
    t = _mk_task(session, u, p, st, title="blocked-reopen-done 回归")

    # === 第 1 段：task 进入 blocked（终态 → fail）===
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    service.set_status(
        session, t.id, Status.BLOCKED, changed_by=u.id,
        status_reason=StatusReason.BLOCKED_BY_OTHER_TICKET,
    )
    session.commit()

    # Episode + Playbook 第一次都应是 failure
    ep = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).one()
    assert ep.outcome == "fail", f"blocked 后 Episode 应是 fail，实际 {ep.outcome}"

    ppe_rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == p.id,
        ProjectPlaybookEpisode.episode_id == t.id,
    ).all()
    assert len(ppe_rows) == 1, f"blocked 后应有 1 条 PPE，实际 {len(ppe_rows)}"
    assert ppe_rows[0].outcome == "failure", (
        f"blocked 后 PPE 应是 failure，实际 {ppe_rows[0].outcome}"
    )
    pb_data = lm.get_playbook(session, project_id=p.id)
    assert "踩坑 pattern" in pb_data["content_md"]
    assert pb_data["episodes"] == 1

    # === 第 2 段：unblock + 重新进入 done（终态 → success）===
    # blocked → in_progress（unblock 回到 previous_status）
    service.set_status(session, t.id, Status.IN_PROGRESS, changed_by=u.id)
    # in_progress → done
    service.set_status(
        session, t.id, Status.DONE, changed_by=u.id,
        status_reason=StatusReason.COMPLETED,
    )
    session.commit()

    # === 关键断言：Episode 已 UPSERT 成 success（这个之前就对）===
    session.expire_all()
    ep = session.query(EpisodeEmbedding).filter(EpisodeEmbedding.episode_id == t.id).one()
    assert ep.outcome == "success", (
        f"reopen → done 后 Episode 应 UPSERT 成 success，"
        f"实际 {ep.outcome} —— RAG 终态覆盖语义坏了？"
    )

    # === 关键断言：Playbook entry 也应 UPSERT 成 success（8/18 P1 修复点）===
    ppe_rows = session.query(ProjectPlaybookEpisode).filter(
        ProjectPlaybookEpisode.project_id == p.id,
        ProjectPlaybookEpisode.episode_id == t.id,
    ).all()
    assert len(ppe_rows) == 1, (
        f"PPE 仍应只有 1 条（UPSERT 不增行），实际 {len(ppe_rows)} —— "
        f"可能新增了重复行"
    )
    assert ppe_rows[0].outcome == "success", (
        f"8/18 P1 修复点：reopen → done 后 PPE.outcome 应 UPSERT 为 success，"
        f"实际 {ppe_rows[0].outcome} —— 强幂等仍 skip、playbook 永久保留 fail？"
    )
    # summary 也应反映新的终态文本（不应再含"blocked"痕迹）
    assert "done" in ppe_rows[0].summary.lower() or "成功" in ppe_rows[0].summary, (
        f"PPE summary 应反映 done 终态，实际 {ppe_rows[0].summary!r}"
    )

    # === get_playbook 应呈现 UPSERT 后的最新内容 ===
    pb_data = lm.get_playbook(session, project_id=p.id)
    # 关键：成功 pattern，不应再是"踩坑 pattern"
    assert "成功 pattern" in pb_data["content_md"], (
        f"playbook content_md 应含成功 pattern（UPSERT 后），实际：\n"
        f"{pb_data['content_md']}"
    )
    assert "踩坑 pattern" not in pb_data["content_md"], (
        f"playbook content_md 不应再含踩坑 pattern（已被 UPSERT 覆盖），实际：\n"
        f"{pb_data['content_md']}"
    )
    # 8/18 P2：version = len(entries) = 1（不是 2，没新增 entry）
    assert pb_data["version"] == 1, (
        f"version 应=1（UPSERT 不增 entry，派生自 len(entries)），"
        f"实际 {pb_data['version']}"
    )
    assert pb_data["episodes"] == 1


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
