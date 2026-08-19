# Dual-Stack BFF 设计稿

> 配套 `proposal.md`。本文件聚焦**怎么拆、怎么不拆、怎么切流**。

---

## 0. 现状速读（决定为什么要这样拆）

| 指标 | 数值 | 影响 |
|------|------|------|
| FastAPI features | 12 个垂直切片 | 已经"按 feature 切好"，.NET 可一比一对齐 |
| 最大 service 文件 | `features/projects/service.py` 1679 行 | 业务复杂度高，必须保留 |
| MCP | 已上 fastmcp 3.4（Streamable HTTP） | 留 FastAPI 是最稳的 |
| WebSocket | `/ws/agents` 1 个端点（Agent 状态广播） | 迁 SignalR 一次性小改 |
| MQ | RabbitMQ 事件总线，`.mq` 模块 | 跨栈事件总线天然合适 |
| DB | 双驱动：SQLite（dev）/ MariaDB（prod） | .NET 端不接 SQLite（dev 模式共用 FastAPI） |
| 鉴权 | HMAC Bearer + API Key 双轨 | 透传 Token 即可 |
| 测试 | pytest 全套（含 Playwright E2E） | 切流期间 FastAPI 必跑，.NET 端独立测试 |

**关键洞察**：项目已经按 feature 垂直切完了，.NET 端不需要再"切"，只需要**一比一对齐每个 feature 的接口契约**。这是单点决策、批量搬家的好前提。

---

## 1. 目标架构图

### 1.1 运行时拓扑

```
                            ┌─────────────────────────────────┐
                            │   外部 (公网/Ingress TLS)        │
                            └────────────┬────────────────────┘
                                         │
                ┌────────────────────────┼────────────────────────┐
                │                        │                        │
        ┌───────▼───────┐       ┌────────▼────────┐      ┌────────▼────────┐
        │ Angular SPA   │       │ 外部 SDK        │      │ MCP Client      │
        │ (Web 8080)    │       │ (B2B/ERP)       │      │ (Codex/Claude)  │
        └───────┬───────┘       └────────┬────────┘      └────────┬────────┘
                │ fetch / signalr         │ https+auth            │ https+bearer
                └────────────────────────┼─────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
                │         ASP.NET Core WebAPI  (BFF + 业务)        │
                │                                                  │
                │  /api/*  业务 REST（兼容 FastAPI 契约 1:1）     │
                │  /hubs/agents  SignalR（替代 /ws/agents）       │
                │  /api/webhooks/*  第三方回调（强类型）          │
                │  /api/reports/*  聚合查询（EF Core 直查）       │
                │  /api/notifications/*  多通道推送               │
                │                                                  │
                │  内部 Channel/HostedService:                     │
                │    - WebhookDispatcher  (Polly retry/backoff)   │
                │    - NotificationCenter (邮件/IM/站内)         │
                │    - ReportAggregator  (大查询 + 缓存)         │
                └─────┬───────────────────────────┬───────────────┘
                      │ EF Core                   │ HttpClient (Polly)
                      │ (Pomelo MySQL)            │ (内部 mTLS or 内网白名单)
                      │                           │ 透传同一 Authorization
                ┌─────▼──────┐             ┌─────▼──────────────────┐
                │  MariaDB   │◄────────────┤  FastAPI AI 服务        │
                │  (共享)    │  共享表     │  (内网 :8000，不外暴)   │
                └────────────┘             │                          │
                                           │  /internal/ai/*          │
                                           │  /internal/agents/run    │
                                           │  /internal/proposals/*   │
                                           │  /internal/scheduling/*  │
                                           │  /internal/learning/*    │
                                           │  /internal/workers/*     │
                                           │  /mcp  (Streamable HTTP) │
                                           └──────┬───────────────────┘
                                                  │ pika
                                                  ▼
                                            ┌──────────┐
                                            │ RabbitMQ │
                                            └──────────┘
```

### 1.2 构建/部署拓扑

```
docker-compose
├── api-dotnet  ←── 新增（.NET 8/9，对外 18000）
├── api         ←── 保留（FastAPI，对外 18000 改内网 8000，不暴露）
├── mcp         ←── 保留（FastAPI 8001/mcp，内网）
├── web         ←── 保留（FastAPI 静态托管 28080）
├── mariadb     ←── 保留
├── rabbitmq    ←── 保留
└── nginx       ←── 改：api-dotnet → 18000（生产）；api → 仅内网白名单
```

