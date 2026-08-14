"""Proposals domain — Proposal 澄清回路(Epic 96:人机协同需求分析)。

Epic 123 Step 3(Story 239)新增模块:
- ``state_machine``:Proposal 状态机一等公民(迁移定义 + 副作用绑定)
- ``ticket_ref``:TicketRef 值对象(4 类型工单创建与回填)
"""
from . import display, models, state_machine, ticket_ref  # noqa: F401
