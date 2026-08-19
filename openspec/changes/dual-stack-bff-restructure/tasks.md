# 实施任务清单（Dual-Stack BFF）

> 配套 `proposal.md` + `design.md`。每条都是可独立交付、可勾选、可回滚的最小单元。
> 阶段与 design §9 一一对应。

## 阶段 0：脚手架（1 sprint）

### S0-1: .NET 10 WebAPI 解决方案脚手架（2026-08-19 完成）

- [x] 建 `dotnet/` 目录结构（`src/AgentBoard.Api`、`tests/AgentBoard.Api.Tests`、`contracts/`、`migrations/`）
- [x] 选 .NET 版本 → **.NET 10**（`global.json` pin 10.0.301）
- [x] `dotnet/AgentBoard.slnx`（.NET 9+ 新格式，VS 2022 17.10+/Rider 2024+ 兼容）
- [x] `dotnet/Directory.Build.props`（nullable / warnings-as-errors / LangVersion=latest）
- [x] `dotnet/.editorconfig`（编码风格 + C# 规则）
- [x] `dotnet/.gitignore`（bin/obj/logs/coverage/）
- [x] `dotnet/global.json`（SDK pin 10.0.301 + rollForward=latestFeature）
- [x] `dotnet/src/AgentBoard.Api/Program.cs`（含根端点 + MapControllers + OpenAPI）
- [x] `dotnet/src/AgentBoard.Api/AgentBoard.Api.csproj`（.NET 10 + OpenApi 10.0.9 + Microsoft.OpenApi 2.0.1 override）
- [x] `dotnet/src/AgentBoard.Api/appsettings.json` + `appsettings.Development.json`
- [x] `dotnet/src/AgentBoard.Api/Properties/launchSettings.json`（端口 18000）
- [x] `dotnet/tests/AgentBoard.Api.Tests/`（xUnit + SmokeTests 占位）
- [x] `dotnet/contracts/README.md`（契约冻结机制说明）
- [x] `dotnet/migrations/README.md`（migration 流程 + 不自动 apply）
### 后续 Story

- [ ] `dotnet/contracts/openapi-v3.json` 拉取脚本（S0-4）
- [ ] `dotnet/contracts/openapi-v3.sha256` CI 校验（S0-4）
- [ ] NSwag 生成 client → `src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs`（S0-4）
- [ ] 实现 `/api/health`（S0-5）
- [ ] 实现 `/api/meta`（S0-5）
- [ ] Serilog + OpenTelemetry 接入（S0-7）
- [ ] docker-compose `api-dotnet` 服务（S0-6）
- [ ] `docs/dual-stack-bff-runbook.md`（S0-8）

---

## S0-2: Repository Pattern 基础架构（EF Core，避免 Include）

### 落地清单（2026-08-19）

- [x] `src/AgentBoard.Domain/` 纯 C# classlib
  - Common: Entity / IAuditableEntity / ISoftDeletable / IDomainEvent / ValueObject / DomainException + 4 子类
  - Common/Enums: ItemType / Status / Priority / SprintStatus
  - Identity: User（含 UserCreatedEvent / UserPasswordChangedEvent）
- [x] `src/AgentBoard.Application/` classlib（不引 EF Core）
  - Abstractions: IService / IProvider / IRepository<T> / IDbContext / IUnitOfWork / IClock / ICurrentUser
  - Common: PagedRequest / PagedResponse / QueryExtensions (WhereIf + ApplyPaging)
- [x] `src/AgentBoard.Infrastructure/` classlib
  - Persistence: AppDbContext + AppDbContextDesignTimeFactory
  - Persistence/Configurations: UserConfiguration（snake_case 列名映射 FastAPI 库）
  - Persistence/Interceptors: AuditFieldsInterceptor / SoftDeleteInterceptor / DomainEventDispatcherInterceptor
  - Persistence/Repositories: Repository<T> 通用 + IUserRepository / UserRepository
  - Time: SystemClock
  - DependencyInjection: AddInfrastructure(IConfiguration)
  - Migrations: InitialEmpty