---

## 2. Feature 归属矩阵

> "留/迁/拆" 决策表。`留` = FastAPI 永久保留；`迁` = .NET 接管对外（FastAPI 仍可内部调用直到下架）；`拆` = 拆成两部分

| Feature              | 归属     | 对外 URL         | .NET 端实现                  | FastAPI 端保留理由             |
|----------------------|----------|------------------|------------------------------|--------------------------------|
| **auth**             | 迁       | `/api/auth/*`    | IdentityService + JWT/HMAC 校验 | -                              |
| **identity** (ApiKey)| 迁       | `/api/api-keys/*`| 同上                         | -                              |
| **projects**         | 迁       | `/api/projects/*`, `/api/epics/*`, `/api/stories/*`, `/api/sprints/*` | ProjectService (EF Core) | - |
| **work_items** (Task/Comment/Attachment) | 迁 | `/api/tasks/*`, `/api/comments/*`, `/api/attachments/*` | WorkItemService | - |
| **documents**        | 迁       | `/api/documents/*` | DocumentService | - |
| **notifications**    | 拆→迁    | `/api/notifications/*` | NotificationCenter (新做) | -                              |
| **webhooks**         | 拆→迁    | `/api/webhooks`, `/api/webhook-configs` | WebhookDispatcher (新做) | - |
| **search**           | 拆→迁    | `/api/search/*`  | ReportAggregator (聚合部分) | -                              |
| **admin** (audit/health) | 迁   | `/api/admin/*`, `/api/health`, `/api/audit-logs` | AdminService | - |
| **proposals**        | 留       | (内部 `/internal/proposals/*` 或经 .NET 代理) | 调 FastAPI | 状态机+澄清对话，AI 编排密集 |
| **scheduling** (Agent/Schedule/Run) | 留 | (内部) | 调 FastAPI | Cron 解析、Agent 注册、心跳 |
| **learning**         | 留       | (内部)          | 调 FastAPI                    | LLM judge + memory，纯 AI      |
| **workers**          | 留       | (内部)          | 不需要，FastAPI 自跑          | 异步执行链，Python 优势        |
| **executor**         | 留       | (内部)          | 不需要                        | CLI 拉起 codex/minimax，Python 优势 |
| **mcp**              | 留       | `/mcp`（.NET 端可加 reverse proxy） | 直接调 FastAPI MCP | fastmcp 3.4 生态最熟 |
| **mq** (RabbitMQ)    | 共享     | -                | 消费端（事件订阅）             | 生产端（事件发布）              |

**关键边界规则**：
1. **同一张表同一时间只允许一边写**——通过 feature flag + 表前缀分组（详见 §6 数据访问边界）。
2. **MCP** 不迁：fastmcp 的 Streamable HTTP + TokenVerifier 已是事实标准；.NET 端只做 reverse proxy。
3. **proposals / scheduling / learning**：业务查询由 .NET 调 FastAPI 拿数据（避免直接读 AI 专属表），但创建/更新走 .NET 写主表 + 发 MQ 事件让 FastAPI 处理 AI 部分。

---

## 3. 对外契约不变性清单（**冻结规则**）

> 这是整个迁移的**宪法**。所有 .NET 实现必须严格匹配；任何偏差视为破坏性变更，必须有 deprecation 流程。

### 3.1 契约来源

**FastAPI `/openapi.json` 是单一事实源**（生成时间戳化版本 `openapi-v3.json`，每次 build snapshot 存到 `dotnet/contracts/openapi-v3.json`）。

> 为什么不反过来让 .NET 先生成？因为现有 FastAPI 已有 Pydantic schema，Web/MCP 客户端在用，反向重写成本远大于"以 FastAPI 为准、.NET 对齐"。**契约冻结 ≠ 永远不变**，而是变更走 RFC + 双侧 PR。

### 3.2 不变性 checklist

