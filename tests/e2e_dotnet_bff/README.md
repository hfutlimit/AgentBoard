# .NET BFF 后端 e2e 测试（pytest）

针对**双栈重构中新加的后端** `.NET BFF（AgentBoard.Api，端口 18099）` 的端到端 HTTP 测试。
覆盖健康、枚举契约、OpenAPI 契约，以及 Story #313 / S0-7 的**跨栈关联观测性**
（`X-Request-Id` / `traceparent` 在 HTTP 边界的真实回显行为）。

## 运行模式

### 1. 连已有实例（默认，无需 dotnet）
```bash
# 指向运行中实例（默认 http://127.0.0.1:18099）
export AGENTBOARD_BFF_URL=http://127.0.0.1:18099
pytest -m e2e tests/e2e_dotnet_bff
```
若实例不可达，本目录用例**整体 skip**（不报错），便于在无 .NET 的 CI 中安全收集。

### 2. 自动拉起（E2E_SPINUP=1）
用 `dotnet run` 以**临时 SQLite + Development** 独立启动 BFF（无需 MariaDB / FastAPI），
待 `/api/health` 就绪后跑用例，结束自动 tear down：
```bash
export E2E_SPINUP=1
pytest -m e2e tests/e2e_dotnet_bff -v
```
要求本机有 `.NET 10 SDK`（`dotnet --version`）与已 Release 构建的 `AgentBoard.Api`。

### 3. 双栈 meta 一致性（可选）
若同时有 FastAPI 在跑，设 `AGENTBOARD_FASTAPI_URL` 可额外校验 BFF 与 FastAPI 的
`/api/meta` 枚举完全一致（契约冻结的运行时校验）：
```bash
export AGENTBOARD_FASTAPI_URL=http://127.0.0.1:18000
pytest -m e2e tests/e2e_dotnet_bff/test_health_meta.py::test_meta_parity_with_fastapi
```

## 用例清单

| 文件 | 用例 | 验证点 |
|---|---|---|
| `test_health_meta.py` | `test_health_shape` | `/api/health` 形状 `{status,database,version,timestamp}` |
| | `test_meta_contract` | `/api/meta` 枚举 = FastAPI enums.py（#5/#311） |
| | `test_meta_parity_with_fastapi` | 双栈 meta 一致（FastAPI 可达时） |
| `test_trace_correlation.py` | `test_x_request_id_echoed` | `X-Request-Id` 入站原值回显 |
| | `test_x_request_id_generated_when_absent` | 未携带时生成关联 id |
| | `test_traceparent_continued` | `traceparent` W3C 续接（同 trace-id、新 span-id） |
| | `test_traceparent_present_by_default` | 默认响应带 `traceparent` |
| `test_openapi_contract.py` | `test_openapi_v1_served` | `/openapi/v1.json` 服务（非 `/openapi.json`） |
| | `test_openapi_paths_aligned` | 路由与 FastAPI `/api/*` 对齐（#4） |

## 设计说明

- 复用仓库 `pytest.ini` 的 `e2e` / `slow` marker：默认 `pytest` 不跑本目录（需 `-m e2e`），
  与既有单测 / Playwright e2e 共存。
- 断言均基于 2026-08-22 实测响应，非猜测；观测性行为经 `RequestIdMiddleware` /
  `TraceContextMiddleware` 在 HTTP 边界确认。
- 出站「BFF→FastAPI 携带 traceparent/X-Request-Id」由 xUnit 单元测试
  `TracePropagationDelegatingHandlerTests` 覆盖；本 e2e 验证其入站→响应这一可观测半环。
  待 BFF 出现真实代理端点后，可补「全链路跨栈 trace」集成用例。
