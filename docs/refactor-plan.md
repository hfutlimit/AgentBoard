# AgentBoard 架构重构方案 (2026-08-14)

> **目标**:把 5300 行 `service.py` / 4000 行 `api.py` / 90+ 函数 `mcp_server.py` 的"三座大山"打散,转成企业级垂直切片架构,同时保证 201 个测试不破。

---

## 1. 现状问题

| 模块 | 行数 | 顶级函数/类 | 主要问题 |
|---|---|---|---|
| `agentboard/service.py` | **5367** | **254 顶层函数** | 纯过程式,无 service class,raw `s: Session` 满天飞,Task 状态机内联,缓存失效靠人工纪律 |
| `agentboard/api.py` | **4007** | 179 `@app.X()` + 58 Pydantic | 0 个 `APIRouter`,所有端点直挂 app,schema 与路由耦合 |
| `agentboard/mcp_server.py` | **1984** | **90+ `_xxx_yyy`** | 12 个资源 × 5 个动作全手写,纯复制 |
| `agentboard/worker.py` | 1808 → 510 | `ProposalWorker` | 已经分得不错,主参考样板 |
| 测试 | 201 文件,**0 conftest** | | 全部外部 httpx 调跑服务,无单元测试基础 |

---

## 2. 目标架构

### 2.1 目录布局

```
agentboard/
├── core/                          # 跨切关注点
│   ├── config.py                  # pydantic-settings
│   ├── exceptions.py              # 异常基类
│   ├── state_machine.py           # 通用 SM 抽象
│   ├── observability/
│   │   ├── logging.py             # 结构化 JSON 日志
│   │   ├── metrics.py             # Prometheus
│   │   └── tracing.py             # OTel 占位
│   ├── infrastructure/
│   │   ├── database.py            # engine / session / UoW
│   │   ├── cache.py
│   │   ├── mq.py
│   │   ├── cos_client.py
│   │   └── auth.py
│   └── api/                       # FastAPI 基础
│       ├── app.py                 # create_app() 工厂
│       ├── middleware.py          # request_id / logging / auth
│       ├── deps.py                # DI 容器
│       └── errors.py              # 异常 → HTTP 映射
│
├── features/                      # 垂直切片
│   ├── auth/
│   ├── projects/                  # Project + Epic + Story + Sprint + Review
│   ├── work_items/                # Task + Comment + Attachment + Dependency
│   ├── proposals/                 # Proposal + Round + Question + Ticket
│   ├── documents/                 # Document + Revision + Folder + Comment
│   ├── scheduling/                # AgentSchedule + AgentRun
│   ├── notifications/
│   ├── webhooks/
│   ├── workers/                   # worker 系统
│   └── mcp/                       # MCP server (registry 模式)
│
├── web/
├── __init__.py
├── main.py                        # uvicorn 入口
│
├── api.py                         # [FACADE] → core.api.app
├── service.py                     # [FACADE] → features.*.service
├── mcp_server.py                  # [FACADE] → features.mcp.server
├── models.py                      # [FACADE] → features.*.models
└── worker.py                      # [FACADE] → features.workers.main
```

### 2.2 每个 feature 的 6 件套

```
features/<name>/
├── __init__.py            # 公开 API
├── models.py              # SQLAlchemy ORM
├── schemas.py             # Pydantic (in/out)
├── service.py             # <Name>Service class
├── router.py              # APIRouter
├── state_machine.py       # (可选) 状态机
└── tests/
    ├── __init__.py
    ├── conftest.py        # 本 feature 专用 fixture
    ├── test_models.py
    ├── test_service.py
    ├── test_state_machine.py
    └── test_api.py
```

### 2.3 核心设计原则

1. **显式契约**:每个 service 函数签名明确,允许 `ServiceResult` 模式或抛领域异常
2. **UoW 模式**:`with uow.transaction() as s:` 统一事务边界 + 缓存失效
3. **状态机复用**:`core.state_machine.StateMachine` 基类,所有域状态机继承
4. **APIRouter 分域**:`prefix="/api/projects"` + `tags=["projects"]`,主 `app.py` 只负责装配
5. **Observability 默认开启**:每个 service 函数入口埋点 `log.info`、`metrics.counter.inc()`、`tracer.start_as_current_span()`
6. **Facade 兼容**:老 `from agentboard.models import X` 全部走 re-export,业务代码零改动

---

## 3. 实施阶段

| # | 范围 | 提交 |
|---|---|---|
| 0 | plan 文档 + 目录骨架 | `docs: refactor plan + 骨架` |
| 1 | `core/` 全部 8 文件 + `main.py` | `refactor(core): 基础设施层` |
| 2 | `features/*/models.py` 迁移 + `models.py` 转 facade | `refactor(features): models 垂直切片` |
| 3 | `core/state_machine.py` + Task SM 落地 | `refactor(state-machine): 通用基类 + Task SM` |
| 4 | service.py 拆分(254 函数 → 8 个 Service) | `refactor(service): 垂直切片 + UoW` |
| 5 | api.py 拆分(179 端点 → 8 router + schemas) | `refactor(api): APIRouter 化` |
| 6 | mcp_server.py 重写(registry 模式) | `refactor(mcp): registry 模式` |
| 7 | workers/ 迁位 | `refactor(workers): 迁 features/workers/` |
| 8 | tests/conftest.py + factories | `test: 共享 fixture + factory` |
| 9 | 清理旧 facade 内容 + 全量 e2e | `chore: 全量回归` |

---

## 4. 风险与回滚

- **回滚策略**:每阶段独立 commit,任意阶段失败可 `git revert <commit>` 回到上一阶段
- **测试保护**:201 个测试 + facade 兼容,任何阶段不破现有 import
- **业务不停服**:重构期所有 HTTP URL 不变,前端 / MCP client 零感知

---

## 5. 不在范围

- 前端 Angular 重构(独立任务,后续)
- 数据库 schema 变更(无)
- 业务逻辑重写(只搬位置,不改行为)
- 移除老的 facade 文件(保留到 Phase 9 之后单独清理)
