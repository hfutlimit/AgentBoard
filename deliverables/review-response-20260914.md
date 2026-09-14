# 外部 Review 回应 · AgentBoard · 2026-09-14

**评审来源**：外部（架构级，基于 GitHub commit 页面截图，作者自述"不能直接展开 diff"）
**评审对象**：`origin/main` 顶部 7 条提交（`34a2c02` 起往回 7 条）
**本回应基线**：`origin/main` = `34a2c023`；本地 `main` = `14ea2a6`
**方法**：逐条对照真实 diff / 源码行号 / 迁移图实测 / 反向测试验证

---

## 0. 三条结论先行

1. **评审方向对，但大量技术论断指向"已经存在的东西"**。7 条里 3 条是"要求补一个已实现的模块"，2 条基于对提交标题的误读。
2. **评审最正确的直觉是"heartbeat 应该是 event 的子集"** —— 这条我没只认同，还把它变成了可验证的事实：新落地的 progress/takeover 路径**完全没接进 WorkflowEvent 事件流**，且契约里定义的 2 个事件类型**全库零生产者**（第 5 节，F1）。
3. **评审的结论"要可观察/可恢复"我同意，并且找到了它没定位到的第 2 个真问题**：新的 stale 接管安全网**没有任何周期触发器**，只能人工 curl（F2）。

### 0.1 一个必须先说清的前提坑

我一开始查本地 `main`（`fb005c2`），发现评审点名的 4 个 commit **在任何 ref 上都不存在**，差点据此写出"评审在评审不存在的提交"。

真相是：**本地 `main` 与 `origin/main` 早已分叉**，远端在本次会话期间被另一会话推了 6 个提交：

```
本地 main 独有：  9d1a618, 6ebfb3a, fb005c2, 14ea2a6(=本次新增)
远端独有：        6249f7b, e29f01b, ba65606, 1cef111, 5c23acd, 34a2c02
```

`git push origin main` 已被拒（`fetch first`）。**这条需要你拍板怎么合**——我没有做任何 merge/rebase/force 操作，因为工作区里还压着另一份未提交的 WIP（`CursorAdapter.cs` / `openspec/changes/cursor-worker-adapter/` 等 19 个文件），在脏树上跑 rebase 正是 `1cef111` 那篇教训文档警告的场景。

---

## 1. 评审条目 ↔ 实际提交 对应关系

| 评审编号 | 评审写的标题 | 实际提交 | 核实结果 |
|---|---|---|---|
| Commit 1 | `fix(workflow): in-flight progress heartbeat + comment/document dedup` | `34a2c02` | ✅ 存在，标题逐字一致 |
| Commit 2 | `test(work_items): fix 4 stale fixture cases` | `5c23acd` | ✅ 存在（全称含 `in test_delete_cascade_fk`） |
| Commit 3 | `docs(lessons): rebase期间重复 alembic migration 坑沉淀` | `1cef111` | ✅ 存在 |
| Commit 4/6 | `merge ghost_work_cleanup + workflow_runs into single head` | `6249f7b` **+** `ba65606`(Revert) | ⚠️ 提交存在，但**当天已被 revert** |
| Commit 5 | `docs(e2e-plan): 同步最后更新时间` | `e29f01b` | ✅ 存在 |
| Commit 7 | `feat(observability): in-process LocalDiagnostics + /api/diag` | `289b266` | ✅ 完全一致 |

评审把 `6249f7b` 和 `ba65606` 合并成了"Commit 4/6 一次架构调整"来表扬（"很好"）。实际是**一次提交 + 当天一次 revert**——评审没看到 revert。这一点决定了它对第 4 条的全部评论都建立在已不存在的改动上。

---

## 2. 逐条判定总览

