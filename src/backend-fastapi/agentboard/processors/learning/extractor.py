"""Learning extractor (Task 13: LearningExtractor).

Distills structured reflections and reusable lessons from trigger events.

Structured reflection framework (Self-Correction Reflection):
1. What was missed?
2. Why was it missed? (root cause)
3. What evidence should have been checked?
4. Actionable rule

Review 2026-08-26 P1/P2 #2 fix:
The old ``extract()`` accepted an ``invoker`` argument but never used it;
reflection came from a hardcoded template. That produces "fake
reflections" - memories that look plausible but are not facts - which are
worse than no memory at all.

Fix: two paths.

1) Reflection Agent path (preferred): use the invoker to run a real
   LLM/agent reflection producing structured JSON, validate it, and map
   it to ExtractedLesson. The invoker can be SubprocessProcessorInvoker
   (production) or CallableProcessorInvoker (tests).

2) Heuristic path (fallback): when the invoker is unavailable or the
   reflection fails, degrade to the original hardcoded template. Kept
   for backwards compatibility and e2e behaviour.

Both paths produce the same ExtractedLesson shape, so the agent runtime
and e2e suites can exercise either.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from pydantic import BaseModel, Field

from .evaluator import LearningCategory, LearningTriggerEvent

log = logging.getLogger("agentboard.processors.learning.extractor")


class ExtractedLesson(BaseModel):
    summary: str
    lesson: str
    category: str
    what_missed: str = ""
    why_missed: str = ""
    evidence_to_check: str = ""
    tags: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    # Which path produced this lesson (drives metrics and later
    # playbook-promotion decisions).
    source: str = "heuristic"  # "reflection_agent" | "heuristic"


_REFLECTION_PROMPT = """You are AgentBoard's reflection assistant. You distill a
*real, reusable* lesson from one failure/correction event.

## Trigger event
- Category: {category}
- Work type: {work_type}
- Summary hint: {summary_hint}
- Discussion context (sanitized):
{discussion_context}

## Reflection framework (output STRICT JSON)
1. **summary**: one-sentence summary of the event (<= 80 chars)
2. **what_missed**: what was actually missed (base it on
   discussion_context; avoid empty generalities)
3. **why_missed**: root-cause analysis (avoid vague phrases like
   "not fully considered"; state the concrete reason)
4. **evidence_to_check**: the concrete evidence that must be checked
   next time (files / schema / call chain / test cases, etc.)
5. **lesson**: the reusable action rule (<= 200 chars)
6. **tags**: keyword list (include category + work_type plus the key
   technical nouns from discussion_context)
7. **confidence**: 0.0-1.0; the more specific the event signal, the
   higher the confidence

## Output format (a single JSON object, no extra text)
{{"summary": "...", "what_missed": "...", "why_missed": "...", "evidence_to_check": "...", "lesson": "...", "tags": ["..."], "confidence": 0.X}}
"""


class LearningExtractor:
    """Structured deep-reflection extractor (Review 2026-08-26: added
    the reflection_agent path)."""

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
        """Reflection Agent path: run a real structured reflection via the invoker.

        Invoker contract (same as processors.invokers.ProcessorInvoker):
        ``invoke_with_prompt(prompt: str, context: dict) -> AgentDecision``

        AgentDecision carries summary / comments / error / action. This
        method builds the prompt from _REFLECTION_PROMPT; the agent
        internally calls the LLM for reflection and outputs structured
        JSON, which we extract with ``extract_decision_json`` (tolerant
        of large stdout noise).

        On failure return None (caller falls back to heuristic).
        """
        if invoker is None:
            return None
        try:
            from .invokers import extract_decision_json  # deferred to avoid cycles
        except Exception:
            extract_decision_json = None

        prompt = _REFLECTION_PROMPT.format(
            category=event.category.value,
            work_type=event.work_type or "(unspecified)",
            summary_hint=event.summary_hint or "(none)",
            discussion_context=self._sanitize_context(event.discussion_context),
        )

        try:
            # Prefer invoke_with_prompt (when the invoker supports it).
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
                # Fallback: invoke(context) + build_prompt(context) path.
                decision = invoker.invoke({
                    "work_type": event.work_type or "general",
                    "category": event.category.value,
                    "summary_hint": event.summary_hint or "",
                    "raw_context_summary": prompt,
                })
        except Exception as e:
            log.warning("LearningExtractor: reflection_agent invoke failed: %s", e)
            return None

        # Parse the decision output (usually decision.summary /
        # decision.comment carries the JSON).
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
        # Agent output contained no valid JSON - treat as failure.
        log.info("LearningExtractor: reflection_agent produced no valid "
                 "JSON, falling back to heuristic")
        return None

    def _parse_reflection_json(self, blob: str) -> dict | None:
        """Extract a JSON object from an agent output blob (may contain
        markdown / noise)."""
        # 1. Try ``extract_decision_json`` (processors.invokers helper).
        try:
            from .invokers import extract_decision_json
            return extract_decision_json(blob)
        except Exception:
            pass
        # 2. Fallback: manual bracket-pair scan.
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
        """Merge agent-returned tags + category + work_type + keyword tags."""
        cat = event.category.value
        tags = {cat}
        if event.work_type:
            tags.add(str(event.work_type).lower())
        for t in parsed_tags:
            if isinstance(t, str) and t.strip():
                tags.add(t.strip().lower())
        return list(tags)

    def _extract_via_heuristic(self, event: LearningTriggerEvent) -> ExtractedLesson:
        """Heuristic path (original hardcoded template; kept as the
        fallback by Review 2026-08-26)."""
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
        """Distill a structured lesson and deep reflection from a learning event.

        Review 2026-08-26 P1/P2 #2 fix: two paths.

        1. Reflection Agent path (invoker is not None) - the agent does a
           real reflection;
        2. Heuristic path (fallback) - hardcoded template, used when the
           reflection fails or no invoker is available.
        """
        # 1. Prefer the reflection_agent path.
        if invoker is not None:
            try:
                agent_result = self._extract_via_reflection_agent(event, invoker)
                if agent_result is not None:
                    return agent_result
            except Exception as e:
                log.warning("LearningExtractor: reflection_agent failed, "
                            "falling back to heuristic: %s", e)

        # 2. Fallback heuristic (preserves e2e behaviour + old callers).
        return self._extract_via_heuristic(event)


learning_extractor = LearningExtractor()
