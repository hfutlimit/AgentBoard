"""Review 2026-08-26 round 5 P1 验收测试（Learning / Memory 全链路）。

覆盖：
- P1 #1: 新 Learning 真正成为 runtime 主 memory 源（StoryHandler 不再 hardcode
        旧 episode recall；PreparedExecution 路径统一拉 Learnings/Playbook/Episodes 三段）
- P1/P2 #2: LearningExtractor 真正调 Agent 做结构化 reflection（heuristic 作为 fallback）
- P2 #3: taxonomy 命名（_record_learning_outcome alias 保留 + 新名 _record_task_outcome_and_memory）
"""
import os
import sys
import inspect
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "src" / "backend-fastapi"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 必须在 import engine 之前设置
DB_PATH = "_test_learning_p1_tmp.db"
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///./{DB_PATH}"

import pytest
from agentboard.core.infrastructure.database import SessionLocal, engine, init_db


@pytest.fixture(scope="module", autouse=True)
def _init_db():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass
    init_db()
    yield
    engine.dispose()
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except OSError:
            pass


# ============================================================
# P1 #1: 新 Learning 真正成为 runtime 主 memory 源
# ============================================================

def test_p1_story_handler_no_longer_hardcodes_old_episode_recall():
    """P1 #1: StoryHandler 不再 hardcode 调 _recall_episodes → /api/learning/recall。

    原 bug：StoryHandler.load_context / build_task_context 各自调
    ``_recall_episodes(project_id, ctx)`` 走旧 Episode RAG 路径，
    绕过 PreparedExecution 路径（即使用户关 load_memory 也会召旧 episode）。
    修法：移除两处 hardcode _recall_episodes 调用，memory retrieval 全部走
    ContextBuilder._resolve_learnings / _resolve_playbook_and_episodes。
    """
    from agentboard.agent_runtime.handlers import story as story_handler
    src = inspect.getsource(story_handler)

    # 1. 关键：load_context 内不再调 _recall_episodes
    load_context_src = src[src.find("def load_context"):]
    # 找 load_context 区块
    next_def = load_context_src.find("\n    def ", 10)
    if next_def < 0:
        next_def = len(load_context_src)
    load_block = load_context_src[:next_def]
    assert "self._recall_episodes" not in load_block, \
        "load_context 仍 hardcode 调 _recall_episodes（应改走 PreparedExecution 路径）"

    # 2. 关键：build_task_context 也不调
    build_task_context_src = src[src.find("def build_task_context"):]
    next_def = build_task_context_src.find("\n    def ", 10)
    if next_def < 0:
        next_def = len(build_task_context_src)
    build_block = build_task_context_src[:next_def]
    assert "self._recall_episodes" not in build_block, \
        "build_task_context 仍 hardcode 调 _recall_episodes（应改走 PreparedExecution 路径）"

    # 3. 关键：ctx["recalled"] 字段不再被注入（统一用 episodes / learnings / playbook）
    # 允许 build_story_prompt / build_task_prompt 读 ctx.get("recalled") 作为 fallback
    # 但 load_context / build_task_context 不能写
    assert "ctx[\"recalled\"] = self._recall_episodes" not in src, \
        "load_context / build_task_context 不应再写 ctx[\"recalled\"]"


def test_p1_context_builder_has_three_layer_memory_retrieval():
    """P1 #1: ContextBuilder 拉三段 memory：Learnings（主）+ Playbook（次）+ Episodes（辅助）。

    全部受 behavior.preparation.load_memory 统一控制。
    """
    from agentboard.agent_runtime.behavior.context_builder import (
        ExecutionContextBuilder, ExecutionContext,
    )
    src = inspect.getsource(ExecutionContextBuilder)

    # 关键 API
    assert "def _resolve_learnings" in src, \
        "ContextBuilder 必须有 _resolve_learnings（新 Learning 主源）"
    assert "def _resolve_playbook_and_episodes" in src, \
        "ContextBuilder 必须有 _resolve_playbook_and_episodes（次/辅助）"
    assert "def _fetch_playbook" in src, \
        "ContextBuilder 必须有 _fetch_playbook helper"
    assert "def _fetch_similar_episodes" in src, \
        "ContextBuilder 必须有 _fetch_similar_episodes helper"

    # ExecutionContext 必须有 playbook / episodes 字段
    from pydantic import BaseModel
    fields = ExecutionContext.__annotations__
    assert "playbook" in fields, "ExecutionContext 必须有 playbook 字段"
    assert "episodes" in fields, "ExecutionContext 必须有 episodes 字段"
    assert "learnings" in fields, "ExecutionContext 必须有 learnings 字段"

    # sources_resolved 必须新增 playbook / episodes
    src_resolved = src[src.find("sources_resolved"):]
    assert "playbook_from_db" in src_resolved or "playbook_from_ctx" in src_resolved, \
        "sources_resolved 必须含 playbook 来源标记"
    assert "episodes_from_db" in src_resolved or "episodes_from_ctx" in src_resolved, \
        "sources_resolved 必须含 episodes 来源标记"