| # | 评审论断 | 判定 | 一句话依据 |
|---|---|---|---|
| 1 | heartbeat 不该代表 process alive，该是 workflow progress alive | 🟡 **部分认同** | progress 语义**已在本条提交里实现**（`last_progress_at`）；但真正缺的是"接进事件流" |
| 2 | 必须有 `workflow_events` / event sourcing light | ❌ **不成立** | `workflow_run_events` + 19 类事件契约 + 原子 emit，9-11 已落地 |
| 3 | 别手写 fixture，引入 factory pattern | 🟡 **方向认同，范围夸大** | 实测 ~48 处 / 29 文件；且根因是"绕过领域 API 裸构造"，不是"没有 factory" |
| 4 | CI 加 `alembic check` / migration graph 校验 | ✅ **认同，已实现** | 见第 3 节 |
| 5 | 别为 migration 简化牺牲 domain boundary | ❌ **前提错误** | merge 是 DAG 节点，不合表；且已有 `test_domain_boundaries.py` 门禁 |
| 6 | 固化 E2E checklist 并在 CI 跑 | ❌ **已存在** | CI 有 `golden-cross-stack` 真库真 MQ 全链路门禁 + `dod_registry.py` |
| 7 | 需要 Agent capability model | ❌ **已实现** | `Agent.capabilities` + `Task.needed_capabilities` + 打分匹配 |
| 8 | P0：workflow state machine 必须明确，别散落 `if status==` | ❌ **已实现** | 显式 transition 图 + `StateMachine` 类 |
| 9 | 异常恢复 🔴 | 🟡 **部分认同** | 恢复机制其实很厚；但确实有两个真洞（第 5 节） |

---

## 3. 认同并已落地

### CI 迁移图门禁（对应评审第 4 条）

评审建议在 CI 加 `alembic check` / migration graph validation。**现有实现是隐式的**：CI 的 `init_db()` 会跑 `alembic upgrade head`，多 head 时确实会炸——但炸出来的是启动链路的 traceback，看不出真因。`1cef111` 那篇教训文档把"rebase 后必跑 `alembic heads`"写成了**人工纪律**。

本次把它变成**自动门禁**：

- 新增 `scripts/migration-graph-gate.py`（沿用 `schema-drift-check.py` / `nuget-audit-gate.py` 的 0/1/2 退出码约定）
- 接入 `.github/workflows/application-stack-check.yml`，跑在 `upgrade head` 之前，失败即具名报出
- 顺带把 `scripts/**` 加进该 workflow 的 `paths`（与 `dotnet-contract-check.yml` 对齐），避免门禁自身没人测

四类检查：head 恰好 1 个、base 恰好 1 个、所有 revision 从 head 可达、无悬空 `down_revision`。

**已验证（含反向测试，不是假绿）**：

| 场景 | 结果 |
|---|---|
| 当前 `main` 树 | `revisions=76 bases=1 heads=1` → **exit 0** |
| 注入 `zzz_gate_probe.py`（parent 指向祖先后分叉） | `heads=2`，两个 head id 都点名 → **exit 1** |
| 删除探针后复跑 | 恢复 `76/1/1` → **exit 0** |
| **注入 `6249f7b` 那份被 revert 的重复 merge migration** | `heads=2`（`0f0e0d0c0b0a` + `51483e66a514`） → **exit 1** |

最后一行是重点：**这个门禁能精确复现并拦截 `1cef111` 记录的那次事故**。原理是重复 merge 会在同一对 parents 上产生两个 merge 节点 → 必然 2 个 head。教训文档里的人工检查，现在变成 CI 硬约束。

交叉验证：另用一份独立的 AST 解析器（不依赖 alembic）复算同一目录，同为 `76 / 1 / 1`，两条路径结论一致。

**提交**：`14ea2a6 ci(ops): gate the Alembic migration graph in application-stack-check`（仅含 2 个文件，**未触碰**工作区里的外部 WIP）

---

## 4. 不认同 / 需要修正的

### 4.1 「heartbeat 应该是 workflow progress alive」——该改的已经改了，没改的是别的

评审说：

> 错误设计：heartbeat → agent process ping；更好的：`last_event_at / last_progress_at / last_output_at / last_tool_call_at`

而 `34a2c02` 的 diff **恰好加了** `agent_runs.last_progress_at / last_progress_note / is_stale` + `(status, last_progress_at)` 索引，以及 `report_task_progress` 的 run 级心跳（而非 agent 级进程探测）：`scheduling/service.py:2810-2866`。

评审在评论一个连正文都读不到的提交（已自述），所以把"建议新增"提给了"已经新增"。

不过评审的**直觉**在这里是对的，只是没打到点上——真正缺的不是字段，是**事件流接线**，见 5.1。

另外评审文末说"不要再加 heartbeat feature，heartbeat 是 event 的一个子集"，而它前面刚建议"增加 last_event_at / last_progress_at / last_output_at / last_tool_call_at"。**同一份评审里自我矛盾**：前文要求扩字段，后文要求别扩。