| 维度 | 不变规则 | 校验方法 |
|------|----------|----------|
| URL 路径 | 完全一致（含大小写、连字符、版本号） | Contract test：FastAPI ↔ .NET schema diff |
| HTTP method | 完全一致 | 同上 |
| Path/Query 参数名 | 完全一致 | 同上 |
| Request body schema | JSON 字段名/类型/required/默认值 1:1 | 同上 |
| Response body schema | 同上（含 `null` vs missing 区分） | 同上 |
| HTTP 状态码 | 业务含义 1:1 | 同上 |
| 错误格式 | `{"detail": "..."}` (FastAPI 默认) | 同上 |
| 鉴权 Header | `Authorization: Bearer v1.<payload>.<sig>` | E2E |
| API Key 格式 | `abk_` 前缀 + HMAC digest | E2E |
| CORS | `AGENTBOARD_CORS_ORIGINS` 透传 | 配置项一致 |
| Rate limit | 暂未实现，未来 .NET 加必须给 deprecation 窗口 | - |
| 限流维度（X-RateLimit-*） | 同上 | - |
| 审计日志字段 | `entity_type/entity_id/action/uid/path/body/status_code/duration_ms` 字段名 1:1 | 字段级 schema 校验 |
| Idempotency-Key | 暂未实现，未来 .NET 加必须与 FastAPI 一致 | - |
| 已知小破坏 | WebSocket `/ws/agents` → SignalR `/hubs/agents` | 前端切流时一次性改 |

### 3.3 契约变更流程

任何想改契约的 PR 必须：
1. 在 `openspec/changes/<id>/breaking-contract-change.md` 写 RFC（影响面+迁移期）
2. 同时改 FastAPI 和 .NET（双 PR，CI 校验 schema diff）
3. 至少 2 周 deprecation 窗口（旧字段 `deprecated: true`，新字段 `new_field`）
4. Angular / MCP 客户端迁移完毕后才删

---

## 4. .NET 调 FastAPI（内部反向调用）

> 这是 .NET 主控、FastAPI 退守后的核心通信模式。

### 4.1 通道设计

| 通道 | 用途 | 鉴权 | 重试/熔断 | 性能预算 |
|------|------|------|-----------|----------|
| **HTTP (Polly)** | AI 同步调用（judge / spec 提案生成） | 透传用户原 Token | 3 次指数退避 + CircuitBreaker | < 500ms P99 |
| **RabbitMQ 事件** | AI 异步触发（agent run / worker 处理） | 服务账号 mTLS 或内网白名单 | 死信队列 + 手动重投 | < 5s 入队 |
| **gRPC (可选)** | 高频内部调用（agent 心跳聚合） | mTLS | 同上 | < 50ms P99 |

> 现阶段只做 HTTP + MQ。gRPC 留作未来性能瓶颈时的优化。

### 4.2 HTTP 通道契约

```
[.NET ApiController]
    │  HttpClient (Polly: Retry+CB+Timeout)
    │  Header: Authorization: Bearer <原用户 Token>
    │  Header: X-Internal-Caller: agentboard-dotnet/<version>
    │  Header: X-Request-Id: <同 ASP.NET Core HttpContext.TraceIdentifier>
    ▼
[FastAPI /internal/ai/*]
    │  middleware: 校验 X-Internal-Caller 白名单 + Token 有效性
    │  business: 复用 features/learning/proposals/scheduling 现有 service
    ▼
[response 透传]
```

**关键点**：
- **Token 透传**：.NET 已经校验过用户身份，调 FastAPI 时把同一 `Authorization: Bearer ...` 原样转发（FastAPI 不再二次校验业务权限，只校验 token 有效 + X-Internal-Caller）。
- **路径隔离**：FastAPI 暴露 `AGENTBOARD_FASTAPI_INTERNAL_ONLY=1` 时，所有 `/api/*` 强制要求 `X-Internal-Caller` 头（生产默认开），无头 403。`.NET webhooks/notifications` 等纯内部流程用服务账号 Token。
- **超时分层**：AI 同步 30s（judge 允许 60s），AI 异步永远走 MQ。
- **取消传播**：ASP.NET Core `HttpContext.RequestAborted` 透传 `CancellationToken`，避免用户断开后 .NET 还在傻跑。

### 4.3 客户端代码生成

```bash
# 在 dotnet/ 目录执行
nswag openapi2csclient \
    /input:contracts/openapi-v3.json \
    /output:src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs \
    /namespace:AgentBoard.Api.Clients \
    /generateClientInterfaces:true \
    /generateDtoTypes:true \
    /useHttpClientCreationMethod:true
```

