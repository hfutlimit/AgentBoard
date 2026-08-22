# AgentBoard v0.1 Demo 设计文档

> 目标：**打通一个端到端 Autonomous Software Development Demo**——PO 给一句需求，Agent 自主完成 分析 → 设计 → 编码 → 测试 → 评审 → 提交 PR，并由人工批准合并。
> 本设计**不重写现有代码**，而是在已建能力上补齐唯一缺口：**Git/PR 闭环 + 运行可视化**。

---

## 0. 现状核对（设计前提，非臆测）

| 用户提案 | 现状 | 证据 |
|---|---|---|
| 完整闭环 Demo | 90% 已建，缺 Git/PR 落地 | 全库 grep `git clone/checkout/commit/push/create_pr` / `Repo()` / `PyGithub` → **零命中** |
| `AgentRun` 数据模型 | **已存在** `agent_runs` 表 | `features/scheduling/models.py:36`，含 `idempotency_key/summary/log_ref` |
| Spec 层 | **已存在** `Proposal.converged_spec` | `features/proposals/models.py:144` |
| Agent 执行框架 | **已存在** `AgentAdapter`/`LauncherAdapter` | `executor.py:156/204`，已能 spawn `codex/claude` 子进程 |
| Worker 抽象 | **已存在** `features/workers/` | polling + MQ 双模式 |
| Evaluation | **已存在** `features/learning/judge.py` | LLM-as-judge + 确定性降级 |
| Project Memory 字段 | **已预留** `AgentRunContext.memory` | `executor.py:90`（空字符串，待注入） |

**结论**：不要新建 `AgentRun` 表、不要新建 spec 模块、不要把 `executor.py` 换成 LangGraph。正确做法是**扩展** `agent_runs` 5 个字段 + 新增 `workflow_instances` 表 + 新增 `features/git/`、`features/workflow/`。

---

## 1. 数据模型扩展（增量，不新建重复表）

### 1.1 扩展 `agent_runs`（`features/scheduling/models.py`）

在现有 13 列基础上追加 5 列，使 5 个 Agent 的运行可归属于同一工作流并携带成本/批准信息：

```python
# 新增字段（追加到 AgentRun）
workflow_instance_id: Mapped[int | None] = mapped_column(
    ForeignKey("workflow_instances.id", ondelete="SET NULL"),
    nullable=True, index=True,
)
step_name: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
input_context: Mapped[str | None] = mapped_column(Text, nullable=True)   # 发给 agent 的 prompt 快照
token_usage: Mapped[str | None] = mapped_column(Text, nullable=True)     # JSON: {prompt,completion,cost_usd}
human_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 批准门结果
```

> 复用而非新建 `AgentRun` 的理由：现有表已有 `idempotency_key`（幂等）、`summary`、`log_ref`、`status` 状态机，新建表会重复这些且割裂 Run 页查询。

### 1.2 新增 `workflow_instances`（`features/workflow/models.py`）

