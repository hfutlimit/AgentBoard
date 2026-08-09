# Design: Ticket 全流程 —— Story 工单化 + Agent 自动处理 + Agent 池可见性/心跳

## 1. 现状（调研结论，文件:行号）

- **Task 强制迁移**：`service.py:55-87` `TRANSITIONS` + `_DESIGN_SEGMENT` + `transitions_for(needs_design)`；`set_status`（994-1026）单步查表 + blocked 全向特判 + previous_status 恢复；`batch_update_task_status`（2875-2899）同款。
- **设计流约束**：`_task_needs_design`（969-974）；needs_design=true 时 todo 出边改为 `{IN_DESIGN,BACKLOG,DONE,BLOCKED}`（`service.py:84`）——「先 design 后实现」约定**已强制实现**，无需新开发。
- **Story 评审闭环接触点**（本次移除/废弃）：`service.py` update_story(372)/assign_reviewer(699)/review_story(736)/list_review_tasks(801)/_vote_majority 族(585-679)/_reassign_story_reviewer(1230)/scan_review_timeouts(1285)/get_review_stats(1420)；`api.py` GET /api/stories 的 reviewer 过滤(910)/assign-reviewer(949)/review(973)/review-stats(1341)/reassign-timeout(1360)；`mcp_server.py` review_story(1748)/list_review_tasks(1760)/get_review_stats(1830)/scan_review_timeouts(1847)；`workflow_worker.py` story.created→_assign_reviewer(133-151)、story.ready→_broadcast_available_tasks(177-200)。
- **Agent 基础设施已齐**：service register_agent(415)/agent_heartbeat(465)/agent_deregister(479)/list_agents(491)；api `/api/agents/*`(1014-1054)；mcp agent_* 4 工具(1698-1739)；agents 表含 `cli_command/online/last_heartbeat`（models.py:92-113）。**worker 无心跳探测；前端无 Agent 视图**。
- **proposal worker/MQ 模板**：`ProposalWorker`(448)；fetch/claim/build_ticket_context/handle_ticket_request(752-851)；`_build_ticket_prompt`(332-369)；`run_mq_forever`(1032-1086)+`_ticket_scan_loop`(1014)+`_maintenance_loop`(1003)；mq.py 事件白名单(679-692)；`SubprocessAgentInvoker`(390-426，env `AGENTBOARD_WORKER_AGENT_CMD`)。

## 2. Story 状态机（切片 1）

### 2.1 状态集合（9→8）

```python
# domains/projects/models.py
STORY_REVIEW_STATUSES = set()            # 移除（原 {"pending_review","ready"}）
STORY_STATUS_SQL = "status IN ('backlog','confirmed','todo','in_progress','in_review','verifying','done','blocked')"
```

### 2.2 强制迁移

```python
# service.py
STORY_TRANSITIONS: dict[str, set[str]] = {
    "backlog":      {"confirmed", "blocked"},
    "confirmed":    {"todo", "blocked"},          # 确认后进入执行；确认动作由专用端点触发
    "todo":         {"in_progress", "backlog", "blocked"},
    "in_progress":  {"in_review", "todo", "blocked"},
    "in_review":    {"verifying", "done", "in_progress", "blocked"},
    "verifying":    {"done", "in_progress", "blocked"},
    "done":         {"in_progress", "todo", "blocked"},
    "blocked":      {"todo", "in_progress"},      # 无 previous_status，解除仅限这两个
}
```

- `set_story_status(s, id, *, status, changed_by=None, reason=None)`：单步查表（复用 `_check_transition` 逻辑）+ blocked 全向特判 + 写 `story_status_history` + 发 MQ `story.status_changed`（含 from/to）；
- `update_story`（service.py:372）的 status 校验从「取值校验」升级为「取值 + 迁移校验」（`backlog→confirmed` 仍允许走 PATCH，等价确认；专用 confirm 端点负责触发 MQ 编排）；
- **确认触发**：`POST /api/stories/{sid}/confirm`（service `confirm_story`）：CAS `status=backlog → confirmed`，成功后发 MQ `story.confirmed`；已 confirmed 幂等返回。

