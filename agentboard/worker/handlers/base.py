"""Handler 协议（Epic 123 Step 2 · Worker 拆 Handler 类）。

一个 Handler 负责一个完整业务域的 agent 协作（需求澄清 / Ticket 转化 /
Story 编排）。Worker 主循环只做「发现 + 路由」，业务逻辑全部下沉。
"""
from __future__ import annotations

from typing import Protocol

from ..config import AgentDecision


class Handler(Protocol):
    """一个 Handler 负责一个完整业务域的 agent 协作。"""

    name: str  # 路由 key
    valid_actions: set[str]  # 接受的 agent decision action

    def can_handle(self, work_item: dict) -> bool:
        """判断 work_item 是否属于本域（Worker dispatch 路由依据）。"""
        ...

    def fetch(self) -> list[dict]:
        """拉取本域待处理工作项（DB 轮询或 MQ 提示后回查）。"""
        ...

    def build_prompt(self, context: dict) -> str:
        """把上下文渲染成 prompt（提示词模板按域隔离）。"""
        ...

    def load_context(self, work_item: dict) -> dict:
        """拉取全量重放上下文（proposal/story/ticket 各自的组装逻辑）。"""
        ...

    def handle_decision(self, work_item: dict, decision: AgentDecision,
                        context: dict) -> str:
        """落决策：根据 action 执行对应服务端调用，返回结果码。"""
        ...
