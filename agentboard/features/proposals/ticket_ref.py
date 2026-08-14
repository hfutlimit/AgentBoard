"""TicketRef 值对象（Epic 123 Step 3 · Story 239）。

Proposal 转化产出的工单引用（4 类型：epic/story/task/bug）的创建与回填。
取代 ``service._ticket_execute_result`` 的 4 个 if 分支，集中处理：
父级校验 → 事务性创建 → ticket_type/ticket_id/story_id 回填。
"""
from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

# 注:历史上 import 了 ``from ... import service as svc`` 用来调 service 的
# create_* / _required / utc_now,但 ``service -> agentboard.models -> proposals.models
# (shim) -> features.proposals -> ticket_ref`` 形成循环。改为函数体内惰性 import。
# 本文件目前没有直接调 svc 的代码(create() 走自己的实现),所以不需要模块级 import。

TicketType = Literal["epic", "story", "task", "bug"]


class TicketRef:
    """Proposal 转化产出的工单引用。"""

    __slots__ = ("type", "id", "parent_epic_id", "parent_story_id")

    def __init__(self, type: str, id: int,
                 parent_epic_id: int | None = None,
                 parent_story_id: int | None = None):
        self.type = type
        self.id = id
        self.parent_epic_id = parent_epic_id
        self.parent_story_id = parent_story_id

    @classmethod
    def create(cls, s: Session, proposal, *, type: str,
               parent_epic_id: int | None = None,
               parent_story_id: int | None = None,
               title: str | None = None) -> "TicketRef":
        """事务性创建 + 父级校验 + 4 类型分支集中处理。

        取代 service._ticket_execute_result 的 4 个 if 分支。
        注意：本方法会 commit（create_epic/story/task 内部各自 commit），
        与 execute_ticket_request 的既有事务语义保持一致。
        """
        from ...service import (
            _required, create_epic, create_story, create_task, utc_now,
        )
        spec = proposal.converged_spec or proposal.content or ""
        resolved_title = _required(title or proposal.title, "title", 300)
        ticket_id: int
        if type == "epic":
            epic = create_epic(s, project_id=proposal.project_id,
                               title=resolved_title, description=spec)
            ticket_id = epic.id
        elif type == "story":
            if not parent_epic_id:
                raise ValueError("story 类型 ticket 需要 epic_id")
            story = create_story(s, epic_id=parent_epic_id,
                                 title=resolved_title, description=spec)
            ticket_id = story.id
            proposal.story_id = story.id  # 兼容既有查询（type=story 快捷字段）
        else:  # task / bug
            if not parent_story_id:
                raise ValueError(f"{type} 类型 ticket 需要 story_id")
            task = create_task(
                s, project_id=proposal.project_id, story_id=parent_story_id,
                title=resolved_title, type=type, description=spec,
            )
            ticket_id = task.id
        return cls(type=type, id=ticket_id,
                   parent_epic_id=parent_epic_id,
                   parent_story_id=parent_story_id)

    def attach_to_proposal(self, s: Session, proposal) -> None:
        """回填 proposals.ticket_type / ticket_id / story_id（兼容字段）。"""
        from ...service import utc_now
        proposal.ticket_type = self.type
        proposal.ticket_id = self.id
        proposal.updated_at = utc_now()
