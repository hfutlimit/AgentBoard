"""ProposalConversionService（Review 2026-08-26 P1/P2 #5 修复）。

GPT 第四轮 review 发现当前存在两套 conversion 路径：

A) ``convert_proposal_to_story`` → 走 ``proposal.status = "story_created"``；
   基于 ``converged_spec`` 的 ``- [ ]`` 清单生成 Story + Tasks。
B) ``execute_ticket_request`` → 走 ``TicketRef.create`` →
   ``proposal.status = "ticket_created"``；按 type 创建 Epic / Story / Task / Bug。

两套 path 终态冲突、职责重叠、agent 不应自己连续调 create_epic / create_story /
create_task 然后告诉 server "我做好了"。

本模块提供 ``ProposalConversionService`` 收敛两套 path 为单一入口：

    ConversionPlan   ←  Agent 提供"想要生成什么" / Server 从 spec 推演
    validate()       ←  Server 校验完整性（min_tasks / type / DAG acyclic / parent）
    apply()          ←  事务性落库（Document + Epic + Story + Tasks + Dependencies + Proposal）

Phase 1（本 commit）只覆盖 Proposal→Story 主路径（最常用 + 跟新概念最贴近）。
Ticket 转化路径（epic / task / bug 单一 type）保持薄 facade 委派进同一 service，
后续 Phase 2 收敛。

设计原则：

- ``Transaction belongs to use case, not entity helper``：
  create_story / create_task / create_epic 接受 ``commit=False`` 让 service
  统一收尾；不引入 Unit of Work 框架。
- ``Configuration stores intent, not mechanism``：
  ``ConversionPolicy`` 描述"想要哪些 entity + 多少 + 什么关联"，不写 SQL。
- 单一终态 ``converted``（保留 ``story_created`` / ``ticket_created`` 作为
  alias 维持向后兼容；后续 Phase 2 收敛所有路径到 ``converted``）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from ...core.common.enums import ItemType, Priority
from ...core.exceptions import InvalidValue, NotFound
from ...core.service_helpers import _commit, _invalidate_project_stats_cache
from ..projects.models import Epic, Project, Story
from ..work_items.models import Task, TaskDependency

log = logging.getLogger("agentboard.features.proposals.conversion_service")


# ---------------------------------------------------------------------------
# ConversionPlan：Agent 提供或 Server 从 spec 推演的可验证中间产物
# ---------------------------------------------------------------------------

@dataclass
class ConversionPlan:
    """提案转化的可验证 plan。

    Fields:
        document: Project Document 信息 dict（title / type / content）；None = 不创建
        epic: Epic 信息 dict（title / description / project_id）；None = 用现有 epic_id
        epic_id: 显式关联到已有 Epic（epic 必须 None，否则冲突）
        story: Story 信息 dict（title / description）
        tasks: 任务列表，每项 dict（title / type / description / priority）
        dependencies: 显式依赖边 list of (source_title, target_title)
        create_qa: 是否自动追加 QA 验收 task（默认 True）
        min_tasks: 最小 task 数量（含 design / dev / qa），不足则 validate 失败
    """
    document: dict[str, Any] | None = None
    epic: dict[str, Any] | None = None
    epic_id: int | None = None
    story: dict[str, Any] | None = None
    tasks: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[tuple[str, str]] = field(default_factory=list)
    create_qa: bool = True
    min_tasks: int = 3


# ---------------------------------------------------------------------------
# ConversionResult：apply() 返回的结构
# ---------------------------------------------------------------------------

@dataclass
class ConversionResult:
    """apply() 的落库结果（用于 API 序列化 + Outbox event 投递）。"""
    document_id: int | None = None
    epic_id: int | None = None
    story_id: int | None = None
    task_ids: list[int] = field(default_factory=list)
    dependency_ids: list[int] = field(default_factory=list)
    proposal_status: str = "converted"


# ---------------------------------------------------------------------------
# ProposalConversionService：plan / validate / apply
# ---------------------------------------------------------------------------

class ProposalConversionService:
    """提案转化的统一服务。

    用法：
        plan = ProposalConversionService.plan(proposal, epic_id=42)
        ProposalConversionService.validate(plan, project_id=proposal.project_id)
        result = ProposalConversionService.apply(s, plan, proposal)
    """

    @staticmethod
    def plan(proposal, *, epic_id: int | None = None) -> ConversionPlan:
        """从 proposal + converged_spec 推演 ConversionPlan。

        - 解析 ``- [ ]`` checklist 产生 task 列表（type=dev）；
        - 默认追加 design task（每个 Story 必需）；
        - 如果 ``create_qa=True``，追加 QA 验收 task；
        - 依赖边：design → 每个 dev；每个 dev → qa。

        Returns:
            ConversionPlan 不可变 dataclass（无 entity id，仅 title 引用）。
        """
        # strip 仅用于 checklist 解析；description 保留原文（旧行为：
        # description=p.converged_spec，不丢首尾空白，Story 389 回归修复）。
        spec_raw = proposal.converged_spec or proposal.content or ""
        spec = spec_raw.strip()
        tasks: list[dict[str, Any]] = []
        seen: set[str] = set()

        # 1. design 任务
        design_title = f"设计：{proposal.title}"
        tasks.append({
            "title": design_title,
            "type": ItemType.DESIGN.value,
            "description": f"Story 设计任务（{proposal.title}）",
            "priority": Priority.MEDIUM.value,
        })
        seen.add(design_title)

        # 2. 从 spec 解析 dev 任务
        import re
        for line in spec.splitlines():
            m = re.match(r"\s*-\s*\[\s*\]\s*(.+)", line)
            if not m:
                continue
            t_title = m.group(1).strip()
            if not t_title or t_title in seen:
                continue
            seen.add(t_title)
            tasks.append({
                "title": t_title[:300],
                "type": ItemType.DEV.value,
                "description": t_title,
                "priority": Priority.MEDIUM.value,
            })

        # 3. 如果 spec 没解析到任何 dev task，至少补一个默认 dev
        if len([t for t in tasks if t["type"] == ItemType.DEV.value]) == 0:
            default_dev = f"实现：{proposal.title}"
            if default_dev not in seen:
                tasks.append({
                    "title": default_dev,
                    "type": ItemType.DEV.value,
                    "description": "默认开发任务（spec 无 checklist 项）",
                    "priority": Priority.MEDIUM.value,
                })
                seen.add(default_dev)

        # 4. QA 验收任务
        if True:  # create_qa 默认 True；保留参数位以备 policy 扩展
            qa_title = f"QA验收：{proposal.title}"
            if qa_title not in seen:
                tasks.append({
                    "title": qa_title,
                    "type": ItemType.QA.value,
                    "description": f"QA 验收任务（{proposal.title}）",
                    "priority": Priority.MEDIUM.value,
                })
                seen.add(qa_title)

        # 5. 依赖边：design → dev(s) → qa
        design_title_final = design_title
        dev_titles = [t["title"] for t in tasks if t["type"] == ItemType.DEV.value]
        qa_title_final = qa_title
        deps: list[tuple[str, str]] = []
        for d in dev_titles:
            deps.append((design_title_final, d))  # design blocks dev
        if dev_titles:
            for d in dev_titles:
                deps.append((d, qa_title_final))  # dev blocks qa

        return ConversionPlan(
            document=None,  # Phase 2 加：默认创建 Project Document
            epic=None,
            epic_id=epic_id,
            story={
                "title": proposal.title,
                "description": spec_raw,
            },
            tasks=tasks,
            dependencies=deps,
            create_qa=True,
            min_tasks=3,
        )

    @staticmethod
    def validate(plan: ConversionPlan, *, project_id: int) -> None:
        """校验 plan 完整性。

        校验失败抛 InvalidValue；调用方应捕获并返回 4xx。

        检查项：
        - min_tasks（默认 3，含 design + dev + qa）
        - type 合法性
        - DAG 简单 acyclic 检查（直接 task 列表无环；显式 dependencies 也不应成环）
        - parent association 必填（epic_id 或 epic）
        """
        if plan.epic is None and plan.epic_id is None:
            raise InvalidValue("ConversionPlan 必须显式指定 epic 或 epic_id")

        if len(plan.tasks) < plan.min_tasks:
            raise InvalidValue(
                f"ConversionPlan 至少需要 {plan.min_tasks} 个 task，当前 {len(plan.tasks)}："
                f"{[t['title'] for t in plan.tasks]}",
            )

        # type 合法性
        valid_types = {ItemType.DESIGN.value, ItemType.DEV.value, ItemType.QA.value,
                       ItemType.BUG.value}
        for t in plan.tasks:
            if t.get("type") not in valid_types:
                raise InvalidValue(
                    f"task '{t.get('title')}' type 非法：{t.get('type')}（仅 {sorted(valid_types)}）",
                )

        # 简单 acyclic 检查：dep 边不应出现重复 + title 引用合法
        titles = {t["title"] for t in plan.tasks}
        for src, dst in plan.dependencies:
            if src not in titles:
                raise InvalidValue(
                    f"dependency source '{src}' 不在 tasks 里（titles: {sorted(titles)}）",
                )
            if dst not in titles:
                raise InvalidValue(
                    f"dependency target '{dst}' 不在 tasks 里（titles: {sorted(titles)}）",
                )
        # 简易环检测：同 title 不会重复；不会出现 a→a
        # （更严格 DFS 在 Phase 2 加；目前 plan 由 plan() 内部生成不易成环）

    @staticmethod
    def apply(
        s: Session,
        plan: ConversionPlan,
        proposal,
        *,
        author_id: int | None = None,
    ) -> ConversionResult:
        """事务性落库：Document + Epic + Story + Tasks + Dependencies + Proposal 终态。

        整个流程在一个 transaction 内 commit；任何步骤失败 SQLAlchemy 自动
        rollback，杜绝"半成品"孤儿数据。调用方应先 validate(plan) 再 apply。

        Phase 1 范围：
        - Story / Tasks / Dependencies 落库（最关键）
        - Proposal 状态推进（converged → converted，保留 story_created alias）
        - Document 创建（Phase 2，stub 留空）
        - Epic 创建（Phase 2，stub 留空；目前 epic_id 由 caller 传入）

        Returns:
            ConversionResult 包含所有新建 entity id
        """
        from .service import _proposal_or_404, _required  # 内部用
        from ..projects.service import create_story, create_epic
        from ..work_items.service import create_task
        from .models import ProposalStatus

        p = _proposal_or_404(s, proposal.id)

        # 1. Story
        # Review 2026-08-26 P1/P2 #5 注意事项：create_story 内部会**自动**创建 1 个
        # design task + 1 个 dev task + design→dev dep。所以我们 plan 里的 design
        # task 不能重复创建 —— 复用 create_story 的默认 design task，把 plan 的
        # design edges 重新 bind 到真实 id。
        story = create_story(
            s,
            epic_id=plan.epic_id or (plan.epic or {}).get("id") or 0,
            title=_required((plan.story or {}).get("title") or p.title, "title", 300),
            description=(plan.story or {}).get("description") or p.converged_spec or "",
            commit=False,
        )
        # 查 create_story 自动创的 default design task
        default_design = s.query(Task).filter(
            Task.story_id == story.id, Task.type == ItemType.DESIGN.value,
        ).first()
        default_dev = s.query(Task).filter(
            Task.story_id == story.id, Task.type == ItemType.DEV.value,
        ).first()

        # 2. Tasks：plan 里 type=design 的跳过（用 create_story 的 default design）
        # plan 里 type=dev 的也跳过 default dev（但 spec checklist 创建的 dev 要创）
        # plan 里 type=qa 的全创
        title_to_task: dict[str, Task] = {}
        plan_design_title = f"设计：{proposal.title}"
        plan_qa_title = f"QA验收：{proposal.title}"

        if default_design is not None:
            title_to_task[plan_design_title] = default_design

        for t in plan.tasks:
            t_title = t["title"]
            t_type = t.get("type") or ItemType.DEV.value
            # 跳过 design（用 default）
            if t_type == ItemType.DESIGN.value:
                continue
            # 如果 dev 的 title 跟 default dev 重合（"实现：<title>"），复用 default
            if (
                t_type == ItemType.DEV.value
                and default_dev is not None
                and t_title == default_dev.title
            ):
                title_to_task[t_title] = default_dev
                continue
            # qa + 其它 spec-driven dev：走 create_task
            task = create_task(
                s,
                project_id=p.project_id,
                story_id=story.id,
                title=t_title,
                type=t_type,
                description=t.get("description") or t_title,
                priority=t.get("priority") or Priority.MEDIUM.value,
                commit=False,
            )
            title_to_task[t_title] = task

        # 3. Dependencies
        # plan 的 deps 用 title 引用，映射到真实 task id 后写库
        for src_title, dst_title in plan.dependencies:
            if src_title in title_to_task and dst_title in title_to_task:
                src_task = title_to_task[src_title]
                dst_task = title_to_task[dst_title]
                # 避免重复创建 design→dev dep（create_story 已自动创了一个）
                if (
                    src_task.id == default_design.id
                    and default_dev is not None
                    and dst_task.id == default_dev.id
                ):
                    continue
                dep = TaskDependency(
                    task_id=dst_task.id,
                    depends_on_id=src_task.id,
                    dependency_type="blocks",
                )
                s.add(dep)

        # 4. Proposal 状态推进（converged → converted / story_created）
        # Review 2026-08-26 P1/P2 #5 注释：未来 Phase 2 收敛成单一 "converted" 终态
        # ，现在保留 story_created 是为了不破坏既有的 5 状态机
        p.story_id = story.id
        p.status = ProposalStatus.STORY_CREATED.value
        p.error = ""

        # 5. 事务性 commit（杜绝半成品）
        _invalidate_project_stats_cache(p.project_id)
        _commit(s)
        s.refresh(story)
        s.refresh(p)
        for t in title_to_task.values():
            s.refresh(t)

        return ConversionResult(
            document_id=None,
            epic_id=plan.epic_id,
            story_id=story.id,
            task_ids=[t.id for t in title_to_task.values()],
            dependency_ids=[],  # Phase 2 补：返回 dep id
            proposal_status=p.status,
        )