### 4.2 「必须有 workflow_events」——9-11 就落地了，而且比建议更严

评审给的方案是 `id / run_id / event_type / created_at / payload` 五列。实际实现（`b0c1b23`，slice 2 of 7）：

| 评审建议 | 实际实现 |
|---|---|
| 5 列表 | `workflow_run_events`：+ `phase` / `state` / `actor_type` / `actor_id` / `task_id` / `agent_run_id` / `summary`（`features/workflow_runs/models.py:93-127`） |
| 无校验 | `event_contract.py` 19 类事件 + 每类**必填 payload keys** + `actor_type` 白名单 + `retry_kind` 值域校验 |
| 无原子性 | `emit_workflow_event` 校验后**同一 session** 内写 event + bump 父 run 的 `last_activity_at` / `version` |
| — | `task_reopened`（run 内打回）与 `workflow_reopened`（跨 run）**严格分列**，避免语义混淆 |

评审列的 10 个事件名里，`RUN_STARTED`→`workflow_started`、`AGENT_ASSIGNED`→`task_assigned`、`REVIEW_STARTED`→`review_requested`、`RUN_COMPLETED`→`workflow_completed` 已覆盖。**确实没有**的是执行粒度事件：`LLM_REQUEST` / `TOOL_EXECUTION_STARTED|FINISHED` / `COMMIT_CREATED` / `TEST_FAILED`。

这条我**部分认同**：执行粒度事件是真实需求，但它属于 slice 3-7 已规划的 `executions` API + `reconcile` 范畴（`openspec/changes/workflow-run-overview-20260911/tasks.md`），现在补是抢跑。

### 4.3 「引入 factory pattern」——方向对，但根因不是"缺 factory"

先给实测规模，别被"几十个测试挂"带走：

```
裸构造 Task( / Story( / WorkItem( 的测试：29 个文件，合计约 48 处
```

不是"几十个测试挂"的量级。再看 `5c23acd` 修的那 4 个 stale fixture 的**真实根因**（提交信息自述）：

1. `_move_to_done` 走 `in_progress → done` **直跳，被状态机拦下**。Story 265 之后的合法迁移是 `in_progress → in_review → done`。
2. fixture 手搓 `ReviewVote` 没设 `reviewer_agent_id`，撞 NOT NULL + per-agent 唯一（迁移 `p4q5r6s7t8u9` 加严）。

结论：这**不是 fixture 老化**，是 fixture **绕过了领域 API 裸写字段**。而且第 1 条恰恰证明状态机在正确工作——评审却把同一现象解读成"核心 domain state machine 可能还没有完全稳定"（总体评价段）。事实相反：**是状态机把非法迁移抓住了**。

团队其实已经局部收敛了做法：`5c23acd` 新增 `_register_reviewer_agent(s, reviewer_user)` helper 供各 case 复用；`tests/conftest.py:49` 也早有 `make_user` factory fixture。

我的判断：**采纳"走领域构造器/共享 builder"，不采纳"全仓 factory 框架"**。正确规则是"fixture 不得直写状态列，必须经由 service 层"，而不是引入一层 factory 抽象。前者治根因，后者只是把裸写藏得更深。

### 4.4 「别为 migration 简化牺牲 domain boundary」——前提不成立

两处误判：

1. **merge migration 不合表**。`6249f7b` 的 `51483e66a514` 只有 `down_revision = ('d9e0f1a2b3c4', 'm5n6o7p8q9r0')` + 空 `upgrade()`。它是纯 DAG 节点，`workflow_runs` 和 ghost cleanup 仍是两张独立表。评审担心的"workflow_run 里面什么都有"不会因此发生。
2. **domain boundary 已有门禁**。`tests/test_domain_boundaries.py` 在 CI 里跑，断言 domains 层不得依赖 transport/entrypoints。评审建议的"分 domain / runtime / audit 表"这个方向，仓库已用 `agentboard/domains/*` facade 表达。

反过来，评审完全没注意到这条提交的**真问题**：它是**重复 merge**，当天就被 `ba65606` revert 了。真正该提的风险是"多 head"，不是"domain 边界"。

### 4.5 「固化 E2E checklist」——已经是最硬的那种

评审建议"Proposal Flow / Task Flow 画出来，然后 CI 跑"。现状：