def test_p1_load_memory_false_disables_all_three_layers():
    """P1 #1: load_memory=False 时 Learnings + Playbook + Episodes 三段都不拉。

    验证：ContextBuilder._resolve_learnings / _resolve_playbook_and_episodes
    都在 load_memory=False 时 early return。
    """
    from agentboard.agent_runtime.behavior.context_builder import ExecutionContextBuilder
    src = inspect.getsource(ExecutionContextBuilder)

    # _resolve_learnings 早期返回检查
    learnings_block = src[src.find("def _resolve_learnings"):]
    learnings_block = learnings_block[:learnings_block.find("\n    def _resolve_playbook")]
    assert "if not behavior.preparation.load_memory:" in learnings_block, \
        "_resolve_learnings 必须 early return on load_memory=False"

    # _resolve_playbook_and_episodes 早期返回检查
    playbook_block = src[src.find("def _resolve_playbook_and_episodes"):]
    playbook_block = playbook_block[:playbook_block.find("\n    def _fetch_playbook")]
    assert "if not behavior.preparation.load_memory:" in playbook_block, \
        "_resolve_playbook_and_episodes 必须 early return on load_memory=False"


# ============================================================
# P1/P2 #2: LearningExtractor 真正调 Agent
# ============================================================

def test_p1p2_learning_extractor_actually_uses_invoker():
    """P1/P2 #2: LearningExtractor.extract() 真正调 invoker 做 reflection。

    原 bug：extract() 接受 invoker 但完全没用，全是 hardcode 模板。
    修法：分两条路径 —— reflection_agent 路径（preferred）调 invoker.invoke_with_prompt；
    heuristic 路径（fallback）保留原 hardcode 模板。
    """
    from agentboard.agent_runtime.learning import extractor as ext_module
    src = inspect.getsource(ext_module)

    # 关键：必须实现 _extract_via_reflection_agent
    assert "def _extract_via_reflection_agent" in src, \
        "LearningExtractor 必须有 _extract_via_reflection_agent 路径"
    # 关键：必须实现 _extract_via_heuristic（fallback）
    assert "def _extract_via_heuristic" in src, \
        "LearningExtractor 必须有 _extract_via_heuristic fallback"
    # 关键：extract 必须先尝试 reflection_agent
    extract_block = src[src.find("def extract("):]
    extract_block = extract_block[:extract_block.find("\n        return ")]
    assert "reflection_agent" in extract_block, \
        "extract() 必须先调 reflection_agent 路径"
    # 关键：reflection_agent 路径调 invoker
    reflection_block = src[src.find("def _extract_via_reflection_agent"):]
    reflection_block = reflection_block[:reflection_block.find("\n    def ")]
    assert "invoker.invoke_with_prompt" in reflection_block or "invoker.invoke(" in reflection_block, \
        "_extract_via_reflection_agent 必须真调 invoker"


