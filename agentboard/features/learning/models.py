"""学习域数据模型（Epic 140）。

切片 1 task_outcome：每个完成（done/blocked/withdrawn）任务的**结构化能力评分沉淀**。
- score: 0~1 复合分（本期 = L1/L2 过程指标公式；L3 LLM-judge 接入后加权）
- judge_json: 各维度明细（L1/L2 过程指标；L3 待 LLM judge 调度补齐，字段预留）

切片 3 Episode RAG + Playbook（Worker 持续学习，Story 268）：
- episode_embedding：每个完成任务的 run trace 向量化（零依赖 hash 向量 + numpy 可选加速），
  供新 task recall 相似经验注入 prompt。
- project_playbook：项目级 playbook 元数据（version / last_compressed_at /
  last_appended_episode_id）。**不再**存 content_md —— 8/17 review P1 #2 长期
  方案下，content_md 每次 get_playbook 时由 ``ProjectPlaybookEpisode`` entries
  实时渲染，彻底消除 ``content_md`` 字段 read-modify-write 的 lost update 风险。
- project_playbook_episode：每个 episode 一条 entry，存 ``(task_type, outcome,
  summary, weight)`` 等结构化字段。``(project_id, episode_id)`` 唯一约束继续作为
  DB 级幂等锚点（防同 episode 重复追加）。
"""
from datetime import datetime

