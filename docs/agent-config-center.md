# Agent 配置中心 + 定期探测 + WebSocket 实时状态

> 状态：已实现并端到端验证（2026-08-09）
> 相关：docs/workbuddy-cli-integration.md、docs/minimax-code-integration.md、Ticket 全流程

## 1. 要解决的问题

旧模型：Agent 只能经 MCP `agent_register` 自报身份，CLI 命令与模型绑定在单条
`cli_command` 里，无法在界面上配置；前端 Agent 池状态靠手动刷新，无实时性。

新模型（本次交付）：

```
前端 Agent 页（创建/编辑/删除/立即探测） ──REST──▶ API(agents 表)
                                                        ▲
Worker ──定期 probe（cli_command + {model} 注入）──▶ heartbeat/deregister + 详情
                                                        │
前端 Agent 页 ◀──WebSocket /ws/agents 实时广播──────────┘
```

## 2. 核心能力

### 2.1 同一 CLI 多 Agent + 模型可选

`agents.cli_command` 支持 **`{model}` 占位符**，配合新增 `agents.model` 字段：

```
cli_command: "C:/.../codebuddy.exe" -p -y --model {model} --mcp-config C:/.../mcp.json
model:       hy3                     ← Agent A（codebuddy + hy3）
model:       deepseek-v4-flash       ← Agent B（同 codebuddy + deepseek）
```

Worker probe 与拉起时按各 Agent 的 `model` 注入占位符——同一条 CLI 模板即
多 Agent 多模型。`model` 为空时占位符被移除。

### 2.2 Worker 定期 probe（保活）

- 周期 `AGENTBOARD_WORKER_HEARTBEAT_INTERVAL`（默认 60s），逐 Agent 执行
  `<cli_command 注入 model> --version`（`AGENTBOARD_WORKER_AGENT_TIMEOUT` 超时）；
- 成功 → `POST /api/agents/{id}/heartbeat`（`probe_ok=true` + 版本详情）；
  失败 → `deregister`（`probe_message` 带原因：超时/找不到/退出码）；
- `enabled=false` 或未配 `cli_command` 的 Agent 跳过；
- 结果落 `probe_message` / `last_probe_at`，前端实时可见。

### 2.3 WebSocket 实时状态

- 端点 `GET /ws/agents`（`?token=` 可选，REQUIRE_AUTH=1 时必带登录 token）；
- 连接后先推 `{"type":"snapshot","agents":[...]}` 全量快照；
- 之后所有状态变更实时推送：
  - `{"type":"agent_state","agent":{...}}` —— register / PUT / probe / heartbeat / deregister；
  - `{"type":"agent_deleted","agent_id":"..."}` —— DELETE；
  - `{"type":"ping"}` —— 30s 保活心跳（nginx/IIS 代理防断连）；
- 实现：进程内 `AgentStateHub`（订阅者队列，`put_nowait` 线程安全，
  同步 REST 端点任意线程可广播，规避跨事件循环 send 的限制）；
- 前端指数退避重连（1s→30s，仅 Agent 页活跃时）。

## 3. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/agents/register` | 幂等注册（MCP/自报），带 `model` |
| PUT | `/api/agents/{agent_id}` | 前端配置更新（name/roles/cli_command/model/enabled/user_id 全可选） |
| DELETE | `/api/agents/{agent_id}` | 删除注册记录 |
| POST | `/api/agents/{agent_id}/probe` | 手动立即探测（同步 `--version`，`{timeout}` 可调） |
| POST | `/api/agents/{agent_id}/heartbeat` | 保活；body 可带 `probe_ok`/`probe_message` |
| POST | `/api/agents/{agent_id}/deregister` | 下线；body 可带 `probe_message` |
| GET | `/api/agents` | 列表（`?online=&role=` 过滤） |
| WS | `/ws/agents` | 实时状态（快照 + agent_state + agent_deleted + ping） |

## 4. 前端

- 侧栏「🤖 Agents」→ Agent 池：统计卡（总数/在线/停用）+ 卡片列表；
- 每张卡片：模型徽标、probe 详情（`🛰 OK v1.2.3` / 失败原因 + 时间）、
  心跳时间、操作按钮（⚡探测 / ✏编辑 / 🗑删除）；
- 「＋ 新建 Agent」表单：agent_id（创建后不可改）/ 名称 / CLI 命令模板
  （支持 `{model}`）/ 模型 / 角色 JSON / 启用开关；
- WebSocket 自动连接：进入页面即订阅，任何状态变更即时刷新（含其它
  标签页/其它 Worker 的变更）。

## 5. 验证

- 单测：`tests/test_agent_config_ws.py`（15 项：CRUD/probe 结果落库/状态翻转/
  Hub 订阅广播/WS 快照+广播/`_probe_cli_sync` 五分支）+ worker 心跳测试
  （`{model}` 注入、enabled 跳过、超时降级）；
- 端到端（本地 API 18099 + worker + WS 订阅客户端）：
  - 同 CLI 建 4 个 Agent（hy3 / deepseek-v4-flash / MiniMax-M2 / 坏命令）；
  - worker 一轮 probe：3 在线 1 离线，`probe_message` 带版本/原因；
  - WS 实时收到 4 条 `agent_state` 广播；PUT 改名 → `agent_state`；
    DELETE → `agent_deleted`；快照含全量。

## 6. 部署注意

- 生产 nginx/IIS 反代需开启 WS upgrade 头（`Upgrade: websocket` /
  `Connection: Upgrade`），否则 `/ws/agents` 握手失败（前端自动降级为
  手动刷新，功能不中断）；
- Docker：`docker compose build api web` 后重启（生产 compose 不挂载源码）。