- `tests/e2e/cross_stack/test_proposal_full_happy_path.py` —— **真 MariaDB 11 + 真 RabbitMQ 3.13**，跑 Proposal → Story Done 全链路，是 `application-stack-check.yml` 里的 `golden-cross-stack` job（名字就叫 "Proposal to Story Done golden gate"）
- `tests/e2e/happy_path/` 下另有 `test_golden_proposal_story_done.py` / `test_pr7_design_to_unlock.py` / `test_pr8_dev_to_ready_for_review.py` / `test_pr9_reviewer_approve.py` / `test_durable_intake.py` / `test_worker_owned_work.py`
- `tests/e2e/dod_registry.py` —— 机器可读的 DoD 清单，每项带 `acceptance` 断言列表 + `test_files` + `status`

即评审描述的"人工输入 → proposal → agent 讨论 → 生成 task → worker 执行 → review → 完成"这条链路，**已经是 CI 门禁**。

### 4.6 「需要 Agent capability model」——已实现，且比建议更细

评审建议 `agent.capabilities: [coding, testing, review, docs]` + `task.required_capabilities`，并说"你之前说 developer 不能做 QA，其它角色不要限制，我赞同"。

现状（`features/scheduling/matching.py`）：

| 评审建议 | 实际 |
|---|---|
| `agent.capabilities` | ✅ `Agent.capabilities`，含 `name / level(0-5) / confidence(0-1)` 结构化条目，并兼容 legacy 字符串标签 |
| `task.required_capabilities` | ✅ `Task.needed_capabilities`，含 `name / minimum_level` |
| 匹配 | ✅ `score_agent_for_task`：coverage 0.35 / proficiency 0.25 / confidence 0.10 / **历史成绩 0.20** / 负载因子 0.10 |

关键一点，评审只是转述你自己的决定，代码里早有明文（`matching.py:211-213`）：

```python
# design/developer/reviewer/qa 是本次 workload，不是 Agent 永久身份。
# roles 仅保留兼容/审计，不再作为 eligibility gate。
eligible = bool(agent.enabled) and not missing
```

即"角色不做硬门槛"**已经落地**，`roles` 只用于 legacy executor 推导。

### 4.7 「P0：state machine 必须明确」——已是显式图

| 评审担心 | 实际 |
|---|---|
| 状态转换必须明确 | `WORKFLOW_RUN_TRANSITIONS`（`state_machine.py:24-32`）—— `failed` 严格终态，无 `failed → running` |
| 不要散落 `if status==` | Task 侧是 `StateMachine` 类 + `_TASK_TRANSITIONS` 注册（`features/work_items/state_machine.py`），迁移走 `execute_transition` + exit hooks |
| phase 也要显式 | `WORKFLOW_PHASE_TRANSITIONS`（`:58-62`）—— `design→development→qa` + `qa→development` rework，跨级跳跃非法 |

有反向证据：`5c23acd` 的 fixture 之所以失败，正是撞上 `IllegalTransition`——**说明门是活的**。

### 4.8 「comment/document dedup → hash 现在够用，未来语义去重」

这条是评审判错最彻底的地方，而且**错在我方提交标题**：

- 提交标题写 `comment/document dedup`
- 提交正文写的是 `2. Comment / document cross-reference (P2)`
- 实现是 `comments.linked_document_id`（FK → `documents.id`, ON DELETE SET NULL）+ MCP `post_document_link`

是**建立关联**，不是**去重**。`create_comment` 里还有一行明确拒绝去重：

```python
# We do NOT police "content vs document body duplication" here — that's the
# agent's responsibility, the database is content-agnostic.
```

所以评审"现在用 hash、以后用 semantic dedup"整段，是对着不存在的能力给建议。

而我认同的部分是：**hash 去重是错的工具**。理由独立于上述误读：

1. 重复评论已经被**结构性围栏**挡住 —— `worker_discussions.validate_turn` 用 `(turn, iteration, review_round)` 三重比对，不匹配直接 409（`worker_discussions.py:20-29`）；`uq_worker_discussion_active_task` 保证一 task 一活跃讨论。
2. hash 去重**该拦的拦不住**（换个措辞就绕过），**不该拦的会误杀**（两轮相同报错文本是合法信息）。

建议：**改标题约定**——"dedup" 与 "cross-reference" 不同义，写进 commit 规范，避免 reviewer 被标题带走。这条成本为零、收益立现（本次已生效）。

