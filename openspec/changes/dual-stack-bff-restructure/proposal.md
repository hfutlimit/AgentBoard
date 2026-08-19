# Dual-Stack BFF 重构（FastAPI + .NET WebAPI 长期共存）

> **状态**：草案（待用户 review）
> **范围**：新增 .NET 8/9 WebAPI 作为统一对外入口，FastAPI 保留为内部 AI 微服务
> **原则**：**零侵入现有 FastAPI 实现**，所有迁移走「绞杀者模式 + 契约冻结」

## TL;DR

把 AgentBoard 后端从「FastAPI 单体」演进为「**双栈 BFF**」：

```
[Angular Web] ──┐
[外部系统 SDK] ─┼─→ [ASP.NET Core WebAPI] ──→ [MariaDB]      （业务/集成/报表/通知/Webhooks/SignalR）
[MCP Client]  ──┘        │   │
                        │   ├─→ [FastAPI AI 服务]（executor / proposals / scheduling / learning / workers）
                        │   │       │
                        │   │       └─→ [MariaDB]   （共享同一份库，约定表边界）
                        │   └─→ [RabbitMQ]         （event bus，跨栈异步）
                        └─→ [MariaDB]              （共享同一份库）
```

- **.NET WebAPI = 唯一对外 HTTP 入口**（接管所有 `/api/*`，新增 SignalR Hub）。
- **FastAPI = 内部 AI 服务**（被 .NET 反向调用，不再对公网监听）。
- **对外契约冻结**：URL/方法/请求/响应 schema/Token 格式完全不变；Web 端可零修改切流到 .NET。
- **数据同库**：.NET 用 EF Core + Pomelo MySQL 直连同一份 MariaDB；通过"表归属+事务边界"避免双写竞态。
- **绞杀者模式**：FastAPI 一行代码不改；.NET 端逐 feature 上线，每上完一个就在网关切流 1→100%，全切完再下架 FastAPI 对外监听。

## 为什么做

| 痛点 | 现状 | 重构后 |
|------|------|--------|
| 外部系统接入成本高 | Python Pydantic schema，强类型客户端只能手写或 autogenerate+脱节 | .NET 暴露 OpenAPI，外部系统用 Kiota/NSwag/Refit 直接生成强类型 SDK |
| 实时推送方案原始 | `/ws/agents` 自管 WebSocket + 手工心跳 + 群广播脚本 | SignalR Hub（reconnect/groups/backplane/鉴权一体化） |
| 重试/限流/熔断 | httpx + 手写 retry | Polly（标准企业级 resilience pipeline） |
| 第三方集成代码散落 | webhooks service 1301 行同 proposals/scheduling 混在一起 | .NET 独立 Webhooks + 通知中心服务，强类型契约 |
| CPU 密集（未来报表/BI 聚合） | Python 写大聚合 SQL + 内存处理 | .NET LINQ-to-Entities + 编译期 SQL 校验 + 后台 Channel |
| 团队技术栈扩张 | 仅 Python 栈 | 双栈，吸纳 .NET 团队（用户已选） |

## 影响范围

| 层 | 影响 | 缓解 |
|----|------|------|
| `agentboard/api.py` / 各 `features/*/router.py` | **零改动**（直到全量切流） | 冻结；后续切流后下架 |
| `agentboard/mcp_server.py` | 保留（fastmcp 生态最熟） | 端口内网化，外部经 .NET MCP 网关转发 |
| `agentboard/features/workers/*` | 保留 | 不动 |
| `agentboard/features/scheduling/*` | 保留 | 不动 |
| `agentboard/features/learning/*` | 保留 | 不动 |
| `agentboard/features/proposals/*` | 保留 | 不动 |
| **新增** `dotnet/src/AgentBoard.Api/` | 新仓库/新目录 | 完全隔离，不影响 FastAPI 任何 import |
| `agentboard.db` / MariaDB | 表结构不变 | 加 `dotnet/migrations/` 用同库同 schema；不允许新增表（除非双方都加迁移） |
| 部署（docker compose） | 新增 `api-dotnet` 服务 | 与 `api` 并行；外部 nginx 只指 .NET |
| Angular Web | **零改动**（直到 .NET 全量切流） | 切流时配置 `environment.apiBaseUrl` 改指 .NET 即可 |
| MCP 客户端 | 暂零改动 | 后续可加 .NET MCP 转发层（本次不在范围） |

## 不做（明确边界）

- ❌ 不重写 FastAPI 业务代码（用户明确禁止）
- ❌ 不动现有 Pydantic schema（OpenAPI 冻结源）
- ❌ 不做大规模数据迁移（共用同一份 MariaDB）
- ❌ 不引入 Service Mesh（先用 nginx/k8s ingress 切流，必要时再上 Linkerd）
- ❌ 不重写 Angular Web 端
- ❌ 不动现有 WebSocket 路径到 SignalR 路径之外（切流时前端一次性小改用 `@microsoft/signalr`，仅此一处）

## 决策摘要（已与用户对齐）

| 决策点 | 选定方案 |
|--------|----------|
| 重构动机 | 企业集成 / 强类型 BFF（.NET 入口） |
| .NET 覆盖范围 | 第三方集成/Webhooks、统计/报表/聚合、通知中心；新增 SignalR |
| 数据库 | 共享同一份 MariaDB，.NET 直连（EF Core + Pomelo） |
| 鉴权契约 | 透传同一 Token（HMAC Bearer），OpenAPI 单一事实源 |
| FastAPI 定位 | 内部 AI 子系统（executor / proposals / scheduling / learning / workers / mcp） |
| 迁移策略 | 绞杀者模式 + 契约冻结 + 网关切流 |
| 新前端 | 暂不引入（保留 Angular；若有桌面/移动端需求另开变更） |
