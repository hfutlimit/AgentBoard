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
        return [
            {
                "episode_id": row.episode_id,
                "project_id": row.project_id,
                "task_type": row.task_type,
                "score": row.score,
                "outcome": row.outcome,
                "similarity": round(score, 4),
                "summary": (row.summary or "")[:1000],
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


def _playbook_entry(task_type: str, summary: str, outcome: str) -> str:
    marker = "成功 pattern" if outcome == "success" else "踩坑 pattern"
    return f"## {task_type}：{marker}\n{summary or '(无摘要)'}\n"


def update_playbook(s, *, project_id: int, task_type: str, summary: str,
                    outcome: str = "success",
                    episode_id: int | None = None) -> ProjectPlaybook | None:
    """按 episode outcome 追加 playbook pattern。

    幂等机制（自 migration e5f6a7b8c9d0 起，**强 → 弱两层**）：

    1. **强幂等（DB 唯一约束）**：若传入 ``episode_id``，先 ``INSERT`` 到
       ``ProjectPlaybookEpisode``（复合主键 = 幂等锚点）。INSERT 成功才追加
       playbook；唯一冲突 → 该 (project, episode) 已记录，跳过。

       修复 8/15 review P1：旧版只比较 ``last_appended_episode_id``，无法阻止
       "101 → 102 → 101" 这类非相邻重复，也防不住并发读旧值。

    2. **弱幂等（字符串兜底）**：未传 ``episode_id`` 时（手动整理 / 旧调用方），
       仍走 ``entry.strip() not in pb.content_md`` 兜底。

    调用方负责 commit。失败静默降级；DB 约束冲突不算"失败"，按幂等正常返回。
    """
    try:
        entry = _playbook_entry(task_type, summary, outcome)
        pb = s.execute(
            select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
        ).scalar_one_or_none()

        # ---- 强幂等：DB 唯一约束 ----
        # 两层防御：
        # (a) 显式 SELECT 预检：避免对已存在的 (project, episode) 调用 s.add()
        #     ——SQLAlchemy 会发 SAWarning（new instance conflicts with persistent），
        #     同时省掉一次 INSERT 往返。
        # (b) SAVEPOINT + IntegrityError：真正的 DB 仲裁，并发场景下另一 session
        #     在我们 SELECT 之后 INSERT 同一行时触发。
        if episode_id is not None:
            existing_ppe = s.execute(
                select(ProjectPlaybookEpisode).where(
                    ProjectPlaybookEpisode.project_id == project_id,
                    ProjectPlaybookEpisode.episode_id == episode_id,
                )
            ).scalar_one_or_none()
            if existing_ppe is not None:
                logger.debug(
                    "update_playbook project#%s 跳过：episode_id=%s 已记录（预检命中）",
                    project_id, episode_id,
                )
                # 重新查最新 pb 状态返回（避免 stale）
                pb_now = s.execute(
                    select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
                ).scalar_one_or_none()
                return pb_now

            try:
                with s.begin_nested():
                    s.add(ProjectPlaybookEpisode(
                        project_id=project_id,
                        episode_id=episode_id,
                    ))
                # flush 成功 → 全新 episode，按需 append。
            except IntegrityError:
                # 并发兜底：另一 session 在我们 SELECT 之后 INSERT 了同一行。
                logger.debug(
                    "update_playbook project#%s 跳过：episode_id=%s 并发冲突（DB 唯一约束命中）",
                    project_id, episode_id,
                )
                pb_now = s.execute(
                    select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
                ).scalar_one_or_none()
                return pb_now
        # 未传 episode_id 或 INSERT 成功 → 落到下面的内容追加逻辑。

        # ---- 弱幂等（兜底）：字符串包含判断 ----
        # 只在"无 episode_id"或"episode_id 是新的"两条路径上生效。
        content_changed = True
        if pb is not None and entry.strip() in pb.content_md:
            content_changed = False

        if pb is None:
            pb = ProjectPlaybook(
                project_id=project_id,
                content_md=entry,
                version=1,
                last_appended_episode_id=episode_id,
            )
            s.add(pb)
        elif content_changed:
            pb.content_md = (pb.content_md + "\n" + entry).strip() + "\n"
            pb.version = (pb.version or 1) + 1
            pb.last_appended_episode_id = episode_id
        else:
            # 弱幂等命中：内容已存在，仅同步 anchor 字段（迁移期间统一化）
            if episode_id is not None:
                pb.last_appended_episode_id = episode_id
        return pb
    except Exception:  # noqa: BLE001
        logger.warning("update_playbook project#%s failed（静默降级）", project_id, exc_info=True)
        return None


def get_playbook(s, *, project_id: int) -> dict:
    """读取项目 playbook（不存在返回空模板）。"""
    pb = s.execute(
        select(ProjectPlaybook).where(ProjectPlaybook.project_id == project_id)
    ).scalar_one_or_none()
    if pb is None:
        return {
            "project_id": project_id,
            "content_md": "",
            "version": 0,
            "episodes": 0,
            "last_compressed_at": None,
        }
    episodes = s.execute(
        select(EpisodeEmbedding).where(EpisodeEmbedding.project_id == project_id)
    ).scalars().all()
    return {
        "project_id": project_id,
        "content_md": pb.content_md,
        "version": pb.version,
        "episodes": len(episodes),
        "last_compressed_at": pb.last_compressed_at.isoformat() if pb.last_compressed_at else None,
    }
