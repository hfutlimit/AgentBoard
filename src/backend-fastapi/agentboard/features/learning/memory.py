"""学习域切片 3：Episode RAG recall + Project Playbook（Story 268）。

三层记忆架构的第 1+2 层，零新增第三方依赖（纯 Python 哈希向量，numpy 可选加速）：

- ``embed_text``：token（中英文词/字 + bigram）→ 固定维度 signed-hash 稀疏向量 → L2 归一化。
- ``VectorStore`` 抽象：本期实现 ``HashVectorStore``（全量余弦扫描，episode 量级 <10k 足够）；
  生产可替换为 pgvector / sqlite-vec，接口不变。
- ``build_episode``：把 task 完整上下文（spec/评论/状态历史/评分）压成文本 + 摘要。
- ``store_episode``：task 终态时幂等 upsert episode（episode_id=task_id 唯一）。
- ``recall_episodes``：query 向量与项目内 episodes 余弦 top-k，成功/失败分组返回。
- ``update_playbook``：按 episode outcome 追加「成功 pattern / 踩坑 pattern」。
- ``get_playbook``：读取项目 playbook（Worker prompt 注入用）。

容错原则（Story 268 验收）：recall/playbook 任何失败都必须静默降级为「不带记忆」，
绝不阻断既有派单链路。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Protocol

from sqlalchemy import select
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError

from .models import EpisodeEmbedding, ProjectPlaybook, ProjectPlaybookEpisode

logger = logging.getLogger(__name__)

# 向量维度：256 维 signed-hash 对 <10k 条 episode 足够区分；维度再高收益递减
VECTOR_DIM = 256
# 默认召回：成功 top-5 + 失败 top-3（Story 268 验收标准）
DEFAULT_SUCCESS_K = 5
DEFAULT_FAIL_K = 3
# 注入 prompt 的长度预算（字符），超限截断（先 recall 再注入最重要的 top-k）
RECALL_PROMPT_CHARS = 4000

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")
_BIGRAM_RE = re.compile(r"[\u4e00-\u9fff]{2}")


def _tokens(text: str) -> list[str]:
    """中文按字 + 英文按词 + 中文相邻二字 bigram（补充词序信息）。"""
    text = (text or "").lower()
    words = _TOKEN_RE.findall(text)
    han = "".join(w for w in words if w and w[0] >= "\u4e00")
    bigrams = _BIGRAM_RE.findall(han)
    return words + bigrams


def embed_text(text: str, dim: int = VECTOR_DIM) -> list[float]:
    """token → signed-hash 稀疏向量 → L2 归一化（确定性，零依赖）。

    signed-hash：token 哈希取模映射到维度索引，符号位来自哈希次高位，
    避免不同 token 同索引同向叠加造成碰撞污染。
    """
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> (8 * 8)) & 1 else -1.0
        vec[idx] += sign
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [round(v / norm, 6) for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class VectorStore(Protocol):
    """向量存储抽象（预留 pgvector / sqlite-vec 替换）。

    search 接收 ``project_id`` 用于在查询层做 project 收敛；传入后实现必须
    把过滤下推到 SQL / 索引层，**不要**先全库 Top-K 再用 Python 过滤，否则
    跨项目高相似度 episode 会把本项目的结果挤出 top_k 窗口。
    """

    def add(self, episode_id: int, vector: list[float], meta: dict) -> None: ...
    def search(
        self, vector: list[float], top_k: int, *, project_id: int | None = None,
    ) -> list[dict]: ...


class HashVectorStore:
    """纯 Python 全量余弦扫描实现（episode 量级 <10k 时 50ms 内，Story 268 验收）。

    数据直接从 DB 读（不复制内存索引），recall 是只读查询，天然支持多进程。

    ``project_id`` 在 SQL ``WHERE`` 中下推，确保 Top-K 候选本身就是项目内的。
    """

    def __init__(self, s):
        self._s = s

    def search(
        self, vector: list[float], top_k: int, *, project_id: int | None = None,
    ) -> list[dict]:
        stmt = select(EpisodeEmbedding)
        if project_id is not None:
            # 关键：先按 project 收敛，再全量余弦扫描，最后才截 top_k。
            # 修 8/15 review P1：避免跨项目高相似度 episode 抢走本项目结果。
            stmt = stmt.where(EpisodeEmbedding.project_id == project_id)
        rows = self._s.execute(stmt).scalars().all()
        scored: list[tuple[float, EpisodeEmbedding]] = []
        for row in rows:
            try:
                vec = json.loads(row.vector or "[]")
            except (TypeError, ValueError):
                continue
            score = cosine_similarity(vector, vec)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        # T6.6：episode 标 source owner —— episode 本身没有 owner 列，
        # 通过 episode_id(=task_id) join tasks.owner_user_id 溯源。注入到
        # prompt 的记忆段必须能回答「这条经验是谁干出来的」，跨 owner 共享
        # 记忆才可审计（但注入的只是文本上下文，不携带任何权限语义）。
        from ..work_items.models import Task as _Task
        task_ids = [row.episode_id for _, row in scored[:top_k]]
        owner_map: dict[int, int | None] = {}
        if task_ids:
            for tid, oid in self._s.execute(
                select(_Task.id, _Task.owner_user_id)
                .where(_Task.id.in_(task_ids))
            ).all():
                owner_map[tid] = oid
        return [
            {
                "episode_id": row.episode_id,
                "project_id": row.project_id,
                "task_type": row.task_type,
                "score": row.score,
                "outcome": row.outcome,
                "similarity": round(score, 4),
                "summary": (row.summary or "")[:1000],
                "source_owner_user_id": owner_map.get(row.episode_id),
            }
            for score, row in scored[:top_k]
        ]


# ---------------------------------------------------------------------------
# Episode builder / store
# ---------------------------------------------------------------------------


def build_episode_text(s, task) -> tuple[str, str]:
    """把 task 完整上下文压成 (检索文本, 摘要)。

    检索文本用于向量化（词多、信息全）；摘要用于注入 prompt（人类可读、紧凑）。
    """
    from ..work_items.models import Comment, TaskStatusHistory

    spec = (task.spec or "").strip() or (task.description or "").strip()
    transitions_rows = (
        s.execute(
            select(TaskStatusHistory.from_status, TaskStatusHistory.to_status)
            .where(TaskStatusHistory.task_id == task.id)
            .order_by(TaskStatusHistory.id)
        ).all()
    )
    transitions = [f"{f}->{t}" for f, t in transitions_rows]

    comments_rows = (
        s.execute(
            select(Comment.content).where(Comment.task_id == task.id).order_by(Comment.id)
        ).all()
    )
    comments = [c[0] for c in comments_rows]

    text_parts = [
        f"task:{task.title or ''}",
        f"type:{task.type or 'dev'}",
        f"status:{task.status or ''}",
        f"reason:{task.status_reason or ''}",
        f"spec:{spec}",
        f"transitions:{' '.join(transitions)}",
        f"comments:{' '.join(comments)[:3000]}",
    ]
    search_text = "\n".join(text_parts)

    summary = (
        f"[{task.type or 'dev'}] {task.title or ''}"
        f" → {task.status or ''}"
        + (f"（{task.status_reason}）" if task.status_reason else "")
        + (f"\nspec: {spec[:400]}" if spec else "")
        + (f"\n评论摘要: {' '.join(comments)[:300]}" if comments else "")
    )
    return search_text, summary


def store_episode(s, task, *, score: float = 0.0, outcome: str = "success") -> EpisodeEmbedding | None:
    """task 终态时幂等 upsert episode（episode_id=task_id 唯一）。

    任何异常静默降级（日志 warning），绝不阻断状态流转。
    """
    try:
        # 先 flush：SessionLocal 是 autoflush=False，同一事务里第二次调用
        # 的存在性检查看不见第一次 add 的 pending 行 → 重复 INSERT 撞
        # UNIQUE（实测踩过）。显式 flush 让幂等 upsert 在任何调用模式下成立。
        s.flush()
        search_text, summary = build_episode_text(s, task)
        vector = embed_text(search_text)
        existing = s.execute(
            select(EpisodeEmbedding).where(EpisodeEmbedding.episode_id == task.id)
        ).scalar_one_or_none()
        if existing is None:
            existing = EpisodeEmbedding(
                episode_id=task.id,
                project_id=task.project_id,
                task_type=task.type or "dev",
                score=float(score or 0.0),
                outcome=outcome,
                vector=json.dumps(vector, ensure_ascii=False),
                summary=summary,
            )
            s.add(existing)
        else:
            existing.vector = json.dumps(vector, ensure_ascii=False)
            existing.summary = summary
            existing.score = float(score or 0.0)
            existing.outcome = outcome
        return existing
    except Exception:  # noqa: BLE001 —— 记忆是增强数据，失败不影响主流程
        logger.warning("store_episode task#%s failed（静默降级）", getattr(task, "id", "?"), exc_info=True)
        return None
    finally:
        # T6.6 容量护栏：单项目 episode 上限，超出删最旧（同分近似随 id）。
        # 记忆的价值密度随时间衰减，旧低分 episode 留着只会推高召回噪声。
        try:
            _prune_project_episodes(s, task.project_id)
        except Exception:  # noqa: BLE001
            logger.warning("prune_project_episodes project#%s failed",
                           getattr(task, "project_id", "?"), exc_info=True)


#: 单项目 episode 容量上限（T6.6「记忆不爆炸」）。project 内任务完成数
#: 长期超过此值时，recall 的向量扫描成本与噪声都会线性变差。
MAX_PROJECT_EPISODES = 500


def _prune_project_episodes(s, project_id: int, cap: int | None = None) -> int:
    """裁剪单项目 episode 到 cap：保留最新（id 大）优先，超出的删最旧。

    ``cap=None`` 时读模块常量 ``MAX_PROJECT_EPISODES`` —— 运行时读而不是
    默认参数绑定，测试/运维 monkeypatch 常量才能生效。
    """
    cap = cap or MAX_PROJECT_EPISODES
    # flush 让本事务刚 add 的 pending 行进入计数（SessionLocal 是
    # autoflush=False，select 看不见 pending），否则裁剪慢一拍、稳态多留 1 行。
    s.flush()
    ids = [
        r[0] for r in s.execute(
            select(EpisodeEmbedding.id)
            .where(EpisodeEmbedding.project_id == project_id)
            .order_by(EpisodeEmbedding.id.desc())
        ).all()
    ]
    excess = ids[cap:]
    if not excess:
        return 0
    s.execute(
        sa_delete(EpisodeEmbedding).where(EpisodeEmbedding.id.in_(excess))
    )
    logger.info("prune: project#%s 裁剪 %s 条最旧 episode（cap=%s）",
                project_id, len(excess), cap)
    return len(excess)


def build_dispatch_memory_section(s, task) -> dict:
    """T6.6 派发记忆注入段：dispatch/prompt 组装方一次调用即得。

    返回 ``{"section": str, "sources": [...], "count": int}``：
    - ``section`` 可直接拼进 prompt（空字符串 = 项目暂无可注入记忆）；
    - ``sources`` 每条记忆的 source owner 标注（谁干出来的，可审计）；
    - 记忆是**只读上下文**：不携带任何权限/策略字段，执行门与 Action Policy
      不受注入内容影响 —— 「跨 owner 记忆不能提权」的结构保证。
    失败静默降级为空段（记忆是增强数据，不是关键路径）。
    """
    try:
        hits = recall_episodes(
            s, project_id=task.project_id,
            task_spec=(task.spec or task.description or ""),
        )
        return {
            "section": build_recall_section(hits),
            "sources": [
                {"episode_id": h["episode_id"],
                 "source_owner_user_id": h.get("source_owner_user_id")}
                for h in hits
            ],
            "count": len(hits),
        }
    except Exception:  # noqa: BLE001
        logger.warning("build_dispatch_memory_section task#%s failed",
                       getattr(task, "id", "?"), exc_info=True)
        return {"section": "", "sources": [], "count": 0}


# ---------------------------------------------------------------------------
# Recall
# ---------------------------------------------------------------------------


def recall_episodes(
    s,
    *,
    project_id: int,
    task_spec: str,
    top_k: int = DEFAULT_SUCCESS_K + DEFAULT_FAIL_K,
) -> list[dict]:
    """query 向量与项目内 episodes 余弦 top-k（成功优先，失败补位）。

    返回已按相似度降序的 episodes；失败时返回 []（调用方 fallback 不带记忆）。

    注意：``project_id`` 已经下推到 ``VectorStore.search()`` 的 SQL 层，
    不要再在 Python 侧二次过滤——否则跨项目高相似度 episode 会把本项目
    的结果挤出 top_k 窗口（见 8/15 review P1）。
    """
    if not task_spec or not str(task_spec).strip():
        return []
    try:
        vector = embed_text(task_spec)
        store = HashVectorStore(s)
        hits = store.search(vector, top_k=top_k, project_id=project_id)
        # 排序：success 优先于 fail，再按相似度
        order = {"success": 0, "fail": 1}
        hits.sort(key=lambda h: (order.get(h["outcome"], 2), -h["similarity"]))
        return hits[:top_k]
    except Exception:  # noqa: BLE001 —— recall 失败 fallback 不带记忆
        logger.warning("recall_episodes project#%s failed（fallback 无记忆）", project_id, exc_info=True)
        return []


def build_recall_section(episodes: list[dict]) -> str:
    """把 recall 结果格式化为 prompt 注入段（长度预算内）。"""
    if not episodes:
        return ""
    lines = ["", "## 项目历史经验（RAG recall，参考案例）"]
    for ep in episodes:
        marker = "✅ 成功" if ep["outcome"] == "success" else "❌ 失败"
        lines.append(
            f"- [{marker} sim={ep['similarity']}] #{ep['episode_id']} "
            f"({ep['task_type']}, score={ep['score']}) {ep['summary'][:280]}"
        )
    section = "\n".join(lines)
    return section[:RECALL_PROMPT_CHARS]


# ---------------------------------------------------------------------------
# Playbook
# ---------------------------------------------------------------------------


def _render_entry_md(task_type: str, summary: str, outcome: str) -> str:
    """把单条 entry 渲染为 markdown 段（get_playbook 实时拼接用）。"""
    marker = "成功 pattern" if outcome == "success" else "踩坑 pattern"
    return f"## {task_type}：{marker}\n{summary or '(无摘要)'}\n"


def _normalize_outcome(outcome: str) -> str:
    """兼容旧拼写：'fail' / 'failed' → 'failure'。

    8/17 review P1 #2 长期方案：outcome 字段的 CheckConstraint 收紧为
    ('success', 'failure')，与 ALL_PLAYBOOK_OUTCOMES 对齐；旧版
    _playbook_entry 内部约定是 'fail'，新接口改成 'failure'（更清晰、
    与 judge / outcome 字段语义一致）。这里做一次兼容转换，避免存量
    调用方直接报错。
    """
    if outcome in ("failure", "success"):
        return outcome
    if outcome in ("fail", "failed"):
        return "failure"
    raise InvalidValue(
        f"invalid outcome '{outcome}', must be one of 'success' / 'failure'"
    )


def update_playbook(s, *, project_id: int, task_type: str, summary: str,
                    outcome: str = "success",
                    episode_id: int | None = None,
                    weight: float = 1.0) -> ProjectPlaybook | None:
    """按 episode outcome 写入 playbook pattern（8/17 review P1 #2 长期方案 + 8/18 review）。

    数据模型变更：旧版把 pattern 拼到 ``ProjectPlaybook.content_md`` 字符串
    末尾（read-modify-write，并发必然 lost update）。新版直接 ``INSERT`` 一
    条 ``ProjectPlaybookEpisode`` entry，content_md **不再**存储，
    每次 ``get_playbook`` 时由 entries 实时渲染。

    幂等 / UPSERT 机制：

    1. **传 ``episode_id``**（业务主路径：终态任务经 ``set_status`` 触发）：
       与 ``EpisodeEmbedding`` 对齐（episode_id=task_id 唯一）—— 同 (project,
       episode) 重复触发时 **UPSERT**（覆盖 outcome / summary / weight）。
       这是 8/18 review 修的关键语义：blocked → reopen → done 后，playbook
       entry 必须从 failure 同步更新为 success，不能永久保留"踩坑 pattern"
       污染后续 Agent prompt。RAG 与 Playbook 同一 episode 的两条 learning
       数据，从此保持一致。
    2. **不传 ``episode_id``**（手动整理 / 旧调用方）：弱幂等，按
       (project_id, task_type, outcome, summary) 字段精确匹配已存在 entry
       跳过；**不**再用 markdown 字符串包含（race condition 太多）。

    调用方负责 commit。失败静默降级；DB 约束冲突不算"失败"，按幂等正常返回。

    ⚠️ 不再有任何「读 A → 改 → 写回 content_md」的中间态：
    entries 表是 append-only / upsert-by-episode，并发 lost update 风险被消除。

    ⚠️ 8/18 review P2：``ProjectPlaybook.version`` **不再维护**。该字段语义
    本质是「已追加 entry 数」，与 ``len(entries)`` 1:1 对应；之前用
    ``pb.version = (pb.version or 0) + 1`` 是 read-modify-write，并发下
    lost update → version=11 但 entries=12 的可漂移状态。修复：``get_playbook``
    永远返回 ``version = len(entries)``，写路径不再写 version 字段。
    """
    try:
        outcome = _normalize_outcome(outcome)

        # ---- 强路径：传 episode_id，UPSERT 语义 ----
        if episode_id is not None:
            existing_ppe = s.execute(
                select(ProjectPlaybookEpisode).where(
                    ProjectPlaybookEpisode.project_id == project_id,
                    ProjectPlaybookEpisode.episode_id == episode_id,
                )
            ).scalar_one_or_none()
            if existing_ppe is not None:
                # 8/18 review P1：UPSERT，覆盖 outcome / summary / weight。
                # 与 EpisodeEmbedding episode_id=task_id 唯一 + 终态覆盖语义对齐。
                # blocked → reopen → done 后，playbook entry 从 failure 同步
                # 变成 success（防"两个 learning source 给出相反经验"）。
                existing_ppe.task_type = task_type
                existing_ppe.outcome = outcome
                existing_ppe.summary = summary
                existing_ppe.weight = weight
            else:
                try:
                    with s.begin_nested():
                        s.add(ProjectPlaybookEpisode(
                            project_id=project_id,
                            episode_id=episode_id,
                            task_type=task_type,
                            outcome=outcome,
                            summary=summary,
                            weight=weight,
                        ))
                        s.flush()
                except IntegrityError:
                    # 并发兜底：另一 session 在我们 SELECT 之后 INSERT 了同一行。
                    # 重新 SELECT 并 UPSERT（与 EpisodeEmbedding 终态覆盖语义一致）。
                    logger.debug(
                        "update_playbook project#%s UPSERT 兜底：episode_id=%s 并发冲突后重 SELECT",
                        project_id, episode_id,
                    )
                    existing_ppe = s.execute(
                        select(ProjectPlaybookEpisode).where(
                            ProjectPlaybookEpisode.project_id == project_id,
                            ProjectPlaybookEpisode.episode_id == episode_id,
                        )
                    ).scalar_one()
                    existing_ppe.task_type = task_type
                    existing_ppe.outcome = outcome
                    existing_ppe.summary = summary
                    existing_ppe.weight = weight
        else:
            # ---- 弱路径：未传 episode_id，弱幂等跳过 ----
            # 精确匹配 (task_type, outcome, summary)，命中则跳过。
            existing = s.execute(
                select(ProjectPlaybookEpisode).where(
                    ProjectPlaybookEpisode.project_id == project_id,
                    ProjectPlaybookEpisode.task_type == task_type,
                    ProjectPlaybookEpisode.outcome == outcome,
                    ProjectPlaybookEpisode.summary == summary,
                )
            ).scalar_one_or_none()
            if existing is not None:
                logger.debug(
                    "update_playbook project#%s 跳过：同 (task_type, outcome, summary) entry 已存在（弱幂等）",
                    project_id,
                )
                pb_now = s.execute(
                    select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
                ).scalar_one_or_none()
                return pb_now

            # 全新 entry，INSERT
            s.add(ProjectPlaybookEpisode(
                project_id=project_id,
                episode_id=None,  # 弱幂等路径不强绑 episode
                task_type=task_type,
                outcome=outcome,
                summary=summary,
                weight=weight,
            ))
            s.flush()

        # ---- 维护 ProjectPlaybook 元数据（仅 last_appended；version 派生自 entries） ----
        # 8/18 review P2：version 字段不再 read-modify-write，``get_playbook`` 用
        # ``len(entries)`` 派生（条目数量就是 source of truth）。这里只更新
        # ``last_appended_episode_id`` 作为展示字段（最近一次触发的 episode）。
        #
        # ⚠️ 8/17 review P1 #2 长期方案发现：``ProjectPlaybook.project_id``
        # UNIQUE 在并发 upsert 时也会触发 IntegrityError（两个 session 都
        # SELECT 不到对方 in-flight 的 pb 记录，于是都尝试 INSERT）。
        # 解决：包 SAVEPOINT 兜底，IntegrityError 后 re-SELECT 拿现态。
        pb = s.execute(
            select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
        ).scalar_one_or_none()
        if pb is None:
            try:
                with s.begin_nested():
                    pb = ProjectPlaybook(
                        project_id=project_id,
                        version=0,  # 派生字段，初始化 0；get_playbook 永远返回 len(entries)
                        last_appended_episode_id=episode_id,
                    )
                    s.add(pb)
                    s.flush()
            except IntegrityError:
                # 并发：另一 session 在我们 SELECT 之后 INSERT 了同一 project 的 pb
                pb = s.execute(
                    select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
                ).scalar_one()
                if episode_id is not None:
                    pb.last_appended_episode_id = episode_id
        else:
            # 不再 pb.version += 1；version 在 get_playbook 派生自 len(entries)。
            if episode_id is not None:
                pb.last_appended_episode_id = episode_id
        return pb
    except Exception:  # noqa: BLE001
        logger.warning("update_playbook project#%s failed（静默降级）", project_id, exc_info=True)
        return None


def get_playbook(s, *, project_id: int) -> dict:
    """读取项目 playbook（8/17 review P1 #2 长期方案 + 8/18 review P2）。

    content_md 字段在响应里**仍然存在**（保留 API 契约），但**不再存
    数据库**——每次读时从 ``ProjectPlaybookEpisode`` entries 表
    按 ``id ASC`` 顺序拼出。这样彻底消除旧版 ``content_md`` 字符串
    read-modify-write 的并发 lost update 风险。

    8/18 review P2：``version`` 字段**完全派生自 ``len(entries)``**——
    旧版 ``ProjectPlaybook.version`` 写路径用 ``(pb.version or 0) + 1``
    read-modify-write，两个不同 episode 并发追加时会 lost update
    （version=11 但 entries=12 的可漂移状态）。现在 ``update_playbook``
    不再写 version 列；本函数直接按 ``len(entries)`` 计算，与「新增
    entry 次数」一一对应、单调、无竞争。``ProjectPlaybook.version`` 列
    保留仅作向后兼容（默认 0，不再被读路径使用）。

    返回字典字段：
    - ``project_id``: int
    - ``content_md``: str（实时渲染自 entries）
    - ``version``: int（== len(entries)；与「已追加 entry 数」一一对应）
    - ``episodes``: int（entries 数量 = playbook 经验数）
    - ``last_compressed_at``: str | None（ISO 格式；ProjectPlaybook 元数据）
    """
    entries = s.execute(
        select(ProjectPlaybookEpisode)
        .where(ProjectPlaybookEpisode.project_id == project_id)
        .order_by(ProjectPlaybookEpisode.id.asc())
    ).scalars().all()

    pb = s.execute(
        select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
    ).scalar_one_or_none()

    if not entries:
        # 无 entry：返回空模板（保留 API 契约）
        return {
            "project_id": project_id,
            "content_md": "",
            "version": 0,  # 8/18 P2：派生自 len(entries)，空时为 0
            "episodes": 0,
            "last_compressed_at": pb.last_compressed_at.isoformat() if pb and pb.last_compressed_at else None,
        }

    # 实时渲染 entries → content_md
    parts = [_render_entry_md(e.task_type, e.summary, e.outcome) for e in entries]
    content_md = "\n".join(parts).strip() + "\n"

    return {
        "project_id": project_id,
        "content_md": content_md,
        # 8/18 P2：version 完全派生自 len(entries)；不再读 ProjectPlaybook.version
        # （DB 列保留兼容，但写路径不再维护它）。
        "version": len(entries),
        "episodes": len(entries),
        "last_compressed_at": pb.last_compressed_at.isoformat() if pb and pb.last_compressed_at else None,
    }
