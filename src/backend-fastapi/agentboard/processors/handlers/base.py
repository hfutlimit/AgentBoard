"""Handler 协议与基类（Unified Execution Model）。

一个 Handler 负责一个具体 WorkType 的执行策略（构建 Prompt / 调用 Agent / 返回 ExecutionResult）。
Worker 协调器只做统一调度与上报，跨实体业务编排由服务端状态机驱动。
"""
from __future__ import annotations

from typing import Any, Protocol

from ..config import AgentDecision, ProcessorInvoker
from ..contract import ExecutionCommand, ExecutionResult, WorkType


class Handler(Protocol):
    """历史 Handler 协议（保持向后兼容）。"""

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


class BaseWorkHandler:
    """统一模型下的 Handler 执行基类。"""

    work_type: WorkType
    name: str
    valid_actions: set[str]

    def can_handle(self, work_item: dict | ExecutionCommand) -> bool:
        """判断工作项是否由本 Handler 承接。"""
        if isinstance(work_item, ExecutionCommand):
            return work_item.work_type == self.work_type
        return False

    def build_prompt(self, context: dict) -> str:
        """构建 Agent Prompt。"""
        raise NotImplementedError

    def load_context(self, command: ExecutionCommand | dict) -> dict:
        """加载执行所需上下文。"""
        raise NotImplementedError

    def execute_command(self, command: ExecutionCommand, invoker: ProcessorInvoker) -> ExecutionResult:
        """纯执行接口：构建上下文 -> invoke -> 返回 ExecutionResult。"""
        raise NotImplementedError

    def handle(self, work_item: dict, invoker: ProcessorInvoker) -> str:
        """历史兼容入口：处理单个工作项 dict 并返回 outcome 字符串。"""
        raise NotImplementedError