- [x] `tests/AgentBoard.Infrastructure.Tests/` xUnit
  - TestDbContextFactory (InMemory + Interceptors)
  - RepositoryCrudTests (5 用例)
  - UserRepositoryTests (3 用例)
  - AuditFieldsInterceptorTests (2 用例)
  - Performance/RepositoryPerformanceBaselineTests (2 用例)
  - SmokeTests

### 验收

- [x] `dotnet build` 0 errors
- [x] `dotnet test` 14/14 通过（Api 1 + Infrastructure 13）
- [x] `dotnet ef migrations add InitialEmpty` 成功
- [x] 性能基线：1000 行 InMemory 查询 < 50ms
- [x] **EF Core 全程无 Include**（用 Select 投影 + 显式 LINQ Join）
- [x] IDbContext 抽象：Application 层无 EF Core 引用

### 关键设计决策

- Pomelo.EntityFrameworkCore.MySql 10.0.0 尚未发布（最新 9.0.0），阶段 0/1 用 SQLite 替代；MySQL provider 留到 Pomelo 发版
- IDbContext 抽象不暴露 DbSet<T>（避免泄漏 EF Core 语义）
- IRepository<T>.AddAsync 返回 Task<T>（便于拿到 EF 跟踪的实体）
- DomainEventDispatcherInterceptor 不创建 nested scope（直接用传入的 IServiceProvider）
- DesignTimeFactory 用 SQLite in-memory（不依赖 Program.cs 启动）

### 踩坑（沉淀到项目记忆）

- EF Core 10 interceptor 基类 `SaveChangesInterceptor` 用 `DbContextErrorEventData` 而非 `SaveChangesFailedEventData`
- AppDbContext 同时实现 IDbContext 和 IUnitOfWork（接口相同 SaveChangesAsync 签名）
- `Repository<T>.AddAsync` 必须返回 `Task<T>` 才能与 `IRepository<T>` 接口签名匹配
- 测试项目 namespace 嵌套时，相对引用 `Persistence.Repositories.UserRepository` 解析到错误路径，必须用全限定名
- `IServiceProvider.GetServices(IEnumerable<T>)` 要求容器注册 `IEnumerable<T>`；EmptyServiceProvider 需要对 IEnumerable<T> 返回空数组
- `dotnet ef migrations add` 的 startup project 必须引 Microsoft.EntityFrameworkCore.Design + 能构造 DbContext；用 DesignTimeFactory 绕开 Program.cs 启动

---

## S0-3: 分层骨架（BaseController / Auth 示例 / NetArchTest）

### 落地清单（2026-08-19）

- [x] **Application 层补完**
  - `Identity/IUserService.cs` + `UserService.cs`（User CRUD + 密码验证 stub）
  - `Identity/IAuthProvider.cs` + `AuthProvider.cs`（Login / GetCurrent / ChangePassword）
  - `Identity/Dtos/UserDto.cs` + `CreateUserRequest.cs` + `AuthSessionDto.cs` + `LoginRequest.cs`
  - `Abstractions/IUserRepository.cs`（**从 Infrastructure 搬到 Application**，符合 Clean Architecture）
  - `DependencyInjection.cs`（`AddApplication()` 注册 Services + Providers）
- [x] **Api 层基础**
  - `Api/Common/ApiError.cs`（`{"detail": "..."}` 统一错误包装，1:1 兼容 FastAPI）
  - `Api/Common/DomainExceptionFilter.cs`（`IExceptionFilter`：DomainException → HTTP 状态码）
  - `Api/Base/BaseController.cs`（基类 + `BaseController<TProvider>` 泛型版）
  - `Api/Conventions/ApiRouteConvention.cs`（去掉 controller 名的 "Api" 前缀）
  - `Auth/CurrentUserService.cs`（从 Infrastructure/Auth 移到 Api/Auth，ICurrentUser 的 HTTP 实现）
  - `AssemblyMarker.cs`（NetArchTest 引用用）
- [x] **Auth 示例 feature**（端到端跑通）
  - `Features/Auth/AuthController.cs` → `IAuthProvider` → `IUserService` → `IUserRepository`
  - 端点：POST /api/auth/login + GET /api/auth/me + POST /api/auth/change-password
  - 全部走 BaseController<TProvider> 注入 Provider，**不直接调 Service**
