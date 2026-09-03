# AgentBoard Agent / Worker / Runtime Architecture

> **Status**: in design (2026-09-03)
> **Author**: Mavis (Mavis@MiniMax.local) + operator
> **Replaces**: `docs/agent-config-center.md` (2026-08-09) and `docs/agent-integration-analysis.md` (2026-08-13)
> **Supersedes / extends**: `openspec/changes/agent-ephemeral-2026-09` (draft, P0–P4 done, P5+ below)
> **Goal**: 重新定义 **Server Control Plane** vs **Workstation Execution Plane** 的边界，
> 让 happy path 上每个流程 block（"X 不存在"、dispatch 失败、owner 缺失、worker 越权、ghost agent、
> 命名冲突）有且仅有一个 clear resolution path。

---

## 1. 三大平面

```text
┌─────────────────────── AgentBoard Server (Control Plane) ───────────────────────┐
│                                                                                │
│  API / Project / Ticket / Work Items / Proposals / Documents                    │
│                                                                                │
│  RuntimeRegistry        Scheduler         Dispatcher         WorkflowProcessors│
│  ──────────────         ──────────        ────────────        ────────────────  │
│  per-process cache      picks (Agent,     MQ publish by      ProposalProcessor  │
│  rebuilt from worker    Worker) for a     (worker_id,        TaskLifecycleProc  │
│  WSS HELLO/DELTA;       task; runs        agent_id) tuple;   ReviewProcessor    │
│  NEVER persisted        RuntimeEligibility never decides      QAProcessor        │
│  to DB                  service           who executes       NotificationProc   │
│                                                                                │
│  DB: User / Project / ProjectMember / Ticket / Task / TaskAssignment           │
│      TaskOutcome / AuditLog / Worker / WorkerProjectMapping /                 │
│      ReviewVote / Notification / Webhook / Document / Proposal                │
│                                                                                │
└────────────────────────────────────┬───────────────────────────────────────────┘
                                     │ RabbitMQ (transport only)
                                     │
┌────────────────────────────────────┴───────────────────────────────────────────┐
│                          AgentBoard.Node (Execution Plane)                     │
│                                                                                │
│  ASP.NET Core host running as Windows Service + Local Web Portal (127.0.0.1)  │
│                                                                                │
│  AgentRegistry         ProjectWorkspaceRegistry    AgentExecutor             │
│  ──────────────        ───────────────────────      ─────────────              │
│  Local SQLite          Local SQLite                 ExecutorFactory            │
│  (~/.codebuddy/        (project_id → local          → CodexExecutor            │
│   agents.db)           path mapping)                → CodeBuddyExecutor         │
│                                                      → ClaudeExecutor            │
│  AgentDefinition[]    ProjectWorkspace[]           → CustomExecutor            │
│  = source of truth     = source of truth           → (future) M365Executor     │
│                                                                                │
│  AssignmentConsumer    ProcessSupervisor   RuntimeReporter                     │
│  ─────────────────     ────────────────    ────────────────                      │
│  receives targeted     owns the LLM CLI    publishes HELLO/DELTA/PING          │
│  MQ message; resolves  process; enforces   to server WSS endpoint;             │
│  Agent + Workspace     permission policy;  no decisions made locally           │
│  locally; spawns       kills on timeout                                   │
│  AgentExecutor                                                            │
└────────────────────────────────────────────────────────────────────────────────┘
```

**RabbitMQ 不决定谁执行**。MQ 只搬运 Server 已经选定好的 `(worker_id, agent_id)` 元组。

---

## 2. 术语规范化（必须先做，否则 PR 描述全混乱）

### 2.1 Server 端（Python + .NET BFF）

旧名字（main 上现在叫 Worker）→ 新名字：

| 旧 | 新 | 职责 |
|---|---|---|
| `features/workers/worker.py` (ProposalWorker) | `features/processors/proposal_processor.py` | 后台业务 processor |
| `features/workers/invokers.py` | `features/processors/invokers.py` | processor 调用的 invocation helper |
| `features/workers/heartbeat.py` | `features/processors/heartbeat.py` | processor 周期任务 |
| `features/workers/cli.py` | `features/processors/cli.py` | processor CLI 入口 |
| `features/workers/handlers/*.py` | `features/processors/handlers/*.py` | processor 业务 handler |
| `agentboard.worker` (module) | `agentboard.processors` (module) | 顶层包名 |