def test_p1p2_learning_extractor_reflection_with_invoker_uses_real_output():
    """P1/P2 #2: reflection_agent 路径用 invoker 真实输出作为 lesson。

    验证：给定一个返回合法 JSON 的 invoker，ExtractedLesson.source == "reflection_agent"，
    且 lesson 字段含 invoker 输出内容。
    """
    from agentboard.agent_runtime.learning.evaluator import (
        LearningCategory, LearningTriggerEvent,
    )
    from agentboard.agent_runtime.learning.extractor import (
        LearningExtractor, learning_extractor,
    )
    from agentboard.agent_runtime.config import AgentDecision
    from agentboard.agent_runtime.invokers import CallableAgentInvoker

    # 模拟一个真反思的 agent：返回结构化 JSON
    real_reflection_json = {
        "summary": "Owner 漏看了 API 文档 v2 的 breaking change",
        "what_missed": "未读 OpenAPI v2 spec 的 deprecated 字段",
        "why_missed": "Owner 直接用代码搜索工具（grep）找 caller，没去翻 OpenAPI 文档",
        "evidence_to_check": "修改 API 字段前必须先 GET /openapi.json 确认 schema",
        "lesson": "API 变更前先查 schema 文档，grep 代码不够",
        "tags": ["api", "schema", "openapi"],
        "confidence": 0.85,
    }

    def fake_agent_invoke_with_prompt(prompt, context):
        import json
        return AgentDecision(
            action="noop",
            summary=json.dumps(real_reflection_json, ensure_ascii=False),
            comment="",
        )

    invoker = CallableAgentInvoker(lambda c: AgentDecision(action="noop"))
    # 替换 invoke_with_prompt
    invoker.invoke_with_prompt = fake_agent_invoke_with_prompt  # type: ignore[assignment]

    event = LearningTriggerEvent(
        project_id=1,
        category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
        discussion_context="reviewer reject: API call missing required field",
        work_type="implementation",
        summary_hint="",
        confidence=0.5,
    )

    result = learning_extractor.extract(event, invoker=invoker)

    # 关键：source 标记是 reflection_agent（不是 heuristic）
    assert result.source == "reflection_agent", \
        f"应走 reflection_agent 路径，实际是 {result.source}"
    # 关键：lesson 字段是 agent 真实反思的输出（不是 hardcode 模板的"未充分考虑..."）
    assert "schema 文档" in result.lesson, \
        f"lesson 应含 invoker 真实反思输出，实际：{result.lesson}"
    # 关键：why_missed 反映真实根因
    assert "OpenAPI" in result.why_missed, \
        f"why_missed 应含真实根因，实际：{result.why_missed}"


def test_p1p2_learning_extractor_falls_back_to_heuristic_on_invoker_failure():
    """P1/P2 #2: invoker 不可用 / reflection 输出不合法 → fallback heuristic。

    验证：source 标记是 heuristic，且 lesson 字段是 hardcode 模板。
    """
    from agentboard.agent_runtime.learning.evaluator import (
        LearningCategory, LearningTriggerEvent,
    )
    from agentboard.agent_runtime.learning.extractor import learning_extractor
    from agentboard.agent_runtime.config import AgentDecision
    from agentboard.agent_runtime.invokers import CallableAgentInvoker

    # 模拟 invoker 输出非 JSON（reflection 失败）
    def fake_invoke_with_prompt(prompt, context):
        return AgentDecision(
            action="noop",
            summary="",  # 空 — 解析失败
            comment="",
        )

    invoker = CallableAgentInvoker(lambda c: AgentDecision(action="noop"))
    invoker.invoke_with_prompt = fake_invoke_with_prompt  # type: ignore[assignment]

    event = LearningTriggerEvent(
        project_id=1,
        category=LearningCategory.ACCEPTED_REVIEW_FEEDBACK,
        discussion_context="x",
        work_type="implementation",
        summary_hint="",
        confidence=0.5,
    )

    result = learning_extractor.extract(event, invoker=invoker)

    # fallback 后 source 标记是 heuristic
    assert result.source == "heuristic"
    # lesson 是 hardcode 模板特征
    assert "复用经验" in result.lesson, \
        f"heuristic fallback 应含 hardcode 模板特征，实际：{result.lesson}"


# ============================================================
# P2 #3: taxonomy 命名
# ============================================================

def test_p2_taxonomy_renamed_record_learning_outcome_to_task_outcome_and_memory():
    """P2 #3: _record_learning_outcome 改名为 _record_task_outcome_and_memory（旧名 alias 保留）。

    验证：features.learning.models 文档化 4 个 model taxonomy；
    service.py 同时定义 _record_task_outcome_and_memory 与 _record_learning_outcome alias。
    """
    from agentboard.features.work_items import service as work_items_service
    src = inspect.getsource(work_items_service)

    assert "def _record_task_outcome_and_memory" in src, \
        "service.py 必须定义 _record_task_outcome_and_memory（新名）"
    assert "_record_learning_outcome = _record_task_outcome_and_memory" in src, \
        "_record_learning_outcome 必须保留为 alias（向后兼容）"

    # 4 个 model taxonomy 文档
    from agentboard.features.learning import models as learning_models
    models_src = learning_models.__doc__ or ""
    assert "TaskOutcome" in models_src and "Evaluation" in models_src
    assert "EpisodeEmbedding" in models_src and "Experience" in models_src
    assert "ProjectPlaybook" in models_src and "Project Guidance" in models_src
    assert "Learning" in models_src and "Correction" in models_src
