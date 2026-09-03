"""PreparedExecution 公共装配（Review 2026-08-26 P1 修复）。

把 Behavior / Context / Prompt 三个 pipeline 集中在 dispatch 入口前完成，
产生不可变 ``PreparedExecution``，供 Coordinator / Worker 在调用 handler 之前
准备好 base prompt 与生效配置。

为何需要这层（review P1）：

- 原 Worker 主流程 ``Handler.load_context() → invoker.invoke(context) →
  全局 _prompt_builder → 旧 handler hardcode prompt`` 完全没调用
  ``BehaviorResolver`` / ``ExecutionContextBuilder`` / ``PromptBuilder``，
  导致新模块是 dead code、用户配置在 production 不生效。
- 这层把"behavior 解析 + context 装配 + prompt 渲染"集中起来，
  既能保证 handler 拿到的 prompt 真正反映 effective_behavior，
  又作为统一入口方便 metrics / log / future A/B testing。

设计原则：

1. **向后兼容**：可接受 ``db=None``（Worker 进程没 DB session 时走 ctx 兜底）
   标注 P1 follow-up：完整 DB-aware 版本需要 server 提供
   ``GET /api/agent-behavior/effective`` endpoint，让 Worker 用 HTTP
   拿到 server 端已解析的 EffectiveBehaviorConfig。

2. **不可变**``PreparedExecution``：handler 拿到后不应再改 prompt / behavior。
   Handler 仍可基于 ``prepared.command.context`` 追加自己的业务段。

3. **canonical WorkType**：legacy alias ``TASK_IMPLEMENT`` / ``TASK_REVIEW``
   在此处归一化成 ``IMPLEMENTATION`` / ``IMPLEMENTATION_REVIEW``，业务层不再判断 alias。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .behavior.context_builder import execution_context_builder
from .behavior.prompt_builder import prompt_builder
from .behavior.resolver import behavior_resolver
from .contract import ExecutionCommand, PreparedExecution, WorkType
from .learning.retriever import learning_retriever

log = logging.getLogger("agentboard.processors.prepared")


def prepare_execution(
    command: ExecutionCommand,
    *,
    db: Any = None,
    retriever: Any = None,
    client: Any = None,
) -> PreparedExecution:
    """集中完成 behavior / context / prompt 三层装配，返回不可变 PreparedExecution。

    Args:
        command: 原始 ExecutionCommand（其 work_type 可以是 legacy alias）
        db: 可选 SQLAlchemy Session。传入则走完整 DB-aware 解析路径；
             不传则走 ctx 兜底（system default + ctx 内嵌数据）。
        retriever: 可选 LearningRetriever；默认用全局 ``learning_retriever``。

    Returns:
        PreparedExecution：frozen model，handler / invoker 直接消费 ``.prompt``。
    """
    t0 = time.perf_counter()

    # 1. 归一化 work_type（TASK_IMPLEMENT → IMPLEMENTATION 等）
    canonical_wt = WorkType.canonical_for(command.work_type)
    normalized_command = command.model_copy(update={"work_type": canonical_wt})

    # 2. 解析 EffectiveBehaviorConfig
    # Phase 4 P1（2026-08-26 review）：Worker 进程无 DB session 时，调
    # server 端 ``GET /api/agent-behavior/effective`` 拿已合并的配置。
    # 优先级：db > client > system-default-ctx-fallback
    behavior = None
    if db is not None:
        behavior = behavior_resolver.resolve(
            project_id=normalized_command.context.get("project_id"),
            agent_id=normalized_command.context.get("agent_id"),
            work_type=canonical_wt,
            db=db,
        )
    elif client is not None:
        # Worker 路径：通过 HTTP 调 server 端 effective endpoint
        from .behavior.models import EffectiveBehaviorConfig as _EBC
        params: dict = {}
        pid = normalized_command.context.get("project_id")
        aid = normalized_command.context.get("agent_id")
        if pid is not None:
            params["project_id"] = pid
        if aid is not None:
            params["agent_id"] = aid
        if canonical_wt is not None:
            params["work_type"] = canonical_wt.value
        try:
            resp = client.request("GET", "/api/agent-behavior/effective", params=params)
            if resp.status_code in (200, 201):
                behavior = _EBC.model_validate(resp.json())
            else:
                log.warning(
                    "Worker 调 effective 端点失败（HTTP %s），退回 system default",
                    resp.status_code,
                )
        except Exception as e:
            log.warning("Worker 调 effective 端点异常，回退 system default：%s", e)
    if behavior is None:
        # Fallback：system default + ctx 内嵌数据（兼容没 client / endpoint 失败）
        behavior = behavior_resolver.resolve(
            project_id=normalized_command.context.get("project_id"),
            agent_id=normalized_command.context.get("agent_id"),
            work_type=canonical_wt,
            db=None,
        )

    # 3. 装配 ExecutionContext（DB 优先；无 DB 时用 ctx 兜底）
    execution_context = execution_context_builder.build(
        command=normalized_command,
        behavior=behavior,
        db=db,
    )

    # 4. 渲染最终 prompt
    #    PromptBuilder 不直接接 ExecutionContext，需要从 context 读 raw_context_summary + learnings
    learnings_payload = [l.model_dump() for l in execution_context.learnings]
    # Review 2026-08-26 P1 #1 修复（Learning 路径）：把 memory 三段也传到 prompt builder
    # —— Learnings（主）已经在 prompt_builder 的 learnings 参数里；
    # Playbook / Episodes 通过 context["playbook"] / context["episodes"]
    # 走 PromptBuilder 的内部 memory 渲染（详见 prompt_blocks）。
    # 这里为了兼容老 PromptBuilder API，把三段都塞到 context dict 里。
    prompt_ctx = {
        "raw_context_summary": execution_context.raw_context_summary,
        "playbook": [p.model_dump() for p in execution_context.playbook],
        "episodes": [e.model_dump() for e in execution_context.episodes],
    }
    base_prompt = prompt_builder.build(
        work_type=canonical_wt,
        behavior=behavior,
        context=prompt_ctx,
        learnings=learnings_payload,
    )

    # 5. 不可变包
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    prepared = PreparedExecution(
        command=normalized_command,
        work_type=canonical_wt,
        behavior=behavior,
        execution_context=execution_context,
        prompt=base_prompt,
        prepare_ms=elapsed_ms,
    )

    log.info(
        "prepare_execution ok [exec_id=%s, work_type=%s, canonical=%s, prepare_ms=%d, sources=%s]",
        command.execution_id,
        command.work_type,
        canonical_wt,
        elapsed_ms,
        behavior.sources,
    )
    return prepared


def build_prompt_for_command(command: ExecutionCommand, *, db: Any = None) -> str:
    """便利方法：只拿 prompt 字符串（供 invokers._prompt_builder 全局函数调用）。

    完整 PreparedExecution 信息见 ``prepare_execution``。
    """
    return prepare_execution(command, db=db).prompt
