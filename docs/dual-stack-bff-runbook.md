# Dual-Stack BFF 运维手册（Stage 0+）

> 本手册覆盖 .NET 10 WebAPI + FastAPI 双栈 BFF 阶段的本地开发、部署、灰度切流与回滚。
> Stage 0 仅搭好脚手架与契约冻结（`/api/health` / `/api/meta` 1:1 兼容 FastAPI），
> 真实业务切流发生在 Stage 2 灰度阶段。

## 1. 30 分钟跑通双栈

### 1.1 前置

| 工具 | 版本 | 说明 |
|---|---|---|
| .NET SDK | 10.0.301（`global.json` 已 pin） | `dotnet --version` 验证 |
| Python | 3.11+ | 现有 FastAPI 链路 |
| Docker + Compose | latest | 双栈一键启停 |
| PowerShell | 7+ | `pwsh` 跑 sh 兼容的 ps1 脚本 |

### 1.2 双栈一键启停

```powershell
# 仓库根目录
pwsh scripts/dev-up.ps1      # 起 5 服务：api(FastAPI) / api-dotnet / web / mcp / db
pwsh scripts/dev-down.ps1    # 停 + 保留 volumes
pwsh scripts/dev-down.ps1 -WithVolumes  # 停 + 删 volumes
```

`dev-up.ps1` 幂等：服务已在跑会跳过。可重复调用。

### 1.3 验证双栈健康

```powershell
# FastAPI 真源
curl http://localhost:8000/api/health
# → 200 {"status":"ok","timestamp":"..."}

# .NET 10 BFF
curl http://localhost:18000/api/health
# → 200 {"status":"ok","database":"ok","version":"0.1.0","timestamp":"..."}
# Header: X-Request-Id, traceparent

# 契约一致性
curl http://localhost:18000/api/meta | python -m json.tool
curl http://localhost:8000/api/meta  | python -m json.tool
# 两端 6 个 enum 列表必须完全一致
```

### 1.4 仅跑 .NET（不开 FastAPI）

适用于纯 .NET 端开发：

```powershell
cd dotnet
dotnet build
$env:AGENTBOARD_DOTNET_PORT = "18000"
$env:AGENTBOARD_ENV = "development"
$env:AgentBoard__Database__ConnectionString = "Data Source=agentboard-dev.db"
dotnet run --project src/AgentBoard.Api
```

注意：`launchSettings.json` 会覆盖 `AGENTBOARD_DOTNET_PORT`，如需换端口用 `--no-launch-profile` 或直接改 launchSettings。

### 1.5 仅跑 FastAPI（不开 .NET）

沿用旧文档，不变。

## 2. 项目结构（双栈视角）

```
AgentBoard/
├── agentboard/                 # FastAPI 真源（AI 子系统、复杂业务、内部管理）
│   ├── api.py                  # facade: lifespan + middleware + include_router
│   ├── features/               # 按 feature 切片的业务模块
│   ├── domains/                # 跨 feature 共享的领域原语
│   └── core/                   # 基础设施（DB / Auth / Cache / MQ）
├── dotnet/                     # .NET 10 BFF（对外 HTTP + 未来 SignalR）
│   ├── src/
│   │   ├── AgentBoard.Api/     # WebAPI 入口
│   │   ├── AgentBoard.Application/  # 业务组合层（Provider + Service）
│   │   ├── AgentBoard.Domain/  # 纯 C# 领域模型
│   │   └── AgentBoard.Infrastructure/  # EF Core + 仓储
│   ├── tests/                  # xUnit 测试
│   ├── contracts/              # OpenAPI 快照（FastAPI 真源 + sha256 钉）
│   ├── migrations/             # EF Core migrations（生产不使用）
│   └── README.md               # .NET 专项规约
├── frontend/                   # Angular 21 SPA
├── docs/
│   ├── architecture-v2.md      # 双栈架构图（本文档的姊妹）
│   ├── contracts/contract-freeze.md
│   └── dual-stack-bff-runbook.md  # ← 本文档
├── openspec/changes/dual-stack-bff-restructure/
│   ├── proposal.md
│   ├── design.md
│   ├── tasks.md                # 阶段 0~3 落地清单
│   └── code-structure.md       # .NET 端代码结构与命名规范
└── scripts/
    ├── dev-up.ps1 / dev-up.sh
    ├── dev-down.ps1 / dev-down.sh
    ├── sync-openapi.ps1        # 拉 FastAPI OpenAPI 快照
    ├── schema-drift-check.py   # 契约漂移检测
    └── generate-fastapi-client.ps1  # NSwag 生成 C# 客户端
```

## 3. 契约冻结（Contract Freeze）

详见 [`docs/contracts/contract-freeze.md`](contracts/contract-freeze.md)。要点：

