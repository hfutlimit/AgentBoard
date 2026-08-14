"""StoryHandler：Story 执行编排域（Epic 123 Step 2）。

职责：
1. confirmed Story 的发现 → CAS 认领 → 上下文（Story + 其下任务）→
   agent 决策（story_handled / fail）→ 收尾 done / 回退 confirmed / blocked；
2. Task 竞争认领与定向处理（MQ 广播 task.available / 定向 task.assigned）。

对应原 ``worker.py`` 中 ``fetch_confirmed_stories`` / ``build_story_context`` /
``_story_comment`` / ``_set_story_status`` / ``_complete_story`` /
``_claim_story`` / ``_unclaim_story`` / ``_story_all_tasks_done`` /
``handle_story`` / ``_story_fail`` / ``build_task_context`` / ``_task_comment`` /
``_process_task`` / ``handle_task_available`` / ``handle_direct_task`` /
``handle_workflow_message``。
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from agentboard import mq
from ..config import ACTION_STORY_HANDLED, AgentDecision

log = logging.getLogger("agentboard.worker.story")


def build_story_prompt(context: dict) -> str:
    """Story 执行模式提示词（Story 265 收敛后，5 状态流）。

    指示 agent 经 AgentBoard MCP 推进 Story 下 task 的下一步：
    - 铁律一：所有 task 走通用 5 状态流 todo→in_progress→in_review→done；
      design 任务不再有独立评审段（评审段已并入通用流）；
    - 铁律二：实现 task 须走 in_progress → in_review（提交评审）→ 评审通过 → done；
    - 铁律三：set_status 到 done/blocked 必须传 status_reason（done: completed/withdrawn；
      blocked: blocked_by_other_ticket/pending_requirement_change/out_of_scope/duplicate）；
    - 铁律四：re-open done → in_progress 时 status_reason 自动清空；
    - 每完成一个里程碑，同步用 MCP 的 update_story 推进 Story 状态
      （设计完成 → todo；开发中 → in_progress；评审 → in_review；全 done → done）；
    - 一次调用尽量推进所有当前可推进的步骤；全部完成后打印 story_handled。
    """
    story_id = context.get("story_id")
    tasks = context.get("tasks") or []
    lines = [
        "你是软件开发执行 Agent。下面的 Story 已被用户确认，请经 AgentBoard MCP 自动推进其下任务。",
        "",
        "## 执行铁律（必须严格遵守）",
        "1. **状态流（Story 265 收敛）**：所有 task 走通用 5 状态流 "
        "todo → in_progress → in_review → done；设计评审段已下线，"
        "design 任务与 dev/qa/bug 走完全相同的流；",
        "2. **实现任务流程**：in_progress（开发）→ in_review（用 submit_task_for_review "
        "提交评审）→ 评审通过 → done；",
        "3. **status_reason 强制**：set_status 到 done 必须传 completed/withdrawn；"
        "到 blocked 必须传 4 选 1（blocked_by_other_ticket / pending_requirement_change / "
        "out_of_scope / duplicate）；其他状态忽略；",
        "4. **re-open done**：done → in_progress 时 status_reason 自动清空（无需手动传）；",
        "5. 每个里程碑完成后，用 MCP `update_story` 同步推进 Story 状态"
        "（开发中→in_progress，评审中→in_review，全部完成→done）；",
        "6. 一次调用内尽量推进所有当前可推进的步骤；无需等待外部人工输入。",
        "",
        "## 决策协议（必须严格遵守）",
        "全部可推进步骤完成后，在输出最后打印 JSON：",
        '{"action":"story_handled","summary":"本轮完成的工作"}',
        "若无法继续（缺 MCP 连接 / 依赖缺失 / 需求不清晰等），打印：",
        '{"action":"fail","error":"原因"}',
        "",
        f"## Story #{story_id}：{context.get('title')}",
        "",
        str(context.get("description") or "(无描述)"),
        "",
        f"## needs_design: {context.get('needs_design')}",
        "",
        "## 当前任务列表（经 MCP list_tasks 也可获取最新状态）",
    ]
    for t in tasks:
        lines.append(
            f"- [{t.get('type')}] #{t.get('id')} {t.get('title')} status={t.get('status')}"
            f"{' reviewer=' + str(t.get('reviewer_id')) if t.get('reviewer_id') else ''}"
        )
    return "\n".join(lines)


def build_task_prompt(context: dict) -> str:
    """单 Task 执行模式提示词（MQ 竞争/定向编排，2026-08-09）。"""
    task = context.get("task") or {}
    lines = [
        "你是软件开发执行 Agent。下面这个任务已分配给你（竞争认领成功或指定指派），"
        "请经 AgentBoard MCP 把它推进到完成。",
        "",
        "## 执行要点（必须严格遵守）",
        "1. 任务状态已由 Worker 置 in_progress（开发中）；",
        "2. design 类任务（needs_design=true 的 Story 下 type=design）：推进 "
        "in_design → design_pending_review → design_review_approved（评审流）；",
        "3. 实现任务：开发完成后用 MCP `submit_task_for_review` 提交评审（in_review），"
        "评审通过 → done，必要时 verifying（测试）；",
        "4. 若任务已 done 或被他人处理，直接报告完成即可，不要重复操作。",
        "",
        "## 决策协议（必须严格遵守）",
        "处理完成（或确认无需处理）后，在输出最后打印 JSON：",
        '{"action":"story_handled","summary":"本轮完成的工作"}',
        "若无法继续（缺 MCP 连接 / 依赖缺失 / 需求不清晰等），打印：",
        '{"action":"fail","error":"原因"}',
        "",
        f"## Task #{task.get('id')}：{task.get('title')}",
        "",
        f"- type: {task.get('type')} | 当前状态: {task.get('status')}",
        f"- 所属 Story: #{context.get('story_id')}（needs_design={context.get('needs_design')}）",
        f"- assignee: {context.get('assignee_id')}",
        "",
        str(task.get("description") or task.get("spec") or "(无描述)"),
    ]
    return "\n".join(lines)


class StoryHandler:
    """Story 执行编排 Handler（confirmed → 推进 task → done / blocked）。"""

    name = "story"
    valid_actions = {ACTION_STORY_HANDLED, "fail"}

    def __init__(self, client: httpx.Client, config: Any):
        self.client = client
        self.config = config
        # 编排节流与失败计数（进程内，重启重置可接受）
        self._story_attempts: dict[int, float] = {}      # story_id → 上次拉起时间戳
        self._story_fail_counts: dict[int, int] = {}     # story_id → 连续失败次数
        self._story_min_interval: float = 30.0           # 同一 Story 最小拉起间隔（秒）

    # ---------- HTTP 辅助 ----------

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.client.request(method, path, **kw)

    def _get_json(self, path: str, **kw) -> Any:
        r = self._request("GET", path, **kw)
        r.raise_for_status()
        return r.json()

    # ---------- Handler 协议 ----------

    def can_handle(self, work_item: dict) -> bool:
        return bool(work_item.get("story_id") and "tasks" in work_item)

    def fetch(self) -> list[dict]:
        """拉取待处理的 Story（status=confirmed，用户已确认的人工闸门）。"""
        try:
            data = self._get_json("/api/stories", params={
                "status": "confirmed", "limit": max(1, self.config.batch_size),
            })
            return (data or {}).get("items", []) or []
        except Exception as e:
            log.warning("拉取 confirmed Story 失败：%s", e)
            return []

    def claim(self, work_item: dict) -> bool:
        """竞争认领：POST /api/stories/{sid}/claim（CAS confirmed→todo）。"""
        sid = work_item.get("id")
        try:
            r = self._request("POST", f"/api/stories/{sid}/claim")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 认领异常：%s", sid, e)
            return False

    def load_context(self, work_item: dict) -> dict:
        """Story 全量重放 + 其下任务列表（供执行模式提示词）。"""
        sid = work_item.get("id")
        tasks = self._get_json(f"/api/stories/{sid}/tasks", params={"limit": 200})
        # Story 243：project_id 经 epic 反查（Story 对象只有 epic_id，epic 才挂项目）
        project_id = None
        epic_id = work_item.get("epic_id")
        if epic_id:
            try:
                epic = self._get_json(f"/api/epics/{epic_id}")
                project_id = (epic or {}).get("project_id") or epic_id
            except Exception as e:
                log.warning("Story #%s 反查 epic #%s 失败（回退 epic_id）：%s", sid, epic_id, e)
                project_id = epic_id
        return {
            "action": "process_story",
            "story_id": sid,
            "project_id": project_id,
            "epic_id": epic_id,
            "title": work_item.get("title"),
            "description": work_item.get("description") or "",
            "needs_design": bool(work_item.get("needs_design", True)),
            "status": work_item.get("status"),
            "tasks": (tasks or {}).get("items", []) if isinstance(tasks, dict) else (tasks or []),
        }

    def build_prompt(self, context: dict) -> str:
        """Story 执行模式提示词（委托模块级 build_story_prompt）。"""
        return build_story_prompt(context)

    def handle_decision(self, work_item: dict, decision: AgentDecision,
                        context: dict) -> str:
        """落决策：story_handled → 收尾/交接；fail → 失败计数 + 回退/blocked。"""
        sid = work_item.get("id")
        if decision.action == ACTION_STORY_HANDLED:
            # 本轮执行成功：节流清零，继续扫描（若任务未全完成则交接下轮继续）
            self._story_fail_counts.pop(sid, None)
            if self._story_all_tasks_done(work_item):
                ok = self._complete_story(sid)
                log.info("Story #%s 全部任务完成，自动收尾 done=%s", sid, ok)
                return "handled" if ok else "failed"
            # 部分推进：unclaim 回退 confirmed，交接给下轮/其它实例
            ok = self._unclaim_story(sid)
            log.info("Story #%s 本轮推进完成（任务未全部完成），回退 confirmed=%s", sid, ok)
            return "handled"
        # agent 主动放弃
        return self._story_fail(sid, decision.error or "Agent 未报告完成原因")

    # ---------- Story 编排细节 ----------

    def handle(self, story: dict, invoker) -> str:
        """处理一个 confirmed Story：竞争认领 → 拉起 agent 推进其下任务。

        多 Worker 竞争模型：claim（CAS 恰一赢家）→ agent 推进 →
        story_handled + 全 done → complete；部分推进 → unclaim 交接；
        失败计数 3 次 → blocked。返回：skipped / handled / blocked / failed。
        """
        sid = story.get("id")
        if sid is None:
            return "skipped"
        now = time.time()
        last = self._story_attempts.get(sid, 0.0)
        if now - last < self._story_min_interval:
            return "skipped"
        self._story_attempts[sid] = now
        if not self.claim(story):
            log.info("Story #%s 认领失败（其它 Worker 已处理或状态不可认领），跳过", sid)
            return "skipped"
        try:
            context = self.load_context(story)
        except Exception as e:
            log.exception("Story #%s 构建上下文失败", sid)
            self._story_comment(sid, f"Worker 构建上下文失败：{e}")
            return self._story_fail(sid, f"构建上下文失败：{e}")
        try:
            decision = invoker.invoke(context)
        except Exception as e:
            log.warning("Story #%s Agent 调用失败：%s", sid, e)
            return self._story_fail(sid, str(e))
        return self.handle_decision(story, decision, context)

    def _story_comment(self, story_id: int, content: str) -> None:
        """在 Story 上落一条执行记录评论（失败原因/进展，审计载体）。"""
        try:
            self._request("POST", f"/api/stories/{story_id}/comments",
                          json={"author": self.config.agent, "content": content[:2000]})
        except Exception as e:
            log.warning("Story #%s 评论失败：%s", story_id, e)

    def _set_story_status(self, story_id: int, status: str) -> bool:
        try:
            r = self._request("PATCH", f"/api/stories/{story_id}",
                              json={"status": status})
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 置 %s 失败：%s", story_id, status, e)
            return False

    def _complete_story(self, story_id: int) -> bool:
        """Story 自动收尾：POST /api/stories/{sid}/complete（任意非 done/blocked → done）。"""
        try:
            r = self._request("POST", f"/api/stories/{story_id}/complete")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 自动收尾失败：%s", story_id, e)
            return False

    def _unclaim_story(self, story_id: int) -> bool:
        """认领交接/失败回退：POST /api/stories/{sid}/unclaim（CAS todo→confirmed）。"""
        try:
            r = self._request("POST", f"/api/stories/{story_id}/unclaim")
            return r.status_code in (200, 201)
        except Exception as e:
            log.warning("Story #%s 回退异常：%s", story_id, e)
            return False

    def _story_all_tasks_done(self, story: dict) -> bool:
        """Story 下任务是否全部完成（收尾判据，Story 265 收敛后）。

        - 所有 task（含 design/dev/qa/bug）：终态统一为 done；
        - 设计评审段已下线，design 任务走通用 todo→in_progress→in_review→done 流。
        """
        sid = story.get("id")
        try:
            data = self._get_json(f"/api/stories/{sid}/tasks", params={"limit": 200})
            tasks = (data or {}).get("items", []) if isinstance(data, dict) else (data or [])
        except Exception as e:
            log.warning("Story #%s 回查任务失败：%s", sid, e)
            return False

        def finished(t: dict) -> bool:
            return t.get("status") == "done"

        pending = [t for t in tasks if not finished(t)]
        return not pending

    def _story_fail(self, sid: int, error: str) -> str:
        """Story 处理失败：评论 + 计数 + 回退 confirmed 重试；连续 3 次 → blocked。"""
        self._story_comment(sid, f"Agent 自动处理失败：{error}")
        count = self._story_fail_counts.get(sid, 0) + 1
        self._story_fail_counts[sid] = count
        if count >= 3:
            self._story_fail_counts.pop(sid, None)
            ok = self._set_story_status(sid, "blocked")
            log.warning("Story #%s 连续 %s 次失败，置 blocked 转人工（%s）", sid, count, ok)
            return "blocked"
        # 未达上限：unclaim 回退 confirmed，重新入池待重试
        ok = self._unclaim_story(sid)
        log.info("Story #%s 失败（第 %s 次），回退 confirmed 待重试（%s）", sid, count, ok)
        return "failed"

    # ---------- Task 竞争/定向处理（MQ 编排） ----------

    def build_task_context(self, task: dict) -> dict:
        """单 Task 上下文：task + 所属 Story 摘要（needs_design 决定走哪条执行流）。"""
        story_id = task.get("story_id")
        needs_design = True
        if story_id:
            try:
                story = self._get_json(f"/api/stories/{story_id}")
                needs_design = bool(story.get("needs_design", True))
            except Exception:
                pass
        return {
            "action": "process_task",
            "task": task,
            "story_id": story_id,
            "needs_design": needs_design,
        }

    def build_task_prompt(self, context: dict) -> str:
        """单 Task 执行模式提示词（委托模块级 build_task_prompt）。"""
        return build_task_prompt(context)

    def _task_comment(self, task_id: int, content: str) -> None:
        try:
            self._request("POST", f"/api/tasks/{task_id}/comments",
                          json={"author": self.config.agent, "content": content[:2000]})
        except Exception as e:
            log.warning("task#%s 评论失败：%s", task_id, e)

    def process_task(self, task_id: int, task: dict, invoker) -> bool:
        """拉起 agent 推进单个 task（认领/定向后）：构建上下文 → invoke → 落评论。"""
        try:
            context = self.build_task_context(task)
        except Exception as e:
            log.exception("task#%s 构建上下文失败", task_id)
            return False
        try:
            decision = invoker.invoke(context)
        except Exception as e:
            log.warning("task#%s Agent 调用失败：%s", task_id, e)
            self._task_comment(task_id, f"Agent 自动处理失败：{e}")
            return True  # ack：失败留评论，task 停留当前态（人工/轮询兜底）
        if decision.action == ACTION_STORY_HANDLED:
            log.info("task#%s 本轮处理完成", task_id)
            return True
        self._task_comment(task_id, decision.error or "Agent 未报告完成原因")
        return True

    def handle_task_available(self, msg: "mq.WorkflowMessage", invoker) -> bool:
        """广播 task.available 竞争处理：回查 → CAS 认领（claim）→ 拉起 agent。"""
        tid = msg.entity_id
        try:
            task = self._get_json(f"/api/tasks/{tid}")
        except Exception as e:
            log.warning("task.available 回查 task#%s 失败：%s", tid, e)
            return False  # 转死信（轮询兜底会再捞）
        if task.get("status") not in ("backlog", "todo"):
            return True  # 已被处理/认领
        try:
            r = self._request("POST", f"/api/tasks/{tid}/claim")
        except Exception as e:
            log.warning("task#%s 认领异常：%s", tid, e)
            return False
        if r.status_code == 409:
            return True  # 竞争失败：他人已认领
        if r.status_code not in (200, 201):
            log.warning("task#%s 认领失败：%s %s", tid, r.status_code, r.text[:120])
            return True
        log.info("task#%s 竞争认领成功（广播轮）", tid)
        return self.process_task(tid, r.json(), invoker)

    def handle_direct_task(self, msg: "mq.WorkflowMessage", invoker) -> bool:
        """定向任务（task.assigned 投递到本 agent 的 direct queue）：回查后处理。"""
        tid = msg.entity_id
        try:
            task = self._get_json(f"/api/tasks/{tid}")
        except Exception as e:
            log.warning("定向 task#%s 回查失败：%s", tid, e)
            return False
        if task.get("status") not in ("backlog", "todo", "in_progress"):
            return True  # 已结束/不可处理
        log.info("task#%s 定向任务（direct queue）处理", tid)
        return self.process_task(tid, task, invoker)

    def handle_workflow_message(self, msg: "mq.WorkflowMessage", invoker) -> bool:
        """Workflow 事件分发（Agent MQ 消费）：广播竞争 + 定向任务。"""
        if msg.event == mq.EVENT_TASK_AVAILABLE:
            return self.handle_task_available(msg, invoker)
        if msg.event == mq.EVENT_TASK_ASSIGNED:
            return self.handle_direct_task(msg, invoker)
        log.info("Agent 忽略非任务事件 %s（entity=%s#%s）",
                 msg.event, msg.entity_type, msg.entity_id)
        return True