- [x] **Program.cs 改造**
  - `AddHttpContextAccessor()` + `AddApplication()` + `AddInfrastructure(config)` + ICurrentUser 注册
  - DomainExceptionFilter 全局注册
  - ApiRouteConvention 路由约定
  - Dev 环境 `EnsureCreated()` 自动建 SQLite 表

### 验收

- [x] `dotnet build` 0 errors 0 warnings
- [x] `dotnet test` 19/19 通过（Api 1 + Infrastructure 18）
- [x] **NetArchTest 5 条架构规则全绿**：
  - Controllers 不依赖 IRepository / IDbContext / IUnitOfWork / EF Core
  - Controllers 不直接依赖 Service（必须经 Provider）
  - Application 层不依赖 Infrastructure / Api / EF Core
  - Domain 层不依赖任何其他层
  - IRepository 实现只在 Infrastructure
- [x] **端到端 5 场景全绿**（真实 HTTP 调 dotnet run）：
  - POST /api/auth/login 正确密码 → 200 + token
  - POST /api/auth/login 错密码 → 422 "invalid credentials"
  - GET /api/auth/me 无 header → 401 "authentication required"
  - GET /api/auth/me 带 X-User-Id=1 → 200 完整 UserDto
  - GET /api/auth/me 带 X-User-Id=999 → 404 "User with key '999' was not found."

### 关键设计决策

- **Repository 接口搬到 Application**（Clean Architecture 修正）：`IUserRepository` 从 `Infrastructure.Persistence.Repositories` 搬到 `Application.Abstractions`，实现仍留 Infrastructure
- **CurrentUserService 移到 Api**（依赖方向修正）：`IHttpContextAccessor` 是 ASP.NET Core 抽象，Infrastructure 不应该引
- **Dev 自动建表**：`db.Database.EnsureCreated()` 仅 Development 环境调，生产仍由 Python Alembic 运维
- **BaseController 泛型版**：`Controller → BaseController<TProvider>` 强类型访问 Provider
- **DomainExceptionFilter 全局拦截**：替代 Controller try-catch 模板

### 踩坑（沉淀到项目记忆）

1. `Infrastructure` 不应该依赖 ASP.NET Core（`IHttpContextAccessor`）→ `CurrentUserService` 移到 `Api/Auth`
2. Repository 接口按 Clean Architecture 应该在 `Application` 层，实现在 `Infrastructure`（一开始放错位置了）
3. `VerifyPasswordAsync` stub 设计用 `password` 直接当 hash 存（无前缀），不要双重加 `plain:` 前缀
4. SQLite dev 库不会自动建表，要在 Program.cs 显式 `db.Database.EnsureCreated()`（dev only）
5. NetArchTest `HaveDependencyOn` 用类型 FullName 精确匹配（如 `IRepository\`1`），避免同 namespace 误伤

### 下一步

---

## S0-4: OpenAPI 契约冻结机制

### 落地清单（2026-08-19）

- [x] `scripts/sync-openapi.ps1` — 拉 FastAPI `/openapi.json` → 写 `dotnet/contracts/openapi-v3.json` + `openapi-v3.sha256`
- [x] `scripts/schema-drift-check.py` — sha256 校验 + 可选 live 漂移检测
- [x] `scripts/generate-fastapi-client.ps1` — NSwag 14.5 生成 C# Client
- [x] `dotnet/contracts/openapi-v3.json` + `.sha256` — 占位（后续 sync-openapi.ps1 替换）
- [x] `dotnet/src/AgentBoard.Api/Clients/AgentBoardFastApiClient.cs` — NSwag 生成（31KB）
- [x] `.github/workflows/dotnet-contract-check.yml` — CI 卡口（hash + regen + build + test）
- [x] `docs/contracts/contract-freeze.md` — 契约冻结规则 + 变更流程

### 验收

- [x] `python scripts/schema-drift-check.py` 0 drift
- [x] `pwsh scripts/generate-fastapi-client.ps1` 成功生成 31KB Client
- [x] `dotnet build` 0 errors
- [x] `dotnet test` 19/19 通过
- [x] NSwag 默认生成 Newtonsoft.Json 风格（Api 端加 Newtonsoft.Json 13.0.3 编译通过）