- **冻结快照**：`openapi-v3.json` 每次 FastAPI 发版后人工/脚本同步，commit 进仓；`dotnet/contracts/openapi-v3.sha256` 校验，CI 校验未变。
- **不漂移**：CI 跑 `nswag ... /compareTo:contracts/openapi-v3.json`，生成的 client 提交进仓。如果 schema diff 但 client 没改 → fail。
- **绕过生成**：个别接口手写 `DelegatingHandler`（如需要 traceparent 注入）。

### 4.4 错误传播

- FastAPI 返回 4xx/5xx JSON：`{"detail": "..."}` → .NET 透传 status code + body 给 Angular。
- FastAPI 5xx → .NET 不吞错，按 Polly 策略重试；3 次后 502 给前端 + 落 audit log + 触发告警。
- FastAPI timeout → 504 给前端。
- 业务异常（`core.exceptions.*`）→ 1:1 映射成 ASP.NET Core `ProblemDetails` 仍用 `{"detail": "..."}` 形态（保持外部契约）。

---

## 5. 数据访问边界（共享 MariaDB 治理）

> 双栈直连同一份库是**最快落地但最容易踩坑**的方案。本节是防雷区。

### 5.1 表归属

按"写入主导"原则分三组：

| 组 | 表示例 | 写入主导 | 读取允许 | 备注 |
|----|--------|----------|----------|------|
| **业务主表** | `projects, epics, stories, tasks, comments, sprints, attachments, documents, document_revisions, document_folders, document_comments, users, api_keys, webhooks, webhook_deliveries, notifications, audit_logs` | .NET | 双侧可读 | 切流期间 FastAPI 仍可读，**写操作走 .NET**（feature flag `AGENTBOARD_FASTAPI_WRITE_DISABLED=1`） |
| **AI 专属表** | `proposals, proposal_rounds, proposal_questions, agents, agent_schedules, agent_runs, learning_*` | FastAPI | 双侧可读 | 切流期间 .NET 写操作一律走 HTTP 调 FastAPI |
| **事件/队列** | `mq_events, outbox` | 双侧（append-only） | 双侧 | 仅追加；只读侧用 LISTEN/NOTIFY 模式 |

### 5.2 写入边界（**最重要**）

**核心原则**：同一张表同一时间只允许一边写。

实现机制：
1. **迁移期**（默认 1-2 个 sprint）：`AGENTBOARD_FASTAPI_WRITE_DISABLED=1`（生产），FastAPI 业务 router 启动时直接 503 写入端点；只读（GET）继续服务（用于 fallback）。
2. **ORM 隔离**：
   - .NET 端 EF Core 实体类**不复用** Python model 名空间；独立 `dotnet/src/AgentBoard.Api/Domain/Entities/*.cs`，字段名 1:1 映射。
   - .NET 端 migration 用 `dotnet/ef migrations add ...`，生成 SQL 后**人工 review**（不允许 EF 自动 apply 到生产）→ 提交到 `migrations/versions/dotnet_xxx.sql`，Alembic 配置文件 exclude 掉 .NET 写的表（避免双 migration runner 打架）。
3. **写冲突检测**：
   - 加 `row_version BIGINT` 列（已有则复用），所有写用乐观锁；EF Core `[ConcurrencyCheck]`、SQLAlchemy `version_id_col`。
   - 不允许双侧同事务写同一行（应用层硬性约束：API 文档明确说明"此表 .NET 主写，FastAPI 调用 HTTP 转发"）。
4. **读一致性**：
   - 默认 READ COMMITTED；统计/报表查询用 `READ COMMITTED + WITH (NOLOCK)` 兼容 MariaDB（MySQL 忽略）。
   - 大聚合查询走 .NET 只读副本（未来部署扩展，本期不实现）。

### 5.3 Schema 同步

- **不允许单边加列**：任何 schema 变更必须双 PR（FastAPI Alembic + .NET EF migration） + CI 跑 `schema-drift-check.py` 比对两套 migration 落库后的表结构。
- **outbox 表** 双栈共享：.NET 写 outbox（事务内），后台 hosted service 轮询发 MQ 事件 → FastAPI 消费；对称方向同理。

### 5.4 部署态切换脚本

```bash
# 阶段 1：.NET 灰度 1%
./scripts/traffic-split.sh set api-dotnet 0.01
# 观察 24h（错误率、延迟、CPU）

# 阶段 2：灰度 10% / 50% / 100%
./scripts/traffic-split.sh set api-dotnet 0.10
./scripts/traffic-split.sh set api-dotnet 0.50
./scripts/traffic-split.sh set api-dotnet 1.00

# 阶段 3：.NET 全量后，FastAPI 写入关闭
./scripts/toggle-fastapi-writes.sh disable
# 观察 1 周

# 阶段 4：FastAPI 对外端口关闭
./scripts/retire-fastapi-public.sh
# FastAPI 改 internal-only（仅 .NET 内网调用）
```

