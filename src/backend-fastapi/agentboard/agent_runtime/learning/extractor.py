"""学习抽取器（Task 13：LearningExtractor）。

根据触发事件深度提炼结构化反思与可复用教训。
结构化反思框架（Self-Correction Reflection）：
1. What was missed? (遗漏点)
2. Why was it missed? (根因分析)
3. What evidence should have been checked? (核查依据)
4. Actionable rule (未来行动准则)

Review 2026-08-26 P1/P2 #2 修复：
原 extract() 接受 ``invoker`` 参数但完全没用，反思靠硬编码模板。这种
"假反思"会产生"看起来合理但不是事实"的 memory，比没 memory 更危险。

修法：分两条路径

  1) Reflection Agent 路径（preferred）—— 用 invoker 调 LLM/Agent 做真实反思
     产出结构化 JSON，校验后转 ExtractedLesson。invoker 可以复用
     SubprocessAgentInvoker（生产）或 CallableAgentInvoker（测试）。

  2) Heuristic 路径（fallback）—— invoker 不可用 / reflection 失败时
     降级到原硬编码模板。保留向后兼容与 e2e 行为。

两条路径结构对齐（都是 ExtractedLesson），Agent runtime 跟 e2e 都能跑。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field

from .evaluator import LearningCategory, LearningTriggerEvent

log = logging.getLogger("agentboard.agent_runtime.learning.extractor")


class ExtractedLesson(BaseModel):
    summary: str
    lesson: str
    category: str
    what_missed: str = ""
    why_missed: str = ""
    evidence_to_check: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    # 标记哪条路径产出（用于 metrics / 后续 Playbook promote 决策）
    source: str = "heuristic"  # "reflection_agent" | "heuristic"


_REFLECTION_PROMPT = """你是 AgentBoard 的反思助手，负责从一次失败/纠正事件中提炼**真实可复用**的教训。

## 触发事件
- 类别: {category}
- 类型: {work_type}
- 摘要提示: {summary_hint}
- 讨论上下文（已清洗）:
{discussion_context}

## 反思框架（必须严格按 JSON 输出）
1. **summary**: 一句话总结这次事件（≤ 80 字）
2. **what_missed**: 实际遗漏了什么（基于 discussion_context，避免空泛）
3. **why_missed**: 根因分析（避免"未充分考虑"这种空话，要说明具体原因）
4. **evidence_to_check**: 下次必须核查的具体证据（文件名 / schema / 调用链 / 测试用例等）
5. **lesson**: 可复用的行动准则（≤ 200 字）
6. **tags**: 关键词列表（包含 category + work_type，加上 discussion_context 里的关键技术名词）
7. **confidence**: 0.0-1.0，事件信号越具体 confidence 越高

