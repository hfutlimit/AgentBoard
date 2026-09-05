# 改动方案：Proposal / Task 的"仅本人 agent 可处理"归属收敛

状态：**已定稿（决策 a/b/c 已确认，待实现，未动代码）**
日期：2026-09-01
范围：backend-fastapi（主要）+ worker 配置；不改 worker C# 代码逻辑（仅配置形态）。

## 1. 目标与归属模型

需求：proposal 及其衍生的 task，相关处理（执行 **和** 评审）只允许"创建者自己的 agent"接手，避免我建的 task 被别人机器的 agent 拿去处理。

归属键 = **user_id**（来自 api key）：

- 一台电脑 = 一个 worker 进程 = 一把 api key = 一个 user。
- 一个 worker 可配置多个 agent；一个人可在多台机器装 worker——只要**共用同一把 api key**，其名下所有 agent 的 `Agent.user_id` 都相同。
- 服务器侧已在 agent 注册时用请求 api key 解析 user：`register_agent(..., user_id=uid)`（`scheduling/router.py:303` → `_caller_uid_admin`）。所以"同 key = 同 user"天然成立，**归属判定不需要新鉴权**。

判定规则统一为：
```
可处理 ⇔ processing Agent.user_id == work_item.owner_user_id
```
执行、评审、认领、仲裁、proposal 认领全部套用。

## 2. 与现有 reviewer isolation 的冲突处理（本方案的前提）

现状是"评审必须用**不同 user** 的 agent"：
- `assign_task_reviewer` 过滤 `a.user_id != t.assignee_id`（`scheduling/service.py:685`）
- `assign_reviewer` `a.user_id != exclude_user_id`（`:2343`）
- P0-1 给同机 `wb-main`/`codex-main` 配**各自不同 token→不同 user_id**，正是为此。

新需求把评审也收进"同 owner"，与上述直接冲突。处理决策：

- **退休跨用户隔离**：评审候选池从 `user_id != assignee` 改为 `user_id == owner`。
- **自审保护降级到 agent 维度**（避免同一个 agent 实例实现完自己批）：在同一 owner 下，评审候选要求 `agent_id != 实现方 agent_id`（§3 的 `created_by_agent_id`/执行记录）。**决策 b**：若该 owner 名下除了实现方没有第二个可用 agent，则**评审保持待处理**（不自审、不指派），等 owner 再注册/上线一个同 owner agent。
- P0-1 的 per-agent 不同 token 在"单 owner 部署"下不再需要；改为**整台 worker 共用一把 key**（见 §7）。跨用户协作/happy-path 演示若要保留，需用**两把 key / 两个 user 的 worker** 来体现"不同 owner"。

## 3. 数据模型（核心前置）

- **Task**（`work_items/models.py:37-92`）：现**无创建者列**。新增 `created_by_user_id`（FK `users.id`，nullable + index）作为 owner。再加 `created_by_agent_id`（记录创建方 agent；§5 评审"排除实现方"需要它）。
- **Story**：**本次不纳入**（决策 a：收敛只做在 Task 级，Task 足够）。Story 的认领/派发不加 owner 校验。
- **Proposal**：已有 `author_id`（`proposals/models.py:185`），复用为 owner，不再加列。
- 迁移：一次 Alembic `add_column`；`server_default=NULL`。

存量数据（决策 c）：老 Task 的 `created_by_user_id` 留 `NULL`，**不**回填成公共、**不**自动放行。因 §5 门槛要求 owner 匹配，NULL owner 的 task 会**匹配不到任何 agent → 保持待处理、不可认领/派发**，直到人工补 owner。配套：提供"列出 owner 为空 Task"的查询 + 一个运维补 owner 的入口（复用 `update_task` 或加一个 admin 端点）。

## 4. 写入路径（保证有 owner 可比）

在**每一处创建 Task** 的入口写 `created_by_user_id`：
1. REST/MCP 建 task：用 `resolve_actor_context(...).user_id`（`api_helpers.py:132-177`）→ 即调用方 api key 的 user。
2. Proposal 转换：`conversion_service.apply → create_task`（`proposals/conversion_service.py:245,294`）当前不写 owner → 改为继承 `Proposal.author_id`；story 同理继承 proposal 的 author。
3. `generate_tasks_from_spec` 等批量入口：owner 取源 story/proposal 的 author。