---

## 6. SignalR 接管 WebSocket

### 6.1 现状

- 端点：`/ws/agents`（FastAPI WebSocket）
- 客户端：Angular 用 `WebSocket` 原生 API + 30s 心跳
- 后端：`AgentStateHub`（订阅者队列，O(1) 广播）

### 6.2 目标

- 端点：`/hubs/agents`（ASP.NET Core SignalR）
- 客户端：Angular 用 `@microsoft/signalr` 替换原生 WebSocket
- 优势：自动 reconnect、groups/除外的定向推送、Azure SignalR Service 可直接接（未来扩缩容）

### 6.3 切换方案

1. .NET 端实现 `AgentStateHub : Hub`，订阅 `AgentStateHub.Groups`，行为 1:1 还原（snapshot → agent_state → ping）。
2. Angular 端把 `WebSocket` client 替换为 `signalr-client` 库（增量改动 < 50 行）。
3. 鉴权：SignalR 支持 query string + header 两种 token 传入，与现有 `Authorization: Bearer` 兼容。
4. 过渡期：FastAPI 的 `/ws/agents` 保留运行 1 个 sprint，新前端切到 `/hubs/agents` 后下架 FastAPI WebSocket router。

### 6.4 跨进程广播

如未来 .NET 多实例部署，SignalR 用 **Redis backplane**（`Microsoft.AspNetCore.SignalR.StackExchangeRedis`），单实例本期不接。

---

## 7. 鉴权与身份

### 7.1 Token 透传

| 场景 | Token 来源 | 验证方 | 备注 |
|------|------------|--------|------|
| Angular → .NET | 用户登录拿 `v1.xxx` | .NET 验 HMAC | 与 FastAPI 同一密钥 `AGENTBOARD_SECRET` |
| .NET → FastAPI | 原样透传 | FastAPI 验 HMAC | 加 `X-Internal-Caller` 头过白名单 |
| 外部系统 → .NET | API Key `abk_xxx` | .NET 验 digest + 权限 scope | 权限模型与 FastAPI 完全一致（`api:read`/`api:write`/`api:*`） |
| Webhook 入站（来自外部） | 签名头（HMAC of body + secret） | .NET WebhookDispatcher 验签 | 签名算法与 FastAPI webhooks service 兼容 |

### 7.2 用户身份

- .NET 端 `IUserContext` 从 `HttpContext` 解析 uid + is_admin + api_key 权限。
- 调 FastAPI 时把 `Authorization` 头原样转发，**不重写** user context（避免双重解析失败）。
- FastAPI 内部审计日志记 `X-Internal-Caller` 头，便于追查"谁替我调的"。

### 7.3 审计与可观测

- .NET 用 Serilog（结构化日志）+ OpenTelemetry（trace）；trace context 透传到 FastAPI（`traceparent` header）。
- 双栈共用同一个 Loki/ES 聚合；同一 `X-Request-Id` 串起来。
- audit_logs 表双侧都写（结构同 §3.2），加 `origin` 字段标识 `python` / `dotnet`。

---

## 8. 部署拓扑

### 8.1 docker-compose 调整

```yaml
services:
  api-dotnet:           # 新增
    build: ./dotnet
    environment:
      AGENTBOARD_SECRET: ${AGENTBOARD_SECRET}
      AGENTBOARD_DB_URL: ${MARIADB_URL}
      AGENTBOARD_FASTAPI_INTERNAL_URL: http://api:8000
      AGENTBOARD_FASTAPI_INTERNAL_TOKEN: ${AGENTBOARD_SERVICE_TOKEN}
    ports:
      - "18000:8080"    # 对外
    depends_on: [mariadb, api]

  api:                  # 保留，内网化
    # 移除 ports: 18000
    expose: ["8000"]
    environment:
      AGENTBOARD_FASTAPI_INTERNAL_ONLY: "1"

  mcp:                  # 保留
    expose: ["8001"]

  web:                  # 保留
    ports: ["28080:8080"]
    # 切流前指向 api-dotnet:18000，切流后改 .NET 入口
    environment:
      AGENTBOARD_WEB_API_URL: http://api-dotnet:8080

  nginx:
    # 切流期间双 upstream；切完后只留 api-dotnet
    config: |
      upstream api_backend { server api-dotnet:8080; }
      upstream api_legacy { server api:8000; }
      server {
        location /api/  { proxy_pass http://api_backend; }
        location /hubs/ { proxy_pass http://api_backend; }
        # 切换开关（注释/启用）
        # location /api/  { proxy_pass http://api_legacy; }
      }
```