### 2.3 状态历史

```python
class StoryStatusHistory(Base):
    __tablename__ = "story_status_history"
    id: int PK
    story_id: FK stories.id, index
    from_status: str(40), to_status: str(40)
    changed_by: str(100) nullable    # username / agent_id
    reason: str(500) nullable
    created_at: DateTime
```

- 与 `task_status_history` 同构，独立表不动存量；`GET /api/stories/{sid}/status-history` 复用列表查询模式。

### 2.4 评审态移除与废弃面

- `assign_reviewer`（story 分支）/`review_story`（story 分支）/`list_review_tasks`（story 过滤）/`_reassign_story_reviewer`/`_vote_majority`（entity_type=story 分支）/`scan_review_timeouts`（story 分支）/`get_review_stats`（story 统计）→ **移除或改为仅 Task**；
- `POST /api/stories/{sid}/assign-reviewer`、`POST /api/stories/{sid}/review` → 保留端点但返回 400「Story 评审已下线，评审在 Task 层进行」；MCP `review_story` 同样返回错误（**契约只增不删**，避免连接方侧未知工具报错）；
- `workflow_worker.py`：移除 story.created→assign_reviewer 与 story.ready→broadcast；新增 story.confirmed→广播可用 Task（设计 task 先行）。
- 前端 `statusLabel/statusColor/statusSemanticClass` 三张映射移除 `pending_review/ready`（app.ts:3816-3845），新增 `confirmed` 映射；review-ops-panel 移除 Story 区块（app.html:784-903）。

## 3. Agent 自动处理编排（切片 2）

### 3.1 MQ 事件

`mq.py` 白名单新增：`story.confirmed`、`story.status_changed`（消息仅带 `story_id`，状态一律回查 DB，沿用 mq.py:14-24 定位信息原则）。

### 3.2 worker 消费

```
handle_story_confirmed(story_id):
  st = get_story(story_id)                    # 回查，不存在/非 confirmed → 幂等丢弃
  tasks = list_tasks(story_id)                # design/实现 task 全量
  ctx = build_story_context(st)               # Story 描述 + tasks + needs_design
  invoker = SubprocessAgentInvoker(cmd=os.environ["AGENTBOARD_WORKER_AGENT_CMD"])
  out = invoker.invoke(ctx)                    # 转化模式提示词（新增 _build_story_prompt）
  _confirm_agent_output(out)                   # 轮询回查 Story 状态进展（复用 _confirm_ticket 模式）
```

- `_build_story_prompt(ctx)`：指示 agent 用 AgentBoard MCP 按序推进：① design task：`in_design → design_pending_review → design_review_approved`（自评/调用评审人）② 实现 task：`in_progress → in_review`（提交评审）→ 评审通过 → done ③ 测试（verifying → done）；**约定铁律：design task 未 approved 前不得推进实现 task**（服务端 `_task_needs_design` 已强制，提示词再强调）；
- 幂等：agent 每次 MCP 调用都是服务端事务内校验（复用现有 `set_status`/`claim_development_task`/`submit_task_for_review`/`review_task`），worker 不持有中间状态；`story.confirmed` 消息 at-least-once，重复消费仅重复拉起 agent（agent 侧幂等：目标状态已达成则直接返回确认 JSON）；
- 失败兜底：invoker 异常/超时/无有效 JSON → 记录 `story` 评论（错误原因）+ 日志告警，**不回退 Story 状态**（人工可在前端重试/手动推进，避免 proposal 式回退的复杂度）；可选 job 重试沿用 `auto_retry_count` 模式（设计阶段不引入，作后续切片）。

## 4. Worker 心跳探测（切片 3）