## 输出格式（仅一个 JSON 对象，不要任何额外文本）
{{"summary": "...", "what_missed": "...", "why_missed": "...", "evidence_to_check": "...", "lesson": "...", "tags": ["..."], "confidence": 0.X}}
"""


class LearningExtractor:
    """结构化深度反思提炼器（Review 2026-08-26 加 reflection_agent 路径）。"""

    def _sanitize_context(self, text: str) -> str:
        """Sanitize context to prevent prompt injection."""
        text = text.strip()
        patterns = [
            r"ignore previous instructions",
            r"system:",
            r"<\|im_start\|>",
            r"<\|im_end\|>"
        ]
        for p in patterns:
            text = re.sub(p, "", text, flags=re.IGNORECASE)

        text = text[:500]
        if text:
            return f"=== CONTEXT START ===\n{text}\n=== CONTEXT END ==="
        return ""

    def _extract_via_reflection_agent(
        self, event: LearningTriggerEvent, invoker: Any,
    ) -> ExtractedLesson | None:
        """Reflection Agent 路径：调 invoker 做真实结构化反思。

        invoker 接口（与 agent_runtime.invokers.AgentInvoker 一致）：
        invoke_with_prompt(prompt: str, context: dict) -> AgentDecision

        AgentDecision 包含 summary / comments / error / action。prompt 由本方法
        构造（REFLECTION_PROMPT 模板），由 Agent 内部调 LLM 反思后输出结构化 JSON，
        再用 extract_decision_json 抽取（兼容 stdout 含大量 noise 的场景）。

        失败：返回 None（调用方 fallback 到 heuristic）。
        """
        if invoker is None:
            return None
        try:
            from .invokers import extract_decision_json  # 延迟避免循环
        except Exception:
            extract_decision_json = None

        prompt = _REFLECTION_PROMPT.format(
            category=event.category.value,
            work_type=event.work_type or "(unspecified)",
            summary_hint=event.summary_hint or "(无)",
            discussion_context=self._sanitize_context(event.discussion_context),
        )

        try:
            # 优先用 invoke_with_prompt（如果 invoker 支持）
            if hasattr(invoker, "invoke_with_prompt"):
                decision = invoker.invoke_with_prompt(
                    prompt,
                    {
                        "work_type": event.work_type or "general",
                        "category": event.category.value,
                        "summary_hint": event.summary_hint or "",
                    },
                )
            else:
                # 兜底走 invoke(context) + build_prompt(context) 路径
                decision = invoker.invoke({
                    "work_type": event.work_type or "general",
                    "category": event.category.value,
                    "summary_hint": event.summary_hint or "",
                    "raw_context_summary": prompt,
                })
        except Exception as e:
            log.warning("LearningExtractor: reflection_agent invoke 失败：%s", e)
            return None

        # 解析 decision 输出（通常是 decision.summary / decision.comment 含 JSON）
        candidates = [
            getattr(decision, "summary", "") or "",
            getattr(decision, "comment", "") or "",
            getattr(decision, "converged_spec", "") or "",
        ]
        for blob in candidates:
            if not blob:
                continue
            parsed = self._parse_reflection_json(blob)
            if parsed is not None:
                return ExtractedLesson(
                    summary=parsed.get("summary") or event.summary_hint or "reflection",
                    lesson=parsed.get("lesson") or "",
                    category=event.category.value,
                    what_missed=parsed.get("what_missed", ""),
                    why_missed=parsed.get("why_missed", ""),
                    evidence_to_check=parsed.get("evidence_to_check", ""),
                    tags=self._merge_tags(parsed.get("tags") or [], event),
                    confidence=float(parsed.get("confidence") or event.confidence),
                    source="reflection_agent",
                )
        # Agent 输出不含合法 JSON —— 失败
        log.info("LearningExtractor: reflection_agent 输出无合法 JSON，fallback heuristic")
        return None

    def _parse_reflection_json(self, blob: str) -> dict | None:
        """从 agent 输出的 blob（可能含 markdown / noise）里抽 JSON 对象。"""
        # 1. 先尝试 ``extract_decision_json``（agent_runtime.invokers 工具）
        try:
            from .invokers import extract_decision_json
            return extract_decision_json(blob)
        except Exception:
            pass
        # 2. 兜底：手动括号配对扫描
        text = blob.strip()
        if not text:
            return None
        depth = 0
        start = -1
        in_str = False
        escape = False
        for i, ch in enumerate(text):
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start >= 0:
                        try:
                            data = json.loads(text[start:i + 1])
                            if isinstance(data, dict):
                                return data
                        except json.JSONDecodeError:
                            pass
                        start = -1
        return None

    def _merge_tags(self, parsed_tags: list[str], event: LearningTriggerEvent) -> list[str]:
        """合并 Agent 返的 tags + category + work_type + 关键词 tag。"""
        cat = event.category.value
        tags = {cat}
        if event.work_type:
            tags.add(str(event.work_type).lower())
        for t in parsed_tags:
            if isinstance(t, str) and t.strip():
                tags.add(t.strip().lower())
        return list(tags)

    def _extract_via_heuristic(self, event: LearningTriggerEvent) -> ExtractedLesson:
        """Heuristic 路径（原 hardcode 模板，Review 2026-08-26 保留为 fallback）。"""
        raw_ctx = event.discussion_context.strip()
        ctx = self._sanitize_context(raw_ctx)
        cat = event.category.value

        tags = [cat]
        if event.work_type:
            tags.append(str(event.work_type).lower())

        for kw in [
            "database", "sqlite", "mariadb", "migration", "index", "unique", "foreign_key",
            "async", "await", "lock", "timeout", "review", "auth", "token", "permission",
            "validation", "schema", "api", "cache", "redis", "csrf", "decimal", "float",
        ]:
            if kw in raw_ctx.lower() or kw in event.summary_hint.lower():
                tags.append(kw)

        if event.category == LearningCategory.ACCEPTED_REVIEW_FEEDBACK:
            summary = event.summary_hint or "采纳审查意见并修复缺陷"
            what_missed = f"实现时未满足的审查项: {ctx}"
            why_missed = "开发过程中未充分考虑边界条件、数据一致性或架构约束。"
            evidence_to_check = "修改代码前必须检索相关 schema 定义、调用上下文与异常处理路径。"
            lesson = (
                f"【复用经验】{summary}。\n"
                f"- 遗漏分析: {what_missed}\n"
                f"- 根因排查: {why_missed}\n"
                f"- 防范准则: 在后续类似开发中，必须在自测阶段主动核查：{evidence_to_check}"
            )
        elif event.category == LearningCategory.REVIEW_JUDGMENT_REVERSAL:
            summary = event.summary_hint or "评审误判经申诉后撤销改判"
            what_missed = f"评审时遗漏了已有代码上下文证据: {ctx}"
            why_missed = "评审仅凭局部代码或主观假设，未全局检索代码库实际调用逻辑。"
            evidence_to_check = "在驳回前必须使用代码检索工具复核全链路实现，严禁未查实即判错。"
            lesson = (
                f"【评审反思】{summary}。\n"
                f"- 误判原因: {why_missed}\n"
                f"- 必须核查: {evidence_to_check}\n"
                f"- 评审准则: 提出 Reject 时必须附带可复现证据或明确的规范依据。"
            )
        elif event.category == LearningCategory.QA_DEFECT:
            summary = event.summary_hint or "QA 阶段捕获到漏测缺陷"
            what_missed = f"开发自测遗漏的缺陷场景: {ctx}"
            why_missed = "测试用例覆盖不全，缺少针对异常输入或边界状态的验证。"
            evidence_to_check = "验收前必须编写针对性单元测试与集成测试覆盖此缺陷路径。"
            lesson = f"【质量教训】{summary}。开发阶段必须补齐自测用例：{what_missed}。"
        else:
            summary = event.summary_hint or "执行异常后成功恢复"
            what_missed = "偶发性异常或外部依赖故障"
            why_missed = "缺乏重试机制或超时熔断"
            evidence_to_check = "检查外部依赖可用性与网络重试配置"
            lesson = f"【恢复经验】{summary}。注意做好容灾与幂等重试。"

        return ExtractedLesson(
            summary=summary,
            lesson=lesson,
            category=cat,
            what_missed=what_missed,
            why_missed=why_missed,
            evidence_to_check=evidence_to_check,
            tags=list(set(tags)),
            confidence=event.confidence,
            source="heuristic",
        )

    def extract(
        self,
        event: LearningTriggerEvent,
        invoker: Any = None,
    ) -> ExtractedLesson:
        """从学习事件中提炼出结构化经验教训与深度反思。

        Review 2026-08-26 P1/P2 #2 修复：两条路径

        1. Reflection Agent 路径（invoker 不为 None）—— 调 Agent 做真实反思；
        2. Heuristic 路径（fallback）—— 硬编码模板，仅 reflection 失败 / invoker 不可用时用。
        """
        # 1. 优先 reflection_agent 路径
        if invoker is not None:
            try:
                agent_result = self._extract_via_reflection_agent(event, invoker)
                if agent_result is not None:
                    return agent_result
            except Exception as e:
                log.warning("LearningExtractor: reflection_agent 失败 fallback heuristic：%s", e)

        # 2. Fallback heuristic（保 e2e 行为 + 旧 caller 兼容）
        return self._extract_via_heuristic(event)


learning_extractor = LearningExtractor()