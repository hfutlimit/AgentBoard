# 实施任务清单（Dual-Stack BFF）

> 配套 `proposal.md` + `design.md`。每条都是可独立交付、可勾选、可回滚的最小单元。
> 阶段与 design §9 一一对应。

## 阶段 0：脚手架（1 sprint）

- [ ] 建 `dotnet/` 目录结构（`src/AgentBoard.Api`、`tests/AgentBoard.Api.Tests`、`contracts/`、`migrations/`）
- [ ] 选 .NET 版本（8 LTS 或 9 STS）+ ASP.NET Core 模板（`webapi --no-openapi false`）
- [ ] `dotnet/contracts/openapi-v3.json` 拉取脚本（FastAPI 启动后 `curl /openapi.json` + sha256 落盘）
- [ ] `dotnet/contracts/openapi-v3.sha256` CI 校验（防止 .NET 端漂移）
- [ ] NSwag 生成 client → `src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs`（占位，不用）
- [ ] 实现 `/api/health`（与 FastAPI `{"status":"ok"}` 1:1）
- [ ] 实现 `/api/meta`（types/statuses/priorities/sprint_statuses/schedule_types/run_statuses）
- [ ] Serilog + OpenTelemetry 接入
- [ ] CI 卡口：`schema-drift-check.py` 比对 .NET 端 mock response vs FastAPI 真实 response
- [ ] docker-compose `api-dotnet` 服务（端口 18000，仅 health/meta 可用）
- [ ] README 临时加一段「.NET BFF 迁移中」说明
- [ ] 文档：跑通阶段 0 后写 `docs/dual-stack-bff-runbook.md`（本地启动 + 切流 + 回滚）

## 阶段 1：只读业务迁 .NET（2-3 sprints）

### 1.1 EF Core 模型与迁移
- [ ] EF Core 8/9 + Pomelo.EntityFrameworkCore.MySql 接入
- [ ] `Domain/Entities/`：User、ApiKey、Project、Epic、Story、Sprint、Task、Bug、Comment、Attachment、Document、DocumentFolder、DocumentComment、DocumentRevision、AuditLog、Notification、WebhookConfig、WebhookDelivery
- [ ] `dotnet/migrations/` 用 `dotnet ef migrations add InitialReadOnly` 生成
- [ ] **不自动 apply**到生产；SQL 提交 `migrations/versions/dotnet_xxx.sql`，由 FastAPI Alembic apply（同库同 schema）
- [ ] CI 卡：禁止 .NET migration 文件包含 "DROP" / "TRUNCATE"

### 1.2 业务 GET 端点
- [ ] auth router：GET /api/auth/me
- [ ] identity router：GET /api/api-keys、GET /api/users/me/projects
- [ ] projects router：GET /api/projects、GET /api/projects/{id}、GET /api/projects/{id}/epics、GET /api/epics/{id}、GET /api/epics/{id}/stories、GET /api/stories/{id}、GET /api/projects/{id}/sprints、GET /api/sprints/{id}、GET /api/projects/{id}/members
- [ ] work_items router：GET /api/tasks（搜索：project/epic/story/type/status/priority/q + 分页）、GET /api/tasks/{id}、GET /api/tasks/{id}/comments、GET /api/comments/{id}、GET /api/attachments/{id}
- [ ] documents router：GET /api/documents、GET /api/documents/{id}、GET /api/documents/{id}/revisions、GET /api/document-folders
- [ ] search router：GET /api/search
- [ ] admin router：GET /api/audit-logs、GET /api/health、GET /api/meta
- [ ] 所有 GET 端点支持 API Key 鉴权（`api:read` scope）

### 1.3 鉴权与中间件
- [ ] `Middleware/AuthMiddleware`：HMAC Token 验证（复用 `AGENTBOARD_SECRET`）
- [ ] `Middleware/ApiKeyMiddleware`：API Key digest 验证 + permission scope
- [ ] `Middleware/CorsMiddleware`：与 FastAPI 同 `AGENTBOARD_CORS_ORIGINS` 配置
- [ ] `Middleware/RequestIdMiddleware`：生成 `X-Request-Id` 透传
- [ ] `Middleware/AuditLogMiddleware`：审计写入（origin="dotnet"）

### 1.4 契约测试
- [ ] 每个 GET 端点写 contract test（FastAPI vs .NET 响应 diff）
- [ ] CI 跑 contract test 必须全绿
- [ ] 错误响应：404/409/422/400/401/403 全部 1:1

### 1.5 影子流量
- [ ] nginx `upstream api_shadow` 配置，0% 流量打 .NET（仅记录）
- [ ] 观察 24h：无 5xx + 响应时间 < 50ms 增量为合格
- [ ] contract test + 影子流量全绿 → 进入阶段 2

## 阶段 2：写迁 .NET + 新功能（3-4 sprints）

### 2.1 业务 POST/PUT/PATCH/DELETE
- [ ] auth router：POST /api/auth/register、POST /api/auth/login、PATCH /api/auth/me、POST /api/auth/change-password
- [ ] identity router：POST /api/api-keys（创建/撤销）
- [ ] projects router：POST/PATCH/DELETE 全部 project/epic/story/sprint/member 端点
- [ ] work_items router：POST/PATCH/DELETE task/comment/attachment + PUT /api/tasks/{id}/status + POST /api/tasks/{id}/spec/append + POST /api/tasks/{id}/generate-subtasks
- [ ] documents router：POST/PATCH/DELETE document/folder/revision/comment（含乐观锁）
- [ ] admin router：审计日志只读