---

## 5. 我另外定位到的两个真问题（评审没打到点）

### F1 —— 新的 progress / takeover 路径完全在事件流之外 ⚠️ 高

**事实**：

```
origin/main 的 features/scheduling/service.py 中
emit_workflow_event / workflow_run_events / workflow_runs 的出现次数 = 0
```

`report_task_progress`（:2810）、`scan_stale_agent_runs`（:2869）、`soft_takeover_run`（:2981）三个新函数，**没有一处写 WorkflowEvent，也没有一处 bump `WorkflowRun.last_activity_at`**。

更硬的证据：契约里定义的两个运维事件**全库零生产者**。

```
agent_heartbeat_lost   → 仅出现在 event_contract.py:45 与 :82
worker_lease_expired   → 仅出现在 event_contract.py:46 与 :83
```

grep 全仓（`src/backend-fastapi/agentboard/**`）**没有任何一处 emit 它们**。契约声明了、必填 payload 定义了（`{agent_id, last_seen_at, probe_message}` / `{worker_id, lease_expires_at}`），但没人产出。

**影响**：

- slice 5 规划的是"Active Workflows 卡片 + 详情页 timeline 渲染 17 类事件"。**运维事件永远不会出现在 timeline 上**——因为没人产生。
- `soft_takeover_run` 会静默把 task 打回 `todo`、把 run 标 failed、释放 assignment。这在 WorkflowRun 视角下**完全不可见**：`last_activity_at` 不动 → 卡片上这条 run 显示"静默 idle"，而它的 task 已经悄悄回到池子里。这正是 slice 5 承诺要消灭的"看不清"。
- 这是评审那条"heartbeat 应该是 event 的一个子集"的**真正落点**：不是要再加字段，而是让已有的 progress/takeover 机制**接入已有的事件流**。

**为什么我没有直接改**：`agent_heartbeat_lost` 的必填 payload 是 agent-probe 形态（`agent_id / last_seen_at / probe_message`），而 run 级 stale 扫描需要的是 run 形态（`run_id / task_id / last_progress_at`）。**契约与产者形状不匹配**，该发哪个事件、要不要新增第 20 类事件（如 `run_stale` / `run_taken_over`），是契约决策，不该我猜。且这些代码在 `origin/main`，本地 `main` 尚未合并。

**建议**：在 slice 3-7 里插一个前置子项——"把 `soft_takeover_run` 与 `scan_stale_agent_runs` 接进 `emit_workflow_event`"，并明确 `agent_heartbeat_lost` 的语义边界（agent 探活 vs run 进度停滞）。

### F2 —— stale 接管安全网没有任何周期触发器 ⚠️ 中高

**事实**：`scan_stale_agent_runs` 全仓**只有一个非测试调用点**：

```
features/scheduling/router.py:224  def scan_stale_agent_runs_endpoint(
features/scheduling/router.py:243      from .service import scan_stale_agent_runs
```

即只有 `POST /api/scheduling/scan-stale-runs` 这个人工端点。**没有 scheduler / worker 主循环 / 定时任务调用它**（`:2774` 的注释只是设计说明，不是调用）。

`report_task_progress` 同理：只有端点 + MCP 工具，靠 agent 自觉每 10 分钟调一次。

**影响**：提交信息写的是

> WARN_AFTER=30min, TAKEOVER_AFTER=60min (defaults)
> A scheduler that polls every 60s can call this with a 5 min warn and 30 min takeover

设计上"可以由 60s 轮询的调度器调用"——但**这个调度器没接**。生产实际后果：agent 卡死/静默后，task 仍然无限期占着 `in_progress` + `active_slot`，除非有人主动 curl 那个端点。

讽刺的是，这正是这套机制要解决的问题本身。**安全网装好了，但没挂上绳**。

**为什么我没有直接改**：挂在哪取决于部署形态（`processors/maintenance.py` 的 `recover_failed` 循环？worker 主循环？外部 cron？），属于运行架构决策；且同样是 `origin/main` 上尚未合并的代码。

---

## 6. 整体汇总

### 6.1 评审质量