```python
class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    __table_args__ = (CheckConstraint(
        "status IN ('pending','running','paused','awaiting_approval','success','failed','cancelled')",
        name="ck_wf_status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    proposal_id: Mapped[int | None] = mapped_column(ForeignKey("proposals.id"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    workflow_type: Mapped[str] = mapped_column(String(40), default="software-development")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    current_step: Mapped[str | None] = mapped_column(String(40), nullable=True)  # 运行页进度条用
    repo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    base_branch: Mapped[str] = mapped_column(String(100), default="main")
    head_branch: Mapped[str | None] = mapped_column(String(200), nullable=True)  # feature/xxx
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

PR 信息直接落在 `workflow_instances`（demo 阶段不另建 `git_pr_records`，避免过早拆分）。

### 1.3 迁移方式

- SQLite（dev）：`batch_alter_table` 改列（见项目记忆：SQLite 改列须 batch）。
- MariaDB（prod）：`alembic revision --autogenerate` 生成迁移；注意 `String(40)/String(512)` 长度避免触发 1406（见已知 `ck_epics_status` 缺陷备忘）。

---

## 2. 目录结构（扩展，不重写）

```
agentboard/
├── features/
│   ├── proposals/        # 已有：spec 层（Proposal.converged_spec 即需求规格）
│   ├── scheduling/       # 已有：AgentRun 模型 + AgentAdapter 框架
│   ├── workflow/         # ★ 新增
│   │   ├── models.py          # WorkflowInstance
│   │   ├── engine.py          # 硬编码 5 步顺序编排（DB 即 checkpoint）
│   │   ├── schemas.py
│   │   ├── trigger.py         # "Start AI Development" 入口：Proposal → WorkflowInstance + 首步
│   │   └── steps/
│   │       ├── requirement.py  # Proposal → spec.md（验收标准/API/DB）
│   │       ├── architect.py    # spec → 架构决策（service/controller/entity/flow）
│   │       ├── developer.py    # 调 LauncherAdapter(claude/codex) 在 clone 仓内改码
│   │       ├── qa.py           # 生成测试 + dotnet/ng test
│   │       └── reviewer.py     # diff 评审 + 多维 PASS/FAIL + 评分
│   ├── git/              # ★ 新增：Git Provider 抽象（首版仅 GitHub）
│   │   ├── base.py            # GitProvider ABC: clone/branch/commit/push/create_pr
│   │   └── github.py          # GitPython + PyGithub 实现
│   ├── memory/           # ★ 新增：项目记忆加载器
│   │   └── loader.py          # 读 documents(memory/design) + decision log → AgentRunContext.memory
│   └── workers/          # 已有：单 worker 模式（polling_once / MQ）
```

> 拒绝用户提案中的 `agentboard/api/workflow/...` 绿field 结构——它会遗弃已建好的 `features/proposals`、`executor.py`、`features/scheduling`，与"保护已有投入"原则冲突。

### 2.1 LangGraph vs 轻量编排（关键决策）

**v0.1 推荐：自研轻量状态机编排器，DB 行即 checkpoint。** 理由：
- 线性 5 步 DAG 不需要 LangGraph 的图调度；额外重依赖拖慢 demo。
- `pause/resume/retry` = 更新 `workflow_instances.status` + 用 `idempotency_key` 重拉该步（`LauncherAdapter` 已支持）。
- 人工批准门 = `status='awaiting_approval'` 阻断 merge 步。
- Run 页直接读 `agent_runs` 按 `workflow_instance_id` 分组，零额外查询层。

**v0.2 候选：LangGraph**——当出现条件分支（评审不过→回 developer 重做）、真实指数退避 retry、多编排模板时，其 checkpoint/resume 才值回票价。可在 `engine.py` 后置于 `WorkflowEngine` 接口之后做无缝替换。**若坚持 v0.1 即用 LangGraph，预算 +3~4 天。**

---

## 3. 五 Agent 工作流（硬编码顺序）

```
Proposal(converged_spec)
   │
   ▼ [1] requirement agent   → 写 spec.md（验收标准 / API / DB 变更）
   ▼ [2] architect agent     → 写 arch-decision.md（service/controller/entity/flow）
   ▼ [3] developer agent     → git checkout -b feature/x；LauncherAdapter(claude) 改码；dotnet/ng build
   ▼ [4] qa agent            → 生成测试；dotnet test / ng test；报告通过率
   ▼ [5] reviewer agent      → 评审 diff；架构/安全/测试/质量 四维 PASS/FAIL + 评分
   ▼ create_pr (github)      → 写 workflow_instances.pr_url
   ▼ [human approval gate]   → PO 点击合并（status=awaiting_approval 解锁）
```

每步统一契约（`features/workflow/steps/_base.py`）：

```python
def run_step(instance, prev_outputs, memory) -> StepResult:
    ctx = build_prompt(...)                      # 模板按步不同
    if step in ("developer","qa"):               # 会改码的 agent
        handle = LauncherAdapter.launch(         # 复用 executor.py，cwd=clone 仓
            run, task, ctx_with_cwd)
        poll until done
        git_commit_and_push(instance)            # 经 features/git
    else:                                        # 思考型 agent（requirement/architect/reviewer）
        out = llm_call(ctx)                      # 经现有 AgentAdapter / LLM
        write_artifact(out)                      # spec.md / arch-decision.md / review.md
    persist_agent_run(step_name, input_context, output, token_usage)
    instance.current_step = next