### 8.2 本地开发

- `dotnet watch run` 启动 .NET（端口 18000）
- `uvicorn agentboard.api:app --port 8000` 启动 FastAPI（端口 8000）
- Angular `environment.apiBaseUrl` 临时改 `http://localhost:18000` 即可切到 .NET 调试
- 共享同一个 SQLite/MariaDB（dev 用 SQLite，.NET 端跳过 SQLite，dev 全用 FastAPI；prod 两边都接 MariaDB）

---

## 9. 演进路线（4 阶段）

> 每个阶段结束都有可演示产物 + 切流开关 + 回滚预案。

### 阶段 0：脚手架（1 sprint）
**目标**：搭好 .NET 工程 + 契约冻结 + CI 卡口；不切流。

- [ ] 建 `dotnet/` 目录（sln + Api + Tests + Contracts）
- [ ] `dotnet/contracts/openapi-v3.json` 从 FastAPI 拉取快照 + sha256 校验
- [ ] NSwag 生成 client（只读，不被实际调用）
- [ ] 实现 `/api/health` 端点（与 FastAPI health 1:1 返回）
- [ ] 实现 `/api/meta` 端点（types/statuses/priorities，与 FastAPI 1:1）
- [ ] Contract test：FastAPI /api/health vs .NET /api/health 响应 diff（snapshot 测试）
- [ ] CI 卡：schema drift check、生成 client 未更新报错
- [ ] 部署：api-dotnet 跑在 18000，仅 health/meta 可用

**退出标准**：打开 http://localhost:18000/api/health 返回 200 + 与 FastAPI 完全一致的 JSON。

### 阶段 1：只读业务迁 .NET（2-3 sprints）
**目标**：所有 GET 端点迁 .NET；不切流；FastAPI 仍服务所有写。

- [ ] auth router：register/login/me（GET 部分）
- [ ] identity router：api-keys GET
- [ ] projects router：list/get（project/epic/story/sprint/member）
- [ ] work_items router：list/get（task/comment/attachment）
- [ ] documents router：list/get
- [ ] search router（GET 聚合）
- [ ] admin router：audit-logs GET、health
- [ ] EF Core entities + migrations（只生成 schema，**不 apply 到生产**，人工 review 后用 FastAPI Alembic apply）
- [ ] Contract test 覆盖：每个 GET 端点的 request/response
- [ ] 影子流量：nginx 把 0% 流量打到 .NET（验证无 5xx），FastAPI 服务 100%

**退出标准**：所有 GET 端点 .NET 1:1 还原；contract test 全绿；连续 24h 影子流量无错。

### 阶段 2：写迁 .NET（3-4 sprints）
**目标**：所有 POST/PUT/PATCH/DELETE 迁 .NET；灰度切流；FastAPI 写入关闭。

- [ ] 业务 POST/PUT/PATCH/DELETE 全部实现（含乐观锁）
- [ ] Webhook 派发（.NET 新做）：HMAC 签名 + Polly 重试 + 死信
- [ ] 通知中心（.NET 新做）：Channel<T> + 多通道 adapter（邮件/IM/站内）
- [ ] SignalR Hub：/hubs/agents（与现有 /ws/agents 行为 1:1）
- [ ] Angular 切换 signalr-client（一次性 PR）
- [ ] 灰度切流脚本：`traffic-split.sh`
- [ ] E2E：Playwright 跑完整业务流（CRUD + 状态机 + spec 编辑）双栈
- [ ] MCP 验证：fastmcp client 切到 .NET（.NET 反向代理 `/mcp` → FastAPI）

**退出标准**：1% → 10% → 50% → 100% 灰度；100% 后关 FastAPI 写入；生产稳定 1 周。

### 阶段 3：FastAPI 内部化 + 收尾（1-2 sprints）
**目标**：FastAPI 退守为 internal-only；切流开关下架；spec 同步。