## 5. 强制门槛（逐口加 `user_id == owner`）

| # | 入口 | 位置 | 现门槛 | 加什么 |
|---|---|---|---|---|
| 1 | 执行派发候选池 | `list_runnable_candidates`(~898)/`_pick_implementation_agent`(954)/`dispatch_implementation_task`(998) | 仅 `agent.user_id ∈ project members` ∩ executor 能力 | 过滤 `agent.user_id == task.created_by_user_id`；命中不了走 §6 兜底 |
| 2 | 认领开发任务 | `claim_development_task`（`work_items/service.py:558` 与 `scheduling/service.py:1597` **两份重复实现**） | 仅 `status==TODO` CAS | 认领前校验 `agent.user_id == created_by_user_id`，否则 403；**两处都要改并去重** |
| 3 | 申请/仲裁 | `apply_for_task`(606)/`arbitrate_task`(672) | 从任意 user 申请里选最高分 | 候选限定为 owner 的 agent；跨 owner 申请直接拒 |
| 4 | Proposal 认领 | `claim_proposal`(`proposals/service.py:192`) + router(`proposals/router.py:206`) | **router 无鉴权**，忽略 `author_id` | router 补 `actor` 依赖；claim 校验 `actor.user_id == Proposal.author_id` |
| 5 | 评审指派 | `assign_task_reviewer`(685)/`assign_reviewer`(2343) | `user_id != assignee`（跨用户） | 改为 `user_id == owner` 且 `agent_id != 实现方`；无第二个同 owner agent → 保持待处理（决策 b） |

## 6. 派发兜底（Q3：保持待处理）

owner 名下没有匹配的合格 agent（executor_type/能力/在线）时：
- **不派给别人**，`task.status` 保持 `todo`，不进入 `in_progress`，不 publish `task.assigned`。
- 记一条可观测日志/事件（如 `dispatch: no owner agent for task %s, owner=%s`）便于排查；后续可接通知提醒 owner 上线 worker 或注册 agent。

## 7. Worker 配置形态（不改 C# 代码）

- 生产"单 owner 机"部署：`Agents.*.AgentBoardToken` **留空**，全部回退到 `AgentBoard.StartupToken`（同一把 key）→ 服务器看到这些 agent 同属一个 user。`qwen_invoker.py` 等仍照常。
- 需要演示"两个不同 owner 协作/评审"：用**两台 worker 各一把 key**，而不是单机两个 token（后者会被 §2 退休隔离后失去跨用户语义）。
- 一台机器一个 worker 进程（现有约束）：由部署保证；`Worker.Id` 每机唯一。

## 8. 测试影响

现有 happy-path / reviewer-fanout 用例假设**跨用户评审**（task 1654 里 assignee_id=16 vs reviewer=15）。退休隔离后：
- `tests/unit/test_dispatch_implementation_pr10.py`、`test_sprint12_reviewer_fanout.py`、`tests/e2e/happy_path/*` 需按"同 owner + agent 去重"重设预期。
- 新增用例：跨 owner 认领应 403；无 owner agent 应保持 todo；proposal 认领鉴权。

## 9. 落地拆分（建议顺序）

1. 迁移：Task 加 `created_by_user_id` + `created_by_agent_id`（Story 不动）。
2. 写入路径 4 处补 owner。
3. 执行侧最小闭环：门槛 1、2（claim 两份去重后统一）、4（proposal router 鉴权）+ 兜底 6。
4. 评审侧：门槛 5 + §2 自审子策略 + 回退 P0-1 per-agent token（配置层面）。
5. 测试与迁移回填。

粗估：1 迁移 + ~4 写入点 + ~5 门槛函数 + proposal router 鉴权 + 一批测试；**中等改动**，集中在 `scheduling/service.py`、`work_items/service.py`、`proposals/{router,service,conversion_service}.py`、`projects/models.py`、`work_items/models.py`。

## 10. 已确认决策（2026-09-01）

- **(a) 范围只到 Task 级**：收敛只做在 Task；Story 不纳入，不加 owner 校验。
- **(b) 评审同 owner，且无第二个同 owner agent 时保持待处理**：不自审、不指派，等 owner 再注册/上线一个同 owner agent。
- **(c) 存量无 owner 的 Task 冻结待人工补**：`created_by_user_id` 留 NULL → 匹配不到 agent → 保持待处理，需人工补 owner 才可被处理。