from sqlalchemy import (
    CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...core.common.models import Base, utc_now


# Playbook episode outcome 枚举值（与 _playbook_entry 内部约定的 outcome 字符串
# 对齐；同时供 CheckConstraint 保护避免漂移到旧 `fail` 拼写）。
ALL_PLAYBOOK_OUTCOMES = ("success", "failure")


class TaskOutcome(Base):
    __tablename__ = "task_outcome"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="ck_task_outcome_score"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 一个 task 唯一一条 outcome（重复计算幂等 upsert）
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    # 明细 JSON：{"pass_first_try":1.0,"review_rounds":0,"attempts":1,
    #            "duration_s":3600,"blocked_count":0,"withdrawn":false,
    #            "judge_pending":true}
    judge_json: Mapped[str] = mapped_column(Text, default="{}")
    duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class EpisodeEmbedding(Base):
    """任务 run trace 的向量化快照（Story 268 切片 3）。

    - 每个 task 唯一一条 episode（episode_id == task_id，幂等 upsert）；
    - vector 存 JSON 文本（list[float]），兼容双后端（MariaDB TEXT / SQLite TEXT）；
    - summary 为人类可读的 episode 摘要（prompt 注入用）；
    - outcome 标记成功/失败（success / fail），recall 时成功 top-5 + 失败 top-3 分组注入。
    """

    __tablename__ = "episode_embedding"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id"), unique=True, index=True, comment="任务 id（一任务一条 episode）"
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(10), default="dev")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    outcome: Mapped[str] = mapped_column(String(10), default="success", comment="success/fail")
    vector: Mapped[str] = mapped_column(Text, comment="JSON list[float] 归一化向量")
    summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProjectPlaybook(Base):
    """项目级 Playbook 元数据（Story 268 切片 3 + 8/17 review P1 #2 长期方案 + 8/18 review P2）。

    **设计变更（8/17 review P1 #2 长期方案）**：
    - 旧版 ``content_md`` 字段被移除。content_md 之前同时承担「展示」
      和「存储」双重职责，导致 read-modify-write（多 session 并发追加
      content_md 时）必然产生 lost update——last writer 赢，pattern 静默
      丢失。修复用 with_for_update / 重设计表结构都是补丁，根本方案是
      **把 content_md 退化为派生数据**，每次读时实时从 entries 渲染。
    - ``version`` 字段保留为「已追加的 entry 数量」（entries 表行数）。
      旧版 version = 渲染后字符串的写入次数（可被 lost update 影响）；
      新版 version = entries 数量（**单调**与「新增 entry 次数」一一对应，
      DB 真相关于 entries INSERT 次数）。
    - ``last_appended_episode_id`` 保留为展示字段。

    **8/18 review P2 进一步收紧**：
    - ``ProjectPlaybook.version`` 字段不再在写路径维护（旧实现
      ``(pb.version or 0) + 1`` 是 read-modify-write，并发不同 episode
      同时追加会 lost update——version=11 但 entries=12）。
    - 读路径 ``memory.get_playbook`` 永远返回 ``version = len(entries)``，
      与「新增 entry 次数」一一对应、单调、无竞争。
    - ``ProjectPlaybook.version`` 列保留仅作向后兼容（默认 0），后续
      可通过单独 migration drop 掉。
    """

    __tablename__ = "project_playbook"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), unique=True, index=True
    )
    # 8/17 review P1 #2 长期方案：content_md 列已删除。content_md 在
    # ``get_playbook`` 时由 ProjectPlaybookEpisode entries 实时渲染，
    # 消除 read-modify-write 的 lost update 风险。
    version: Mapped[int] = mapped_column(Integer, default=0)
    last_compressed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 展示字段：「最近一次成功追加的 episode_id（= task_id）」。幂等判据已迁移到
    # ``ProjectPlaybookEpisode`` 表（migration e5f6a7b8c9d0）。
    last_appended_episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True, index=True,
        comment="最近一次成功追加的 episode_id（= task_id）；展示字段，幂等请查 project_playbook_episode",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ProjectPlaybookEpisode(Base):
    """Normalized playbook entry（8/17 review P1 #2 长期方案）。

    每个 entry 是 ``(project_id, episode_id)`` 唯一 + 完整结构化字段
    （``task_type`` / ``outcome`` / ``summary`` / ``weight``），**取代** 旧版
    ``ProjectPlaybook.content_md`` 字符串拼接。`content_md` 每次读时
    由 entries 表实时渲染（见 ``memory.get_playbook``），彻底消除并发
    read-modify-write 的 lost update 风险。

    幂等机制（继承自 migration e5f6a7b8c9d0，加强于 P1 #2 长期方案）：
    - **数据库级幂等**：`UNIQUE (project_id, episode_id)` 约束——同
      (project, episode) 重复 ``update_playbook`` 直接 IntegrityError，
      跨 session / 跨线程并发 DB 仲裁。
    - **结构化字段**：每条 entry 独立可查、可改、可删、可按
      ``task_type`` / ``outcome`` / ``weight`` 排序；不再耦合在
      字符串 markdown 里。
    - **无 read-modify-write**：entries 是 append-only INSERT；不存
      任何「最终聚合的字符串」字段，所以没有"读 A → 改 → 写回"
      的中间态竞争。
    - 给 Learning Router 用的 ``weight`` 字段（默认 1.0，未来可由
      judge score / outcome 动态调整）当前仅预留，未参与排序逻辑。

    旧表结构（复合主键，无 entry 字段）兼容：migration ``f1g2h3i4j5k6``
    给老表补 `id` 单 PK、加 entry 字段、把复合主键降级为 UniqueConstraint。
    """

    __tablename__ = "project_playbook_episode"
    __table_args__ = (
        # 8/17 review P1 #2：复合主键降级为 UniqueConstraint，腾出 id 单 PK
        # 让 entry 可独立 update / delete / 排序（按 id desc 取代 created_at
        # 排序更稳，不依赖时钟单调性）。
        UniqueConstraint(
            "project_id", "episode_id",
            name="uq_project_playbook_episode_project_episode",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure')",
            name="ck_project_playbook_episode_outcome",
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id"), index=True, nullable=False,
    )
    # episode_id nullable：弱幂等路径（手动整理 / 旧调用方不传 episode_id）
    # 走 entry 字段去重；同时 legacy 迁移的 playbook 没有对应 task 也允许 NULL。
    # 唯一约束 ``(project_id, episode_id)`` 兼容：NULL 不参与唯一性比较
    # （SQL 标准行为，SQLite / MariaDB 一致）。
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"), index=True, nullable=True,
        comment="episode（= task）id；同 (project, episode) 重复追加触发唯一冲突；nullable = 弱幂等 / legacy 路径",
    )
    # ===== Entry 结构化字段（8/17 review P1 #2 长期方案新增） =====
    task_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="dev",
        comment="entry 对应的 task_type（dev / bug / qa / design / legacy）",
    )
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success",
        comment="success / failure；驱动渲染时的 '成功 pattern' / '踩坑 pattern' 标记",
    )
    summary: Mapped[str] = mapped_column(
        Text, nullable=False, default="",
        comment="结构化摘要文本（取代旧版 markdown 字符串拼接）",
    )
    weight: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0,
        comment="Learning Router 排序权重；当前默认 1.0，未来可由 judge / outcome 动态调整",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utc_now, index=True,
        comment="首次成功追加的时间（去重后不变）",
    )