```

**关键复用**：`developer.py`/`qa.py` 不重造 coding agent，而是调 `executor.py` 的 `LauncherAdapter` spawn `claude`/`codex` 子进程——这与用户"不要重实现 coding agent，通过 CLI/MCP 调用"的洞察完全一致。

---

## 4. Git Provider（唯一真缺口）

`features/git/github.py`：

```python
class GitHubProvider(GitProvider):
    def clone(self, repo_url, dest, token): ...        # GitPython，auth URL 注入 PAT
    def create_branch(self, repo, name): ...
    def commit(self, repo, message): ...                # git add -A
    def push(self, repo, branch): ...                   # git push -u origin
    def create_pr(self, repo, base, head, title, body): # PyGithub → (pr_number, pr_url)
```

- 工作区隔离：`data/workspaces/{workflow_instance_id}/`，**绝不**在 AgentBoard 仓内跑 coding agent（沙箱底线）。
- 密钥：`AGENTBOARD_GITHUB_TOKEN` 走环境变量，绝不进代码。
- 首版仅 GitHub；GitLab 留 `GitProvider` 抽象后续实现。

---

## 5. Run 页（最重要 UI，LangGraph Studio 风格）

新增 Angular 组件 `frontend/.../workflow/`：

- 垂直 Stepper：`Requirement ✓ → Architect ✓ → Developer 🔄 → QA ⏳ → Reviewer ⏳`
- 每节点可展开：input_context / output 预览（spec.md / review.md）/ token_usage / cost
- 底部 PR 卡片：链接 + **Merge 按钮（人工批准门）**
- 后端新增：`GET /api/workflow-instances/{id}`、`GET /api/agent-runs?workflow_instance_id=`、

`POST /api/workflow-instances/{id}/approve`

**这是 demo 的视觉中心，也是与 Cursor/Copilot 的差异点（治理可视化）。**

---

## 6. 样本目标仓库（易被忽略，但 demo 跑通的前提）

当前没有 .NET+Angular+PostgreSQL 目标仓供 agent 修改。**Day 1–3 必须先脚手架一个最小 CRM 骨架**（`AgentBoard-DemoTarget`：Customer 实体 + EF Core + PostgreSQL + Angular 列表页），"customer import" 功能才落实地。否则闭环无法端到端验证。

---

## 7. 两周落地计划（对齐现有模块）

| 阶段 | 天 | 交付 | 复用/新增 |
|---|---|---|---|
| 建模 | 1–3 | `workflow_instances` 模型+迁移；`agent_runs` 扩 5 字段+迁移；`trigger.py` 入口；样本 CRM 仓脚手架 | 新增 model / 扩表 / 脚手架 |
| 闭环 | 4–7 | `features/git/github.py`；`developer.py`/`qa.py` 接 `LauncherAdapter`；`requirement/architect/reviewer` 模板；`engine.py` 顺序编排 | 新增 git+workflow；复用 executor/proposals |
| 记忆 | 8–10 | `features/memory/loader.py` 注入 `AgentRunContext.memory`；决策日志 | 新增 loader；复用 documents |
| 包装 | 11–14 | Run 页；Docker compose（AgentBoard + 样本仓 runner）；README；视频脚本；端到端 dry-run | 新增前端页 |

---

## 8. 护栏（demo 阶段也不可省略，是差异化卖点）

1. **沙箱隔离**：coding agent 只在 clone 工作区运行，永不触碰 AgentBoard 仓。
2. **成本上限**：`token_usage` 累计超预算 → 终止并标红（避免 claude/codex 跑两次资不抵债）。
3. **超时熔断**：每步 `LauncherAdapter.timeout_seconds` 已存在，demo 必须配置。
4. **人工批准门**：PR 未获 PO 批准前不合并——这是企业客户"敢不敢买"的底线，第一天就进闭环。
5. **密钥管理**：GitHub PAT 仅环境变量。

---

## 9. 与战略层衔接

- 本 demo 全部代码暂留私有单仓，**不现在切 Open Core**（见前序讨论：开源/闭源是证明价值后的封装决策）。
- 跑通后，`features/workflow` + `features/git` + `features/memory` 即 Layer 2 闭源 Runtime 的核心；`features/proposals` + `mcp` + `AgentAdapter` 即 Layer 1 开源 Core 候选。
- DevPilot 的"理解代码"引擎可作为 `features/memory/loader.py` 的研发线持续喂入，暂不合并仓库。