| 维度 | 评价 |
|---|---|
| 大方向 | **对**。"从功能验证进入运行可靠性阶段"、"别继续扩 infra、先把闭环打通"——方向准确 |
| 具体技术论断 | **命中率低**。9 条里 5 条指向已实现的东西（事件流、capability、state machine、E2E 门禁、migration merge 性质） |
| 事实校准 | **有偏差**。自述无法看 diff，导致把已有实现当缺口、把已 revert 的提交当方向调整、把状态机在正确拦截当"状态机不稳定" |
| 有价值的部分 | **两点**：① "heartbeat 应是 event 子集"的直觉（指向 F1）；② 对 `1cef111` 教训文档的肯定——这条确实是高质量的工程沉淀 |

评审自己的免责声明是准确的："不能直接展开 diff，所以重点分析提交意图、演进方向、潜在风险"。**按这个定位，它是合格的；但按"逐 commit 技术判断"的定位，它不可作为行动依据。**

### 6.2 我认同并已做的

| 项 | 产出 | 验证 |
|---|---|---|
| CI 迁移图门禁 | `scripts/migration-graph-gate.py` + workflow 接线（`14ea2a6`） | 4 场景正反测试；能复现拦截 `1cef111` 记录的重复 merge 事故 |

### 6.3 我认同方向但建议换做法的

| 评审建议 | 我的替代方案 |
|---|---|
| 引入 factory pattern | fixture 必须走 service 层构造器，禁止直写状态列；只对 work_items 做共享 builder，不上全仓 factory |
| 加 workflow 事件表 | 已有；真正要做的是把 progress/takeover 接进去（F1），并给 `agent_heartbeat_lost` / `worker_lease_expired` 补生产者 |
| hash + semantic dedup | hash 是错的工具；保留现有 turn-fencing 结构性围栏；修正 "dedup vs cross-reference" 的标题用词 |

### 6.4 优先级建议（修正版）

评审给的排序是 `P0 状态机 → P1 事件日志 → P1 capability`。基于实测，我建议改成：

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0** | 合并分叉：`origin/main` ↔ 本地 `main` | 现在两边各有独立提交，`push` 已被拒。不解决，后面所有改动都没法落地 |
| **P1** | F1 事件流接线（progress/takeover → WorkflowEvent） | slice 5 的 timeline 依赖它；不接等于 slice 5 上线即缺一角 |
| **P1** | F2 给 stale 扫描挂周期触发器 | 安全网不挂绳 = 没装 |
| P2 | MQ 路径的 task 级重试封顶 | 见 6.5 |
| — | 状态机 / capability / 事件表 | **不需要动**，已实现 |

### 6.5 顺带：一个已知未修的洞（与本次评审无关，但属"异常恢复"范畴）

`processors/coordinator.py` 的两级重试预算里，**Task 级计数桶只有轮询路径会写**：`_burn_poll_cycle` 的调用点只有 `:710` 和 `:721`（都在 `_record_poll_attempt` 内），MQ 路径的 `_message_consumed`（`:261`）不调用它。

且**不能用"在 `_message_consumed` 里补一次 `_burn_poll_cycle`"来修**——我确认过桶键的构成：

```python
_workflow_retry_key(msg)   = (event, entity_type, entity_id, ref_id)   # event 来自消息
_poll_cycle_retry_key(k)   = (event, entity_type, entity_id, 0)        # 优先沿用 event
```

轮询路径的 `event` 固定是 `task.assigned`，MQ 路径是 `task.available` / `task.review_requested` / `task.rejected`。**同一 task 的 MQ 桶和轮询桶 event 名不同 → 不是同一个桶**，直接补调用只会造出一个平行的、依然无界的计数器。

正确修法是先定一个**不含 event 名的 canonical "每 task 总投递轮数"键**，再让两条路径共写。这是设计决策，需要你拍板，我没有擅自改。

---

## 附：验证方法（可复现）

```bash
# 迁移图（当前树）
python scripts/migration-graph-gate.py

# 迁移图（任意 ref，无需 checkout）
# 见 tmp/ref_migration_check.py 的抽取+AST 解析法

# 事件契约零生产者
git grep -n "agent_heartbeat_lost\|worker_lease_expired" origin/main
# → 仅 event_contract.py 的声明与必填键映射，无 emit 调用

# stale 扫描的调用点
git grep -n "scan_stale_agent_runs" origin/main
# → 唯一非测试调用者：features/scheduling/router.py:243（人工端点）

# 裸构造 fixture 规模
# Grep:  tests/**/*.py  pattern: Task\(|Story\(|WorkItem\(
# → 29 个文件 / 约 48 处
```