### 关键决策

- **NSwag 14.5 是 net9.0 binary**，在 .NET 10 上通过 `DOTNET_ROLL_FORWARD=Major` 跑通
- **Hash 算法统一为 raw bytes**（Python + PowerShell 一致），不用 sorted-keys
- **占位 openapi-v3.json 包含真实示例 schema**（LoginRequest / AuthSession / User / Error），后续 sync 直接覆盖
- **CI workflow** 包含 hash-check + regen-client + build + test；live drift check 暂时注释（CI 没有 FastAPI runtime）

### 踩坑

1. NSwag 14.5 不识 `/generateEqualityComparers:false` 参数
2. NSwag 默认用 Newtonsoft.Json，Api 项目需显式加 `Newtonsoft.Json 13.0.3` 包
3. PowerShell `Set-Content -Encoding UTF8` 写带 BOM，python hash 比较失败；改用 `[System.IO.File]::WriteAllText` + `UTF8Encoding($false)`
4. PowerShell 没有 `?.Source`（nullable 简写）语法，必须分多行
5. `pwsh` 命令不在 Windows PowerShell 5.1 默认 PATH；脚本里不依赖 pwsh 直接用 `powershell -NoProfile -File` 兜底

### 下一步

---

## S0-5: /api/health & /api/meta 端点实现（1:1 兼容 FastAPI）

### 落地清单（2026-08-19）

- [x] `Features/Health/HealthController.cs` + `HealthResponseDto.cs`
  - `GET /api/health` 返 `{status, database, version, timestamp}` 与 FastAPI 1:1
  - 走 BaseController<IHealthProvider> → HealthProvider → HealthService → IDbContext
  - 不依赖 EF Core（架构测试通过）
- [x] `Features/Meta/MetaController.cs` + `Dtos/MetaResponseDto.cs`
  - `GET /api/meta` 返 6 个 enum 列表（types / statuses / priorities / sprint_statuses / schedule_types / run_statuses）
  - **wire format snake_case**（`[JsonPropertyName]` 锁定）匹配 FastAPI
  - 值硬编码（与 FastAPI `core/common/enums.py` 1:1）
- [x] `Application/Health/IHealthService.cs` + `HealthService.cs` + `IHealthProvider.cs` + `HealthProvider.cs`
  - Service 调 IDbContext.CanConnectAsync（EF Core 内部实现）
  - Provider 装配 version 常量（`HealthProvider.ApiVersion`）
- [x] `Application/Abstractions/IDbContext.cs` 加 `CanConnectAsync` 抽象
- [x] `Infrastructure/Persistence/AppDbContext.cs` 实现 `CanConnectAsync`（`Database.CanConnectAsync`）
- [x] `Application/DependencyInjection.cs` 注册 IHealthService + IHealthProvider
- [x] 测试：
  - `tests/AgentBoard.Api.Tests/Features/MetaControllerTests.cs`（3 用例：enum 完整性 + 公开 + wire format snake_case）
  - `tests/AgentBoard.Api.Tests/Features/HealthControllerTests.cs`（2 用例：shape 验证 + 公开）
- [x] `tests/AgentBoard.Api.Tests` 加 `Microsoft.AspNetCore.Mvc.Testing 10.0.0` + `FluentAssertions 7.0.0`
- [x] `dotnet/contracts/openapi-v3.json` 加 `/api/health` + `/api/meta` paths + `HealthResponse` / `MetaResponse` schemas
- [x] `scripts/write-snapshot.py` 新增（避免 PowerShell 写 CRLF 导致 hash 不一致）
- [x] NSwag 重新生成 Client（62KB，含 health/me 端点）

### 验收

- [x] `dotnet build` 0 errors
- [x] `dotnet test` 24/24 通过（Api 6 + Infrastructure 18）
- [x] **端到端 5 场景全绿**（真实 HTTP）：
  - `GET /api/health` → 200 `{status, database, version, timestamp}`
  - `GET /api/meta` → 200 6 个 snake_case enum 列表
- [x] `python scripts/schema-drift-check.py` 0 drift
- [x] NetArchTest 5 条架构规则全绿（HealthController 通过 Provider/Service 间接调 IDbContext）