```python
# worker.py 新增 _agent_heartbeat_loop（挂入 run_mq_forever 的 _maintenance_loop 同层）
async def agent_heartbeat_once():
    agents = api_get("/api/agents")                    # 全量
    for a in agents:
        cmd = a.get("cli_command") or os.environ.get("AGENTBOARD_WORKER_AGENT_CMD", "")
        if not cmd:
            continue                                   # 无 CLI 命令 → 依赖 agent 自报心跳
        try:
            subprocess.run([cmd, "--version"], timeout=8, capture_output=True)
            api_post(f"/api/agents/{a['agent_id']}/heartbeat")   # 置 online + last_heartbeat
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
            api_post(f"/api/agents/{a['agent_id']}/deregister")  # 置 offline（保留记录）
```

- 周期 60s；`cli_command` 含参数的兼容：用 `shlex.split(cmd)` 解析后拼接探测参数（`--version`/`--help` 兜底，探测失败视为不可用）；
- 探测异常只记日志，不抛入主循环（try/except 包住单 agent）；
- 无 `cli_command` 的 agent：保留自报心跳路径（MCP `agent_heartbeat` 不受影响），`last_heartbeat` 超过阈值（如 5min）的由本循环一并置 offline（**可选项**，MVP 先跳过，避免误杀）。

## 5. 前端（切片 4）

### 5.1 Agent 池面板

- 入口：侧栏「Agent」或工作台 Tab（跟随现有导航风格）；数据 `GET /api/agents`；
- 卡片列表：name + agent_id + roles 标签（reviewer/designer/developer…）+ online 徽标 + last_heartbeat 相对时间（`timeAgo` 复用）；数量统计卡（总数/在线/离线）；
- models.ts 新增 `AgentRow` 接口（对齐后端 `_ser` Agent 输出）。

### 5.2 Story 确认与状态

- Story 详情（app.html:1372-1390 区域）：backlog 时显示「确认开始」主按钮 → `POST /api/stories/{sid}/confirm` → 成功后状态变 confirmed，toast 提示「已触发 Agent 自动处理」；
- 状态 chips：`statusLabel/statusColor/statusSemanticClass`（app.ts:3816-3845）增 confirmed（label「待确认开始」/ 色 amber / 语义 warning）、删 pending_review/ready；
- 状态历史：Story 详情「状态历史」折叠区，`GET /api/stories/{sid}/status-history` 时间线渲染（复用评论时间线样式）。

### 5.3 评审运营面板

- review-ops-panel（app.html:784-903）移除 Story 统计卡/Story 工作量条，保留 Task 侧；`ReviewStats` 接口 story 字段标记可选并置空。

## 6. 契约与兼容

- REST：新增 `POST /api/stories/{sid}/confirm`、`GET /api/stories/{sid}/status-history`、`GET /api/agents`（已有，前端新增消费）；`assign-reviewer`/`review` 保留返回 400；`review-stats` 结构不删字段（story 侧恒 0/空）；
- MCP：新增 `story_confirm`/`story_status_history`（可选）；`review_story` 保留返回错误；`list_agents` 不变；
- 迁移：`w_story_ticket_flow`（models 变更 + 新表 + 存量 `pending_review/ready` → `blocked`/`confirmed` 数据映射：`pending_review→todo`（评审职责已转移）、`ready→confirmed`（评审通过等价于确认可执行））——SQLite batch_alter + MariaDB 双路径；
- 权限：全部走 `project_access_middleware`；`confirm` 需成员；agent 推进沿用现有 Task 权限。

## 7. 测试计划

| 文件 | 覆盖 |
|---|---|
| tests/test_story_status_machine.py | 迁移校验/blocked 特判/历史记录/confirm CAS/废弃端点 400 |
| tests/test_story_confirm_flow.py | confirm→MQ→worker 编排→agent 推进幂等（InMemoryBroker + 假 invoker） |
| tests/test_worker_heartbeat.py | 探测判活/超时降级/无 cli_command 跳过/异常不阻塞 |
| tests/test_epic122_* 改造 | Story 评审用例迁至 Task 或移除；workflow_events 适配 |
| 前端 vitest + E2E | Agent 池渲染/Story 确认按钮/chips 映射 |