- [ ] FastAPI 对外端口移除（仅内网）
- [ ] FastAPI 业务 router 下架（保留 features/proposals/scheduling/learning/workers/mcp）
- [ ] openspec/specs/agentboard/spec.md 更新：标注 .NET 为新入口，FastAPI 为 AI 子系统
- [ ] 变更归档到 openspec/changes/archive/
- [ ] README 架构图重画
- [ ] 后续：报表聚合（.NET 新做）、AI 编排优化（FastAPI 继续）

**退出标准**：架构图与代码一致；外部只有 .NET 一个入口；FastAPI 8000 端口仅内网可达。

---

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| OpenAPI 漂移（FastAPI 改了 Pydantic schema，.NET 未同步） | 高 | 高 | CI 跑 schema diff；生成 client 强制 commit；breaking change 走 RFC 流程 |
| 同表双写竞态 | 中 | 极高 | 表归属 + 写入主导 + 乐观锁 + 切流期间 FastAPI 写关闭 |
| 鉴权 / Token 行为偏差 | 中 | 高 | 同一密钥 + 同一签发逻辑（.NET 用 `Microsoft.IdentityModel.Tokens` 实现同一 HMAC） + E2E 互测 |
| Angular 不兼容 | 低 | 中 | 契约冻结 + Playwright E2E 双栈 + 灰度切流 |
| SignalR 切流期前端错乱 | 中 | 中 | 双端点并行运行 1 sprint；前端一次性改 + 旧端点保留兜底 |
| .NET 团队对 Python 生态不熟 | 中 | 中 | FastAPI 不动；.NET 只做 CRUD + 集成；AI 部分仍是 Python 主导 |
| RabbitMQ 双栈消息重复消费 | 中 | 中 | Outbox 模式 + 消费侧幂等键；事件 payload 加 `event_id` |
| 部署拓扑变复杂 | 高 | 低 | docker-compose 化 + 切流脚本；先双栈后单栈 |
| 性能（跨进程调用） | 中 | 中 | HTTP + Polly；同步路径加 cache；异步路径走 MQ |

---

## 11. 开放决策（要用户拍板）

> 这些是 proposal 阶段没定死的点，影响 §4 / §5 / §9 实施细节。

1. **MCP 是否也走 .NET 反向代理？**（fastmcp 留 FastAPI 即可，.NET 加一层 reverse proxy）
2. **.NET 版本**：.NET 8 LTS（稳）还是 .NET 9 STS（新）？建议 .NET 9（SignalR 9 有新特性 + 11 月 LTS 化）
3. **.NET 团队规模**：单人 / 小组 / 完整 squad？影响阶段 2 的并行度
4. **报表聚合的需求**（统计 dashboard、BI 导出等）优先级：本期做还是阶段 3 后做？
5. **AI 编排是否要写 OpenAPI 内部子集**（让 .NET 反向 client 类型更准）？
6. **本地 dev 模式**：.NET dev 期跳过 SQLite，全用 FastAPI（仅 prod 双栈连 MariaDB）？还是 .NET 也支持 SQLite（testcontainer）？
7. **灰度切流工具**：nginx weight（最简单）/ k8s ingress / APISIX？项目当前用 docker-compose，建议先 nginx weight
8. **Angular 是否仍由用户维护**？.NET 端是否要做 SSR（Blazor United）以替换 SPA？还是仅 API 端切 .NET，Angular UI 不动？

---

## 12. 验收标准

- [ ] 所有现有 12 个 features 在 .NET 端有 1:1 实现（业务主表）或保留理由（AI 专属表）
- [ ] contract test 全绿（FastAPI schema ↔ .NET schema diff = 0）
- [ ] 双栈并发跑 100% 流量 1 周，错误率与单栈持平
- [ ] Playwright E2E 全业务流跑通（CRUD + 状态机 + spec + 评论 + 通知 + Webhook 派发 + SignalR）
- [ ] OpenAPI 单一事实源 + sha256 校验 + CI 卡口
- [ ] audit_logs 双侧写入格式一致（origin 字段标识来源）
- [ ] 切流脚本 + 回滚脚本 + runbook
- [ ] 文档：`docs/architecture-v2.md`、`docs/dual-stack-bff-runbook.md`、`README` 架构图更新
- [ ] `openspec/changes/dual-stack-bff-restructure/` 走完 proposal → design → tasks → archive