**核心**：服务端 `Worker` 一词不再表示"执行节点"，仅保留在 `Worker` SQLAlchemy model 里
（指工作站身份，由 `WorkerProjectMapping` 关联 Project）。

### 2.2 Client 端（执行节点）

| 旧 | 新 | 备注 |
|---|---|---|
| `src/workers/AgentBoard.ProposalWorker/` (C#) | `src/nodes/AgentBoard.Node/` | client 端统一命名 |
| `AgentBoard.ProposalWorker.exe` | `AgentBoard.Node.exe` | 主进程 |
| `appsettings.Local.json` 里 `Worker:` section | `appsettings.Local.json` 里 `Node:` section | config 路径 |
| `AgentBoardWorkerOptions` 类 | `AgentBoardNodeOptions` 类 | .NET 配置类 |
| `WorkBuddyRunner.cs` / `CodexRunner.cs` / ... | `ExecutorFactory.cs` + `CodexExecutor.cs` / `CodebuddyExecutor.cs` / ... | 按 `Agent.executor_type` dispatch |

**理由**：client 端叫 `Node`（节点）最贴"工作站身份"语义。`Runner` 也是备选，但
`Node` 在分布式系统术语里更标准。

### 2.3 数据库模型不变

- `Worker` (server SQLAlchemy model)：仍然存在，含义 = **工作站身份**。`worker_id` 仍然是
  RabbitMQ routing key + WSS HELLO 的 `worker_id`。
- `WorkerProjectMapping`：Project ↔ Worker 授权（T6.3）。
- `Agent` / `AgentInstance`：在 P6 之前是 source of truth；P6 之后**只存 audit / outcome**，不再
  用于 dispatch decision。

### 2.4 必须先做的 P7 commit

> 命名规范化必须**先于** P5（permissions 字段扩展）和 P6（cache 替代 DB online）。
> 否则 PR 描述、OpenSpec、MCP agent guide 全部混淆 Worker 含义。

---

## 3. 数据所有权（SoT 矩阵）

| 数据 | Owner | 在哪里 | Server cache? | Server DB? |
|---|---|---|---|---|
| `agent_id` (逻辑 id) | **Worker** | worker SQLite `agents` PK | ✓ key only | ❌ |
| `name` / `description` | **Worker** | worker SQLite | optional (admin UI) | ❌ |
| `executor_type` | **Worker** | worker SQLite | ✓ | ❌ |
| `cli_path` / `cli_args` / `cli_cwd` | **Worker** | worker SQLite | ❌ **never** | ❌ |
| `env_refs` (key 名 + ref, 不存值) | **Worker** | worker SQLite | ❌ **never** | ❌ |
| `model` (运行模型) | **Worker** | worker SQLite | ✓ (用于 outcome 记录) | ❌ |
| `extra_prompt` (system prompt append) | **Worker** | worker SQLite | ❌ **never** | ❌ |
| `roles` / `capabilities` | **Worker** | worker SQLite | ✓ (dispatch decision) | ❌ |
| `permissions_summary` (fs_scope paths, allowed_mcp_tools, max_runtime_minutes) | **Worker** | worker SQLite | ✓ (filter) | ❌ |
| `permissions_detail` (allow/deny rules, shell patterns) | **Worker** | worker SQLite | ❌ **never** (enforcement-only) | ❌ |
| `enabled` / `online` / `ready` / `current_load` / `max_concurrency` | **Worker** | worker SQLite + 实时算 | ✓ (cache) | ❌ |
| `credential` (OAuth refresh token / API key) | **Worker** | worker keychain / env / file | ❌ **never** | ❌ |
| `config_revision` (monotonic) | **Worker** | worker SQLite | ✓ (cache 校验 stale) | ❌ |
| `last_heartbeat` (presence 时间戳) | **Server** | server cache | ✓ (cache) | ❌ |
| Project workspace path (`D:\AI\Projects\AgentBoard` 等) | **Node (client)** | node SQLite | ❌ **never** | ❌ |
| Project ↔ Node 授权 (`WorkerProjectMapping`) | **Server** | server DB | n/a | ✓ (server SoT) |
| Project / Ticket / Task / Comment / Attachment | **Server** | server DB | n/a | ✓ (server SoT) |
| Task owner / TaskAssignment / TaskOutcome | **Server** | server DB | n/a | ✓ (server SoT) |

**Server cache 镜像 = `AgentAdvertisement`**（决策所需）。**Worker SQLite = `AgentDefinition`**（执行所需）。
Server **永不**缓存 `cli_path / cli_args / extra_prompt / credentials / workspace path`。

---

## 4. Worker 端数据模型

### 4.1 Worker SQLite schema（`~/.codebuddy/agents.db`）

```sql
CREATE TABLE agents (
    agent_id              TEXT PRIMARY KEY,
    name                  TEXT NOT NULL,
    description           TEXT DEFAULT '',
    executor_type         TEXT NOT NULL,        -- 'codex' | 'codebuddy' | 'claude' | 'qoder' | 'custom'
    cli_path              TEXT NOT NULL,
    cli_args              TEXT DEFAULT '[]',    -- JSON array
    cli_cwd               TEXT DEFAULT '',
    env_refs              TEXT DEFAULT '{}',    -- JSON: {KEY: "keychain:name" | "env:VAR" | "file:..."}
    model                 TEXT DEFAULT '',
    extra_prompt          TEXT DEFAULT '',
    roles                 TEXT DEFAULT '[]',    -- JSON
    capabilities          TEXT DEFAULT '[]',    -- JSON
    enabled               INTEGER NOT NULL DEFAULT 1,
    max_runtime_minutes   INTEGER,
    max_cost_usd          REAL,
    max_concurrency       INTEGER DEFAULT 1,
    config_revision       INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE agent_permissions (
    agent_id          TEXT NOT NULL,
    permission_kind   TEXT NOT NULL,        -- 'fs_scope' | 'network' | 'shell_exec' | 'mcp_tool'
    permission_value  TEXT NOT NULL,
    PRIMARY KEY (agent_id, permission_kind),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);

CREATE TABLE agent_credentials (
    agent_id           TEXT NOT NULL,
    credential_kind    TEXT NOT NULL,
    credential_ref     TEXT NOT NULL,        -- 'keychain:name' | 'env:VAR' | 'file:~/.secrets/x'
    PRIMARY KEY (agent_id, credential_kind),
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id) ON DELETE CASCADE
);

CREATE TABLE project_workspaces (
    project_id        INTEGER PRIMARY KEY,    -- matches server Project.id
    local_path        TEXT NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    last_verified_at  TEXT
);
```

### 4.2 Server 端 `AgentCacheEntry`（决定用镜像）

```python
@dataclass
class AgentCacheEntry:
    worker_id:        str
    agent_id:         str
    config_revision:  int
    executor_type:    str = ""          # dispatch decision
    model:            str = ""          # outcome 记录
    roles:            list[str]         # role filter
    capabilities:     list[str]         # capability match
    permissions:      dict              # permissions_summary（详见 §5.2）
    enabled:          bool = True
    ready:            bool = True
    online:           bool = True
    current_load:     int = 0
    max_concurrency:  int = 1
    last_heartbeat:   float
```

**关键简化**：`cli_command` 字符串字段**删除**（已经决定不让 server 决策依赖 CLI 模板）。
新增 `ready` 字段（区别于 `online`：`online` = 进程活着；`ready` = Agent 配置 + 凭据 + 依赖都齐）。

---

## 5. WSS 协议扩展（兼容旧 HELLO）

### 5.1 HELLO / DELTA 帧（agent-ephemeral-2026-09 P0-P4 已落地）

```json
{
  "type": "HELLO",
  "worker_id": "Hank-PC",
  "node_version": "1.4.0",
  "agents": [
    {
      "agent_id": "wb-dev-1",
      "name": "WorkBuddy Dev",
      "description": "",
      "executor_type": "codebuddy",
      "model": "hy4-preview",
      "roles": ["developer", "reviewer"],
      "capabilities": ["python", "backend", "api-design"],
      "enabled": true,
      "ready": true,
      "max_runtime_minutes": 30,
      "max_concurrency": 1,
      "current_load": 0,
      "config_revision": 7,

      "permissions_summary": {
        "fs_scope_paths":     ["C:\\code\\project-a", "C:\\code\\shared-lib"],
        "allowed_mcp_tools":   ["agentboard-mcp"],
        "denied_mcp_tools":    ["github-write"],
        "max_runtime_minutes": 30
      },

      "permissions_detail": {
        "fs_scope":   { "deny_patterns": ["**/.env", "**/secrets/*"] },
        "network":     { "allow_domains": ["https://api.openai.com", "https://pypi.org"] },
        "shell_exec":  { "allow": ["git", "pytest", "ruff"], "deny": ["rm -rf", "sudo"] }
      }
    }
  ]
}
```

### 5.2 `permissions_summary` vs `permissions_detail` 两层

- **`permissions_summary`**：server 缓存，做 fast filter（O(1) 排除明显不符的 agent）。
  - `fs_scope_paths`：路径白名单（set 索引）
  - `allowed_mcp_tools` / `denied_mcp_tools`：MCP tool 过滤
  - `max_runtime_minutes`：cost/risk 上限
- **`permissions_detail`**：server **接收后立即丢弃**，不存 cache。
  仅 worker 端 enforcement 用（fs_scope deny patterns / network allow_domains / shell allow/deny rules）。
- **reasoning**：dispatch 时 server 必须能 select "哪些 agent 能跑 task X"，否则选不出。
  但不需要 enforce-level 完整规则。enforce 由 worker 端 wrapper 进程负责。

### 5.3 PING 帧

只动 `last_heartbeat` + `current_load` / `ready` 状态。**不再** 重新发送完整 agent list（那是 DELTA 帧的职责）。

### 5.4 Worker 端发 DELTA 的触发条件

- Operator 在 local portal 改 agent SQLite → SQLite trigger 或 app 主动 → 推 DELTA
- OAuth refresh / CLI binary update / workspace path change → DELTA
- **配置变更必须 increment `config_revision`** — server 端 audit log 可追溯

---

## 6. Server Scheduler — `RuntimeEligibilityService`

### 6.1 统一 eligibility 链（所有 task / review / QA 走同一管线）

```python
def list_eligible(s, *, item, workload_type, project_id, owner_user_id) -> list[(Agent, AgentInstance)]:
    # 1. Owner gate (T1.5)
    if owner_user_id is None:
        return []  # fail-closed

    # 2. 候选 = owner 拥有的 Agent（DB Agent table 只作 identity，不作 presence）
    candidates = s.query(Agent).filter(
        Agent.user_id == owner_user_id,
        Agent.enabled.is_(True),
    ).all()

    # 3. Runtime cache overlay（presence 唯一来源）
    cache = get_default_cache()
    out = []
    for a in candidates:
        entry = cache.get(worker_id=ANY, agent_id=a.agent_id)
        if entry is None:
            continue
        if not entry.ready or not entry.online:
            continue
        if entry.current_load >= entry.max_concurrency:
            continue

        # 4. WorkerProjectMapping（项目授权）
        if not worker_in_project(s, entry.worker_id, project_id):
            continue

        # 5. Permissions summary filter
        if not _permissions_allow_task(entry.permissions, item, project_id):
            log.warning("eligibility: agent %s lacks permission for %s", a.agent_id, item.id)
            continue

        # 6. Review/QA exclusion（不同 agent than implementer / reviewer）
        exclusion = get_assignment_exclusion(s, item, workload_type)
        if a.id in exclusion.agent_registry_ids:
            continue

        out.append((a, entry))
    return out
```

### 6.2 Ranking（Phase 1 简单版）

按现有 `rank_agents_for_task`：capability 覆盖 + load 倒序 + 简单 tie-break。**不做** history scoring（spec #15）。

### 6.3 Server cache miss

按 `agent-ephemeral-2026-09` 决策 E：cache miss → 503 `Retry-After: 30`。
Worker 还没推 HELLO 之前，dispatch 必然 fail-closed。

### 6.4 Dispatch 决策后

- Server 把 `(task_id, agent_id, worker_id, project_id, payload, assignment_id)` 写到 MQ，**per-(worker_id, agent_id) queue**。
- Worker 收到后**只做本地校验**：
  - `agent_id` 在 local registry 存在？
  - `enabled` / `ready`？
  - `project_id` 在 local ProjectWorkspaceRegistry 有路径？
  - 任一不满足 → 报 `AGENT_UNAVAILABLE` / `PROJECT_NOT_AVAILABLE` 给 server，server release assignment 后重选。

---

## 7. 流程上各种 Block 的解决方案

按发现顺序：

### 7.1 P0 #1 — `create_epic` 不传 user → task 派不出去 ✅ 已修

**Commit `5e55da0` 修完**：
- `features/projects/router.py::create_epic` 加 `authorization: str | None = Header(None)` + `resolve_actor_context` 拿 user
- `features/projects/service.py::create_epic` 加 `created_by_user_id: int | None = None` 参数，透传到 `create_story`
- 匿名调用仍允许（try/except HTTPException 兜底 None），跟 `create_story` 行为对齐

### 7.2 P0 #2 — `create_run` 造 ghost agent → task 死锁 ✅ 已修

**Commit `5e55da0` 修完**：
- `features/scheduling/service.py::create_run` 删除 `if agent_config is None: s.add(Agent(...))` ghost agent 分支
- 改为 `raise NotFound("agent 'xxx' is not registered; create_run refuses to mint a synthetic Agent. Register via POST /api/agents or wait for the worker's WSS HELLO to publish it.")`
- 跟 spec 决策 G（DB read-only for `agent_instances`）一致

### 7.3 P1 #3 — 多 owner invariant 漏洞 ⚠️ 部分解决

**当前状态**：
- `f08d492` 加了 `ProjectMember` 唯一约束规范化
- `e7eabdd` 加了 `remove_user` 自动移交 owner → 接收方按 `joined_at` 最早
- `c995120` 加了 `owner_transfer_history` 实体

**剩余问题**：`add_project_member(..., role='owner')` 仍允许多 owner（UNIQUE 仅 `(project_id, user_id)`）。
Service 已经选 single-owner 语义（`resolve_project_owner` 多 owner 时按 joined_at 最早 + warning），但**写侧不强制**。

**P7 阶段修法**（schema 改动 + 行为统一）：
- `ProjectMember` 加 partial unique index：`UNIQUE (project_id) WHERE role = 'owner'`
- Alembic migration：先 drop 已有 UNIQUE 约束（如果有），加 partial UNIQUE
- `add_project_member(role='owner')` 在 service 层：检测到已有 owner → 抛 `Conflict("project already has an owner; use transfer_project_ownership()")`
- 新增 `transfer_project_ownership(s, *, project_id, from_user_id, to_user_id, changed_by)` — 显式状态机迁移，写 `owner_transfer_history` + 发通知
- DB 层 migration 用 partial unique index（SQLite + MariaDB 都支持）

### 7.4 P1 #4 — `WorkerProjectMapping` 未接入 dispatch ⚠️ 已知

**当前状态**：
- `1249cce` 加了 `WorkerProjectMapping` model + `map_worker_to_project` / `unmap_worker_from_project` / `list_project_workers` / `worker_in_project` service
- 但 `_runnable_instance_for_agent` (features/scheduling/service.py:917-925) **没**调用 `worker_in_project`

**P5 阶段修法**（合并到 RuntimeEligibilityService 重构）：
- §6.1 第 4 步 `worker_in_project(s, entry.worker_id, project_id)` 已写入 eligibility chain
- 旧 `_runnable_instance_for_agent` 保留作为"agent 物理可运行" 的子检查，但 project 授权走 eligibility 链路

### 7.5 P0 Design #4 — Worker identity composite key → 推迟

- 当前 spec 决策 C（按 worker 隔离）已经签字 + main 实现按此走
- Review 推荐的 `(user_id, agent_name)` 复合 key 推迟到 Phase 3（多 worker 跨用户协作时再做）
- 短期不阻塞 happy path

### 7.6 "Worker" 命名冲突 → P7 修

见 §2。**这是 P7 commit 的第一优先级**。

### 7.7 两套 Worker Agent 配置（Python worker.py + .NET ProposalWorker） → P7 + 后续合并

**当前状态**：
- Python `agentboard/worker.py` 通过 local SQLite 读 Agent config
- .NET `src/workers/AgentBoard.ProposalWorker/appsettings.Local.json` 硬编码 6 个 adapter slot（WorkBuddy / MiniMax / Codex / Qwen / Fake / Scenario）

**P7 阶段修法**：
- 命名规范：C# 项目重命名 `AgentBoard.Node`
- 配置迁移：.NET Node 也读 `~/.codebuddy/agents.db`（跟 Python 共享 schema），不再读 `appsettings.Local.json` 里的 `Agents:` section
- `appsettings.Local.json` 删 `Agents:` section，只留 `Node:` 元数据 + connection

**修完后**：Server → MQ targeted dispatch → Node → ExecutorFactory 查本地 SQLite → 选 Executor → 启动 CLI

### 7.8 RuntimeRegistry 还不是唯一 presence source

**当前状态**：
- `Agent.online` + `AgentInstance.online` 仍在 `list_runnable_candidates` filter 链
- `AgentCacheEntry.online` 也存在
- 三套真源（DB online / cache online / Agent 实际 readiness）

**P5/P6 阶段修法**：
- P5：RuntimeEligibilityService 只看 `AgentCacheEntry.online` + `ready`
- P6：删 `Agent.online` / `AgentInstance.online` 在 dispatch 路径的引用（保留 column 用于 audit）
- 注：DB `Agent.online` 仍作为 audit 字段，但**不**用于 dispatch 决策

### 7.9 ProjectWorkspaceRegistry 缺失 → P5 落地

**当前状态**：server 不知道 project 在 worker 上的本地路径。

**P5 修法**：
- Worker SQLite 加 `project_workspaces` 表（见 §4.1 schema）
- WSS HELLO/DELTA 帧加 `project_workspaces: [{project_id, local_path}]`
- Server cache 加 `project_workspaces: dict[int, dict[worker_id, str]]` 镜像
- `RuntimeEligibilityService` 第 4 步之后加：worker 端必须 `project_workspaces[project_id]` 存在
- MQ message 加 `expected_workspace_path` 让 worker 二次确认

### 7.10 Dispatch 是 broadcast 抢任务（不是 targeted）

**当前状态**：
- PR-10 已经改成 `dispatch_implementation_task` 选 agent → 但**MQ 路由还是 per-agent queue + 抢**
- 不是真正 targeted

**P5 修法**：
- MQ message body 加 `target_worker_id`（server 选好的）+ `target_agent_id`
- Worker 收到后**只**消费分配给自己的 message
- 替代当前 "per-(worker, agent) queue" 模型为 "per-worker queue + target_agent_id body filter"

**为什么 P5 一起做**：P5 改 WSS frame 时同步改 MQ 协议，避免双重改动。

### 7.11 Agent ≠ Adapter 严格化（AgentsOptions 硬编码）

**当前状态**：
- `src/workers/AgentBoard.ProposalWorker/AgentsOptions.cs` 硬编码 6 个 slot
- 改 Agent = 改代码 + 重新部署

**P7 修法**：
- 删 `AgentsOptions`
- 改 `ExecutorFactory`：根据 `Agent.executor_type` 选 `IExecutor` 实现
- `IExecutor` 抽象：`async Task<RunResult> ExecuteAsync(AgentDefinition agent, ProjectWorkspace ws, AssignmentRequest req)`
- 实现：`CodexExecutor` / `CodebuddyExecutor` / `ClaudeExecutor` / `FakeExecutor`（测试用）
- 加 Agent = 改 worker SQLite 一行 + 推 DELTA，**不改代码不重新部署**

### 7.12 Permission enforcement 缺失

**当前状态**：任何 Agent 都能读写任意文件、访问任意网络、执行任意 shell。

**P5 (server-side decision) + P6+ (worker-side enforcement)**：
- Server 端：eligibility 第 5 步 `permissions_summary` filter
- Worker 端：wrapper 进程 + 沙箱（OS-level）— **本设计**不实现 kernel-level sandbox（spec #16 明确不做）
- 但 worker 端至少做：fs_scope **pre-check**（启动前 `os.path.realpath` 比对）+ shell_exec **monitor**（用 Python `secrets` 库或 `cgroups` 简化版）

**Phase 1 范围**：Server decision + Worker pre-check。Runtime enforcement 留到 Phase 2。

### 7.13 Windows Service 运行账号

**当前状态**：
- agentboard.localdeploy 脚本里 `appsettings.Local.json` 是当前 Windows User 跑的
- 历史上有人用 LocalSystem 部署，OAuth / Git config / keychain 全部失效（参考 memory）

**P7 修法**：
- install-service.ps1 显式要求选择：
  - `LocalSystem` — 显式 warn"无法访问 user OAuth / keychain"
  - `NT AUTHORITY\NetworkService` — 类似 LocalSystem
  - 当前 user (`%USERNAME%`) — **推荐**
  - 专用 `AgentBoardServiceAccount` — 需先创建
- 默认拒绝 LocalSystem（除非 operator 显式 `--allow-local-system` flag）

---

## 8. 实施阶段

### Phase 0 — 命名规范化（P7 第一个 commit，先做）

| Task | 文件 | 改动量 |
|---|---|---|
| Python: rename `features/workers/` → `features/processors/` | 多文件 | 中（import / 路径） |
| Python: rename `agentboard.worker` module → `agentboard.processors` | 多文件 | 中 |
| .NET: rename `src/workers/AgentBoard.ProposalWorker/` → `src/nodes/AgentBoard.Node/` | 1 project | 大 |
| .NET: rename `AgentBoard.ProposalWorker.exe` → `AgentBoard.Node.exe` | csproj | 小 |
| .NET: `AgentsOptions` 删 → `AgentBoardNodeOptions` | cs | 中 |
| 更新所有 doc / OpenSpec / architecture-v2 / mcp-agent-guide 引用 | 多 | 中 |
| 更新 deploy 脚本路径 | deploy scripts | 中 |
| **功能不变** | — | — |

测试：`pytest -m e2e` 跑通 + .NET 174/174 绿。

### Phase 1 — 数据所有权 + Permissions 扩展（P5）

| Task | 文件 | 改动量 |
|---|---|---|
| Worker SQLite 加 `extra_prompt` / `permissions_*` / `project_workspaces` 表 | `agent_runtime/cli_storage.py` | 中 |
| WSS 协议扩展（`permissions_summary` / `permissions_detail` / `project_workspaces`） | `agent_registry_ws.py` | 中 |
| `AgentCacheEntry` 字段扩展（`executor_type` / `model` / `roles` / `capabilities` / `permissions_summary` / `ready` / `current_load` / `max_concurrency` / `config_revision`） + 删 `cli_command` 字段 | `agent_registry_cache.py` | 中 |
| 新建 `RuntimeEligibilityService`（统一 chain） | `features/scheduling/service.py` | 中 |
| 切 `list_runnable_candidates` 到新 service（保留旧函数 deprecated） | 同上 | 中 |
| MQ message body 加 `target_worker_id` + `expected_workspace_path` | `core/infrastructure/messaging/rabbitmq.py` | 中 |
| .NET ProposalWorker → Node 收到新 MQ 字段处理 | C# | 大 |
| WorkerProjectMapping 接入 eligibility | `RuntimeEligibilityService` | 小 |
| 新增 `transfer_project_ownership` + partial unique index migration | projects service + alembic | 中 |

测试：
- `tests/test_workflow_outbox.py` 仍 3/3
- 新 `tests/test_runtime_eligibility.py`（P5 acceptance）
- 新 `tests/test_transfer_project_ownership.py`（P7 #3 acceptance）
- `pytest -m e2e` 全绿

### Phase 2 — Presence 唯一化 + Server DB read-only（P6）

| Task | 文件 | 改动量 |
|---|---|---|
| `list_runnable_candidates` 删 `Agent.online` / `AgentInstance.online` filter | features/scheduling | 中 |
| `Agent.online` / `AgentInstance.online` 改为 audit 字段（仍写，但不读） | features/projects/models | 小 |
| `agent_instances` 加 deprecation flag（参照 spec 决策 G） | alembic | 小 |
| Operator 文档：手动删 legacy rows（spec 决策 F） | docs/ | 小 |

测试：`pytest` 全绿 + 新 acceptance test "dispatch 不读 DB online"。

### Phase 3 — Worker 端 enforcement（推后）

- Wrapper 进程 + fs_scope monitor + shell_exec monitor
- OS-level sandbox（暂不实现，spec #16 明确不做）

### Phase 4 — Multi-worker 跨用户 / Agent scoring（暂不实现）

- (user_id, agent_name) composite identity（按 review 方案 B）
- Capability scoring

---

## 9. 完成标准

### Phase 0 完成

- [ ] `features/workers/` 路径全 codebase 0 命中
- [ ] `agentboard.worker` import 全 codebase 0 命中
- [ ] `src/workers/AgentBoard.ProposalWorker/` 路径 0 命中
- [ ] `AgentsOptions` 类 0 命中
- [ ] 所有 docs / OpenSpec / 注释 / 测试名 用 `Node` 不用 `Worker`（指 client 端执行节点）
- [ ] `pytest -m e2e` 跑通 + .NET 174/174 绿

### Phase 1 完成

- [ ] `RuntimeEligibilityService` 跑通 7 步 chain
- [ ] `WorkerProjectMapping.enabled` 在 dispatch 路径生效
- [ ] WSS 帧加 `permissions_summary` / `project_workspaces`，server cache 同步
- [ ] MQ targeted dispatch 生效（worker 只消费分配给自己的）
- [ ] `transfer_project_ownership` 实现 + partial unique index
- [ ] 多 owner `add_project_member` 拒绝
- [ ] `pytest` 全绿 + 新 e2e `test_runtime_eligibility.py` + `test_transfer_ownership.py`

### Phase 2 完成

- [ ] `list_runnable_candidates` 0 引用 `Agent.online` / `AgentInstance.online`
- [ ] DB schema 加 `agent_instances.deprecated_at` 列
- [ ] `agent-ephemeral-2026-09` spec status → `accepted`

### 终极验收（所有 Phase 完成后）

- [ ] Server DB **没有** `agents` / `agent_instances` runtime 写入（仅 audit）
- [ ] Operator 改 agent 只改 worker SQLite → 推 DELTA → server cache 立即看到
- [ ] Worker restart → HELLO → cache 重建 → dispatch 恢复
- [ ] Agent online 但 worker 未映射 project → 不能执行
- [ ] Review/QA/Implementation 全部走同一 eligibility pipeline
- [ ] MCP `runtime_list_agents` 工具暴露（operator 通过 MCP Agent 查看 runtime state）

---

## 10. 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| Phase 0 命名改动大，PR merge 冲突多 | 一次大 PR + 多个 follow-up 修 import | 保持旧名字可 import（facade） |
| Phase 1 字段扩展破现有 fixture | flag-gated `AGENTBOARD_RUNTIME_RICH=1` | off 走老路径 |
| Phase 2 `Agent.online` 删 filter 后多 owner ghost dispatch 漏 | 先 audit 全部 `Agent.online` 引用 + 灰度切 | 立即 revert 那一行 |
| .NET Node 收到新 MQ 字段不识别 | 双协议（`target_worker_id` 可选，缺省时 fallback old path） | 老 Node 仍能跑老 MQ path |
| Worker 端 SQLite schema 升级失败（agent 启动前迁移） | 加 schema_version column + startup migration | 旧 schema 仍能读 |

---

## 11. 相关文档

- `docs/architecture-v2.md` — Server 端 FastAPI + .NET BFF 双栈（不变）
- `docs/dual-stack-bff-runbook.md` — BFF 切流（不变）
- `openspec/changes/agent-ephemeral-2026-09/` — P0-P4 已落地，P5+ 由本设计收口
- `openspec/changes/workflow-outbox-2026-08/` — DB+MQ atomicity（已落地）
- `docs/contracts/contract-freeze.md` — REST contract freeze（本设计**不**改 public schema，仅改 cache + 内部 service 字段，OK）

---

## 12. 这次设计**不**解决什么

显式列出以防 scope creep：

- ❌ Agent capability scoring 真实算法（留 Phase 4）
- ❌ Kernel-level sandbox
- ❌ Per-agent network firewall
- ❌ Server Portal 编辑 Worker Agent（central admin 留 Phase 4+）
- ❌ 完整 (user_id, agent_name) composite identity migration（Phase 4）
- ❌ Cost quota + permission audit（Phase 4）
- ❌ Server 缓存 `cli_path` / `prompt` / `credential`（**永久**不做）

---

**完成本设计后**：每个流程 block（"X 不存在" / dispatch 失败 / owner 缺失 / worker 越权 / ghost agent / 命名混淆）有且仅有一个 clear 解决路径。

下次新流程 block 出现时，按本文档 §7 的模板（Block 名 / 当前状态 / 修法 / 所属 Phase）追加。