- FastAPI 是公开 REST 契约的**唯一真源**。
- 任何契约变更必须先改 FastAPI → `pwsh scripts/sync-openapi.ps1` 拉快照 → 提交 `openapi-v3.json + sha256` → `pwsh scripts/generate-fastapi-client.ps1` 重生成 C# 客户端 → 同一 commit。
- CI workflow（`.github/workflows/dotnet-contract-check.yml`）跑 SHA256 比对，**漂移直接 fail**。

## 4. 切流（Stage 2 灰度）

> Stage 0 不涉及切流，nginx upstream 仍 100% 走 FastAPI。本节为 Stage 2 准备。

### 4.1 切流脚本（计划）

```powershell
# 待 Stage 2 实装，先留接口
pwsh scripts/cutover.ps1 -ApiDotnetWeight 10  # 10% 流量走 .NET
pwsh scripts/cutover.ps1 -ApiDotnetWeight 50  # 50/50
pwsh scripts/cutover.ps1 -ApiDotnetWeight 100 # 100% 走 .NET
pwsh scripts/cutover.ps1 -Revert             # 回滚到 0/100
```

`cutover.ps1` 修改 `nginx/nginx.conf` 的 `upstream api` 权重，平滑 reload nginx（`nginx -s reload`）。

### 4.2 切流前的硬性验证

| 验证项 | 命令 | 期望 |
|---|---|---|
| .NET 编译 | `dotnet build` | 0 Error |
| .NET 单测 | `dotnet test` | 全绿（Stage 0：24 用例） |
| 契约一致 | `python scripts/schema-drift-check.py` | 0 drift |
| 端到端 smoke | `curl /api/health` + `curl /api/meta` | 双栈响应 1:1 |
| .NET 健康 | 容器 `healthcheck` | `healthy` |
| 监控 | Prometheus / Serilog | 无 ERROR 级日志 |

### 4.3 切流后观察

切流后至少观察 1 小时，关注：

- nginx 5xx 错误率（`api_django_5xx_total` / `api_dotnet_5xx_total` 比例）
- p99 响应时间（双栈对比）
- .NET `Exception` Serilog 事件
- 数据库连接池使用率（EF Core `Database.Connection` 监控）

任何一项异常 → `pwsh scripts/cutover.ps1 -Revert` 立即回滚。

## 5. 回滚

### 5.1 立即回滚（毫秒级）

```powershell
pwsh scripts/cutover.ps1 -Revert   # nginx 切回 100% FastAPI
```

### 5.2 容器级回滚（10 秒级）

```powershell
pwsh scripts/dev-down.ps1 -WithVolumes
pwsh scripts/dev-up.ps1            # 全部用最新镜像重启
```

### 5.3 代码级回滚（分钟级）

```powershell
cd D:\AI\Projects\AgentBoard
git log --oneline -10               # 找稳定 commit
git revert <bad-commit>             # 生成回滚 commit
git push origin main                # CI 自动部署
```

### 5.4 数据库回滚

**Stage 0 不涉及**。.NET 端 `EnsureCreated()` 仅在 Development + Testing 环境跑，production 永远不建表，所有表由 FastAPI Alembic 管控。

> **未来如果 .NET 写路径上线，EF Core migrations 必须与 Alembic 互不冲突**：
> - Alembic 是真源，.NET migrations 仅作为影子比对。
> - 切流前先 `alembic upgrade head`，再 `dotnet ef database update` 验证一致性。

## 6. 常见问题 FAQ

### Q1: `dotnet test` 报 `HealthControllerTests.Get_Returns_200_And_Shape_That_Matches_FastAPI` 失败，`dto.Database.Should().Be("ok")` 不通过

A: 命中 `ApiWebApplicationFactory` per-instance temp SQLite 未建表的早期 bug。修复：Program.cs 的 `EnsureCreated` 改为 `IsDevelopment() || IsEnvironment("Testing")`（commit `6de19b4`）。如果你 fork 了代码，请确保 `Program.cs` 这一段没回退。

### Q2: `dotnet run` 启动时日志双写（每条 Serilog 事件出现 2 次）

A: Serilog console sink + OpenTelemetry console exporter 各自输出。功能上不影响，结构化字段（`@t` / `@mt` / `Application` / `MachineName`）正确。如需单一输出，注释 `Observability/OpenTelemetrySetup.cs` 里的 `AddConsoleExporter()`。

### Q3: `AGENTBOARD_DOTNET_PORT=18099` 设了 env，service 还是监听 18000

A: `Properties/launchSettings.json` 的 `environmentVariables.AGENTBOARD_DOTNET_PORT` 覆盖 shell env。两种解法：
- 改 launchSettings
- 用 `dotnet run --no-launch-profile`

