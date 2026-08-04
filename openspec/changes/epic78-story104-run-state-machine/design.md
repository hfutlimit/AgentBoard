# Epic 78 · Story 104 — 设计：AgentRun 状态机驱动 + report_run_result

## 1. 现状与缺口

### 现状

- `AgentRun.status` 只有枚举校验（`ALL_RUN_STATUSES`），无迁移合法性校验：
  `PATCH /api/runs/{rid}` 可把 success 覆盖成 failed、可跳过 running。
- `summary` / `log_ref` 字段不存在；Agent 结果只能写进 `output`（自由文本）。
- `launch_run` / `trigger_run` 两条路径各自 finalize，无共享状态机语义。

### 缺口

| 能力 | 现状 | 目标 |
|------|------|------|
| 状态机迁移校验 | 无（任意写） | `RUN_TRANSITIONS` 表 + `IllegalTransition` |
| Agent 自报结果 | 通用 PATCH（无校验） | `POST /api/runs/{rid}/report` |
| 结构化结果字段 | 仅 output/error_message | + summary/log_ref |
| 统一驱动 | launch/trigger 分离 | `execute_run` 自动分派 |

## 2. 状态机设计

```
                 ┌──────────────┐
    pending ────▶│   running    │────▶ success
       │         └──────────────┘      │
       │               │               ├──▶ failed
       │               └──────▶ cancelled
       └──▶ success / failed / cancelled（执行器未认领时 Agent 直接报终态）
```

`RUN_TRANSITIONS`（service.py）：

```python
RUN_TRANSITIONS = {
    "pending":   {"running", "success", "failed", "cancelled"},
    "running":   {"success", "failed", "cancelled"},
    "success":   set(),
    "failed":    set(),
    "cancelled": set(),
}
```

- 终态不可再迁移；重复报告**同一终态** = 幂等 no-op（200，不覆盖已有 summary）。
- `report_run_result` 与通用 `PATCH /api/runs/{rid}` 并存：PATCH 保留宽松
  （执行器内部 finalize / 既有调用方兼容），report 端点走严格状态机
  （Agent 自报结果专用）。

## 3. 分层改动

### 3.1 模型 + 迁移

`agentboard/domains/scheduling/models.py` — AgentRun 增加：

```python
summary: Mapped[str | None] = mapped_column(Text, nullable=True)
log_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

`migrations/versions/l4m5n6o7p8q9_add_run_summary_logref.py`
（down_revision = `k8l9m0n1o2p3`，即 Story 105 的 CHECK 约束迁移之后）：

- 双后端（SQLite/MariaDB）兼容的 `add_column`（inspector 防重复）；
- 可空列，既有数据零影响。

### 3.2 service 层

- `update_run` 增加 `summary` / `log_ref` 两个写入分支（保持既有行为不变）。
- 新增 `RUN_TRANSITIONS` + `report_run_result(s, id, *, status, summary, log_ref)`：
  - NotFound → 抛 `NotFound`；
  - 非法 status → 抛 `InvalidValue`；
  - 非法迁移（含终态→异态）→ 抛 `IllegalTransition`；
  - 幂等：`run.status == status`（终态重复报告）→ 只补缺失 summary/log_ref，返回 200；
  - 合法迁移 → 写 status/summary/log_ref + `finished_at = utc_now()`。

### 3.3 REST 层

- `RunPatch` 增加 `summary` / `log_ref`（保持 PATCH 兼容）。
- 新增 `RunReportIn(status, summary?, log_ref?)` + `POST /api/runs/{rid}/report`：
  - NotFound → 404；InvalidValue → 422；IllegalTransition → 409；成功 → 200 序列化。

### 3.4 执行器主循环

`agentboard/executor.py` 新增 `execute_run(...)`：

```
pending? ──否──▶ return None（跳过，防重复执行）
   │是
ctx = build_run_context(run)
ctx 无效 ──▶ failed("no valid schedule/project context")
adapter = resolve_adapter(ctx.agent)   # TRIGGER_AGENTS→Trigger 子类，其余→Launcher 子类
run → running（started_at）
handle = adapter.launch(run, ctx)       # 异常 → failed("launch failed: ...")
进入轮询（poll_interval / max_poll_seconds 兜底）：
  ① 每次先读 DB：外部已终态（success/failed/cancelled）→ 以外部为准直接返回
  ② adapter.poll_status(handle) 到 SUCCESS/FAILED → break
  ③ 超时 → kill 子进程 + failed(timeout)
finalize：
  SUCCESS → status/summary/output/log_ref/finished_at
  FAILED  → status/error_message/output/log_ref/finished_at
```

关键语义：
- **外部回写优先**：Agent 经 `report_run_result` 落库终态后，执行器下一轮
  轮询即感知并以外部为准 finalize（外部 summary 不被执行器覆盖）；
- **非 pending 跳过**：已终态 run 再调 `execute_run` 返回 None（幂等防重放）；
- `summary`/`log_ref` 同时写入 `output`（截断 20000）保持向后兼容。

### 3.5 MCP 层

`mcp_server.py` 新增 `report_run_result(run_id, status, summary?, log_ref?)`：

```python
@mcp.tool()
def report_run_result(...):
    body = {"status": status, ...}
    return _http("POST", f"/api/runs/{run_id}/report", json=body)
```

## 4. 测试策略

### 单测 `tests/test_epic78_story104_state_machine.py`（13 项）

- service 层：pending→success / running→failed / 终态不可变 + 幂等 /
  NotFound / InvalidValue；
- REST（真实 uvicorn 子进程）：200 / 409 / 422 / 404 语义；
- executor：success 路径 / failed 路径 / launch 异常 / **外部回写优先** /
  超时兜底 / 非 pending 跳过；
- MCP：工具注册 + REST 全链路（monkeypatch API_URL 指向测试 uvicorn）。

### E2E `tests/test_epic78_story104_state_machine_e2e.py`（2 项）

- report 端点 REST 全链路（pending→success + summary/log_ref 落库 + 幂等 + 409）；
- Playwright：登录 → 项目/看板渲染 0 控制台报错 / 0 资源 404。

## 5. 兼容性

- 零既有 REST 契约破坏（仅新增端点 + 可空列 + RunPatch 扩展字段）；
- `PATCH /api/runs/{rid}` 行为不变（宽松校验保留）；
- 不触碰 18001 / docker 端口；自包含测试（临时 SQLite + 随机端口 uvicorn）。