### 2.2 乐观锁与冲突检测
- [ ] 所有可写表加 `row_version BIGINT` 列（已有复用；迁移脚本走 Alembic）
- [ ] EF Core `[ConcurrencyCheck]` 标注 + 异常映射 409
- [ ] FastAPI 端对齐：version_id_col 配齐
- [ ] 写冲突测试：同 row 并发写 → 一胜一败，败者 409

### 2.3 Webhook 派发（.NET 新做）
- [ ] `Features/Webhooks/Service/WebhookDispatcher.cs`：Channel<T> + Polly retry/backoff
- [ ] 签名算法：HMAC-SHA256(body, secret) → `X-Webhook-Signature` 头（与 FastAPI webhooks service 1:1）
- [ ] 死信表 `webhook_deliveries`（status: pending/success/failed/dead）
- [ ] 指数退避（1s/4s/16s/64s/256s 后 dead）
- [ ] HostedService 消费 outbox 写 webhook_deliveries
- [ ] 端点：POST /api/webhooks（创建）、GET /api/webhook-configs、POST /api/webhook-configs、DELETE /api/webhook-configs/{id}
- [ ] E2E：外部测试服务收到回调 + 签名校验通过

### 2.4 通知中心（.NET 新做）
- [ ] `Features/Notifications/Service/NotificationCenter.cs`
- [ ] 多通道 adapter：邮件（SMTP）/ 站内（DB）/ IM（webhook 出站，预留接口）
- [ ] 模板渲染：基于 RazorLight 或 Scriban（强类型模板）
- [ ] HostedService 消费 `notification.requested` 事件
- [ ] 端点：GET /api/notifications（分页）、PATCH /api/notifications/{id}/read、DELETE /api/notifications/{id}
- [ ] 触发：评论 @mention、Story 状态变更、Task 指派、Review 请求（事件订阅）

### 2.5 SignalR Hub
- [ ] `Hubs/AgentStateHub.cs`：snapshot + agent_state + ping
- [ ] `Microsoft.AspNetCore.SignalR` 接入
- [ ] Angular：`@microsoft/signalr` 替换原生 WebSocket
- [ ] 鉴权：query string 传 `?access_token=...`（SignalR 标准方式）
- [ ] 端点：/hubs/agents
- [ ] 过渡期 FastAPI /ws/agents 保留 1 sprint
- [ ] E2E：连上 hub 收 snapshot → Agent 状态变化收到推送

### 2.6 灰度切流
- [ ] `scripts/traffic-split.sh`：nginx weight 配置 + reload
- [ ] 阶段 1% → 10% → 50% → 100% 灰度
- [ ] 监控：错误率、延迟、CPU、内存；任一超阈值自动回滚
- [ ] 切流期间 FastAPI 业务写入关闭（`AGENTBOARD_FASTAPI_WRITE_DISABLED=1`）
- [ ] 100% 切流后观察 1 周

### 2.7 MCP 验证
- [ ] .NET 加 MCP reverse proxy：/mcp → FastAPI :8001/mcp
- [ ] fastmcp client 切到 .NET 端测试
- [ ] Bearer Token 透传验证

### 2.8 E2E
- [ ] Playwright 跑完整业务流（CRUD + 状态机 + spec + 评论 + 通知 + Webhook + SignalR）
- [ ] 影子流量统计 vs 真实流量统计对比

## 阶段 3：FastAPI 内部化 + 收尾（1-2 sprints）

- [ ] FastAPI 业务 router 下架（保留 features/proposals/scheduling/learning/workers/mcp）
- [ ] FastAPI 对外端口移除（仅内网）
- [ ] 移除 nginx `api_legacy` upstream
- [ ] 移除 FastAPI 业务写相关 schema（Pydantic 模型可保留引用，路由下架即可）
- [ ] `openspec/specs/agentboard/spec.md` 同步：架构章节改写
- [ ] 变更归档：`mv openspec/changes/dual-stack-bff-restructure openspec/changes/archive/`
- [ ] README 架构图重画（双栈图）
- [ ] 跑通 Playwright E2E + pytest 全套 + .NET test 全套
- [ ] 删除 `docs/dual-stack-bff-runbook.md` 中"灰度期"段（永久化到 `docs/architecture-v2.md`）

## 横切关注点（贯穿所有阶段）

- [ ] OpenAPI 漂移 CI 卡口（每次 FastAPI 发版前必跑）
- [ ] audit_logs 双侧写入（origin 字段标识 python/dotnet）
- [ ] trace context 透传（`traceparent` header）
- [ ] Serilog + OpenTelemetry 双栈共用
- [ ] secrets 同一份（AGENTBOARD_SECRET、MARIADB_*、RABBITMQ_*）
- [ ] docker-compose 化所有新服务
- [ ] runbook：`docs/dual-stack-bff-runbook.md` 持续更新
- [ ] 用户偏好遵守：每完成一个阶段 → commit + push（不批量）
