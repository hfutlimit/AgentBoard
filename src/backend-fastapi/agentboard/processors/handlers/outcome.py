"""Handler outcome enum（Review 2026-08-26 P1 #1 修复）。

Handler.handle_decision() 与 Handler.execute_command() 之间的 outcome
语义此前用裸字符串 ("created" / "failed" / "skipped" vs "success") 表达，
字符串不匹配导致正常成功路径被误判为 failure。

修复：所有 Handler 共用 ``TicketOutcome`` / ``HandlerOutcome`` enum 作为
唯一定义，execute_command 跟 handle_decision 比对 enum value 而非字面量。
"""
from __future__ import annotations

from enum import StrEnum


class TicketOutcome(StrEnum):
    """TicketHandler outcome 语义。

    - CREATED: ticket 实体已落库（成功）
    - FAILED: 明确失败（agent 报告失败 / 兜底回查仍 pending）
    - SKIPPED: 跳过（agent 主动放弃 + 兜底回查非 done/failed/pending）
    """
    CREATED = "created"
    FAILED = "failed"
    SKIPPED = "skipped"


class HandlerOutcome(StrEnum):
    """通用 Handler outcome 语义（未来扩展用）。"""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