### 关键设计决策

- **Health 端点走 Provider/Service 模式**（不直连 IDbContext）→ 满足"Controller 不依赖 IDbContext"架构规则
- **Meta DTO wire format snake_case** 用 `[JsonPropertyName]` 显式锁定，绕过 ASP.NET Core 默认 camelCase
- **写 JSON 用 Python**（不用 PowerShell）—— PowerShell `WriteAllText` 写 CRLF，python 读时 hash 不匹配
- **Enum 值硬编码**（不从 Domain 读）—— Domain enum 与 FastAPI 不一致（6 状态 vs 5 状态；dev/bug vs dev/bug/qa/design），后续 stage 1 业务迁移时统一

### 踩坑（已沉淀到项目记忆）

1. Controller 直接依赖 IDbContext/EF Core 触发 NetArchTest fail → 重构为 Provider/Service 模式
2. ASP.NET Core 默认 System.Text.Json 输出 camelCase，与 FastAPI snake_case 不匹配 → `[JsonPropertyName]` 锁定
3. PowerShell `WriteAllText` 默认 CRLF 换行，与 python raw-bytes hash 计算不一致 → 改用 python 写文件
4. `ConvertTo-Json` 自动加 trailing newline，叠加后文件末尾多一个换行 → 去掉 `+ "`n"` 后缀

### 下一步

---

## S0-6: docker-compose api-dotnet 服务接入

### 落地清单（2026-08-20）

- [x] `docker-compose.yml` 新增 `api-dotnet` service
  - 端口 18000 → 容器内 8080
  - 共享 `AGENTBOARD_SECRET`（与 FastAPI 同一密钥）
  - 默认 SQLite（Stage 0 不连 MariaDB，Stage 1 Pomelo 10.0 接入后再共享）
  - Healthcheck: `wget --spider http://localhost:8080/`
  - 独立 volume `agentboard_dotnet_data`
- [x] `docker-compose.dev.yml` 把 api-dotnet 放到 `dotnet` profile（host 跑 `dotnet watch`）
- [x] `.env.example` 加 `AGENTBOARD_DOTNET_PORT=18000`
- [x] `examples/nginx-agentboard.conf` 加 `upstream api_dotnet { ... }` + grayscale 切流注释
  - `proxy_next_upstream error timeout http_500/502/503` + `tries 2` 实现 fallback
  - FastAPI = primary，.NET BFF = backup（Stage 0），Stage 2 调权重
- [x] `scripts/dev-up.sh` + `dev-up.ps1` — 一键双栈启动
- [x] `scripts/dev-down.sh` + `dev-down.ps1` — 停止（支持 -WithVolumes 清数据）
- [x] `README.md` 加 .NET 10 BFF 启动说明（dotnet run / dotnet test / pwsh dev-up）

### 验收

- [x] `dotnet build` 0 errors
- [x] `dotnet test` 24/24 通过
- [x] docker-compose.yml YAML 语法校验通过（Docker 未在本机，CI 验证）
- [x] 6 个服务编排（api / api-dotnet / web / mcp / db + nginx 注释示例）齐整

### 关键设计决策

- **api-dotnet 默认用 SQLite**（不连 MariaDB）—— Stage 0/1 数据写仍由 FastAPI 主导；Stage 2 切流后用 EF Core Pomelo 接 MariaDB
- **nginx upstream 注释** 写 grayscale 切流方案：FastAPI primary + .NET backup → 后续改权重做 A/B
- **dev compose 把 api-dotnet 放 profile** —— host 跑 `dotnet watch` 比容器 hot-reload 快
- **双版本启停脚本**（sh + ps1）—— Windows 用 ps1，Linux/Mac 用 sh

### 踩坑

1. .NET 容器内端口 ASP.NET Core 默认 8080（不是 5000/5001），需要 `ASPNETCORE_URLS` 或 launchSettings 显式
2. `healthcheck` 的 `wget` 在 aspnet:10 镜像里可用（不需装 curl）
3. PowerShell 5.1 写文件默认 CRLF，sh 脚本里 `curl -s` 调用没问题但需要保持 sh 语法

### 下一步

S0-7: Serilog + OpenTelemetry 接入

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