### Q4: NSwag 在 .NET 10 上跑不起来，提示 `Microsoft.NETCore.App 9.0` not found

A: NSwag 14.5 是 net9.0 binary，需 `DOTNET_ROLL_FORWARD=Major` 启动：

```powershell
$env:DOTNET_ROLL_FORWARD = "Major"
pwsh scripts/generate-fastapi-client.ps1
```

### Q5: MySQL provider 报 `NotSupportedException: MySQL provider not yet wired in .NET BFF`

A: `Pomelo.EntityFrameworkCore.MySql 10.0.0` 暂未发布。当前用 SQLite 替代。跟踪：Epic 148 Story 308 follow-up。

### Q6: docker compose up 后 nginx 报 `connect() failed (111: Connection refused) while connecting to upstream`

A: 启动顺序问题。`api-dotnet` 健康检查通过前 nginx 已开始转发。`depends_on: { api-dotnet: { condition: service_healthy } }` 已配置；如仍出现，等 10s 再 `curl`。

### Q7: `dotnet test` 在 Windows 上偶发 `IOException` in Test Class Cleanup（ApiWebApplicationFactory Dispose）

A: e_sqlite3 mmap 句柄释放晚于 File.Delete。`ApiWebApplicationFactory.Dispose` 内已加 5×50ms 重试（commit `6de19b4`）。如 fork 后丢失可手动加回。

### Q8: Serilog 日志里看不到 `request_id` 字段

A: 需要 controller 内调用 `ILogger.LogXxx()`（即任何 Serilog 事件）。中间件已 push `LogContext`，但仅在 active 期间（请求处理中）的 Serilog 事件才有 `request_id`。响应头 `X-Request-Id` 不受此限制。

## 7. 监控与告警

### 7.1 日志

- **本地**：Serilog CLEF JSON 写到 `dotnet/logs/agentboard-dotnet-YYYYMMDD.log`（14 天滚动）
- **容器**：stdout → docker logs → 由 docker-compose 日志驱动收集
- **聚合**：未来接 Loki / ES（Stage 2）

### 7.2 追踪

- **本地**：OpenTelemetry console exporter 打印 Activity 到 stdout
- **聚合**：未来接 Jaeger / Tempo（OTLP exporter 在 `OpenTelemetrySetup.cs` 已留口子，未启用）

### 7.3 指标

Stage 0 不实现 Prometheus exporter。下个 Sprint 加 `OpenTelemetry.Exporter.Prometheus.AspNetCore`。

## 8. 安全护栏

- .NET 端默认 `Development` env，所有 controller 公开（与 FastAPI Stage 0 一致）
- `Production` env 启动时由 `validate_runtime_security()`（FastAPI 端）强制：要求 `AGENTBOARD_SECRET` ≥ 32 字节、`REQUIRE_AUTH=1`、CORS 白名单
- .NET 端 `Serilog` 不打印 `Authorization` header（自带的 SensitiveData scrubber 默认开）
- 数据库连接串在 appsettings 里，生产用 env var 覆盖

## 9. 阶段 0 → 阶段 1 升级路径

| 关注 | Stage 0（当前） | Stage 1（只读业务迁 .NET） |
|---|---|---|
| 数据库 | 影子 SQLite | 接 FastAPI 共享 MariaDB（应用层只读连接） |
| 端点 | `/api/health` / `/api/meta` | 增量：projects / epics / stories（GET only） |
| 鉴权 | 公开 | 沿用 FastAPI Bearer Token 解析 |
| Provider | HealthProvider / AuthProvider | ProjectProvider / EpicProvider / StoryProvider |
| Repository | 仅 UserRepository | 全套实体仓储 |
| DTO | snake_case（[JsonPropertyName]） | 沿用 OpenAPI 快照 1:1 |
| 测试 | 24 用例 | 单元 + 集成 + 契约 100+ 用例 |

## 10. 相关文档

- [`docs/architecture-v2.md`](architecture-v2.md) — 双栈架构图（mermaid）
- [`docs/contracts/contract-freeze.md`](contracts/contract-freeze.md) — 契约冻结规则
- [`openspec/changes/dual-stack-bff-restructure/proposal.md`](../openspec/changes/dual-stack-bff-restructure/proposal.md) — 改造提案
- [`openspec/changes/dual-stack-bff-restructure/design.md`](../openspec/changes/dual-stack-bff-restructure/design.md) — 架构设计
- [`openspec/changes/dual-stack-bff-restructure/tasks.md`](../openspec/changes/dual-stack-bff-restructure/tasks.md) — 阶段 0~3 落地清单
- [`dotnet/README.md`](../dotnet/README.md) — .NET 端规约
