# Epic 78 · Story 104 — AgentRun 状态机驱动 + report_run_result

**status**: in_review
**date**: 2026-08-04

## 问题

Story 102/103 交付了两条独立的最小单次驱动路径：

- `launch_run`（模式 A）：spawn CLI Agent 子进程 → 同步等退出码 → 回写；
- `trigger_run`（模式 B）：POST webhook 唤醒 Runner → 轮询 DB `run.status`
  （依赖外部回写）→ finalize。

但两者之间缺统一的**状态机驱动**：没有共享的认领/轮询/finalize 语义，且
「Agent 主动报告结果」的唯一通道是通用 `PATCH /api/runs/{rid}`——它只做
枚举校验（`ALL_RUN_STATUSES`），**不校验迁移合法性**，任何状态都能写成
任意其它状态（终态可被覆盖、running 可被跳过），且 `summary` / `log_ref`
字段无处落库。Agent 执行完没有结构化、可靠的"自报结果"入口。

## 目标

1. **状态机驱动主循环** `execute_run(session_factory, run_id)`：
   认领 pending → running → 按 agent 类型自动分派（TRIGGER_AGENTS → WebhookTrigger，
   其余 → LauncherAdapter）→ 轮询（DB 外部回写优先 / 适配器退出码 / 超时兜底）
   → finalize 为 success/failed 并落库 `summary` + `log_ref` + `finished_at`。
2. **`report_run_result` 服务函数 + REST 端点 `POST /api/runs/{rid}/report`**：
   状态机合法迁移校验（`RUN_TRANSITIONS` 表：pending→running/success/failed/cancelled，
   running→success/failed/cancelled，终态不可再迁移），幂等（终态重复报告同状态
   返回 200 且不覆盖已有 summary）。
3. **`agent_runs` 新增 `summary`（Text）/ `log_ref`（String(512)）列**
   （Alembic 双后端迁移，纯增量可空）。
4. **MCP 工具 `report_run_result(run_id, status, summary?, log_ref?)`**：
   Agent 经 MCP 显式回写 run 结果，执行器据此 finalize。
5. **CLI 入口** `python -m agentboard.executor --execute <id>`（统一状态机驱动）。

## 非目标（后续 Change 承接）

- 执行器 daemon 主循环（并发认领 / 租约续期 / 后台轮询常驻进程）——本 Story 交付
  单次驱动语义，daemon 化留后续；
- `AgentSchedule` 绑定松绑（项目/Agent 级 + 筛选）（Story 106）；
- `Agent` 记忆自动加载 `get_project_memory`（Story 107）。

## 验收

- `service.report_run_result`：pending/running → success/failed/cancelled 合法；
  终态不可再迁移（抛 `IllegalTransition`）；终态重复报告同状态幂等 200；
  `summary`/`log_ref`/`finished_at` 正确落库；
- REST `POST /api/runs/{rid}/report`：200 / 404 / 422 / 409 语义正确；
- `execute_run`：fake Launcher 立即 success → 落库 summary/finished_at；
  抛异常 → failed；Agent 外部回写 success → 执行器轮询感知并以外部为准；
  超时兜底 → failed(timeout)；非 pending（已终态）不重复执行（返回 None）；
- MCP `report_run_result` 注册并走 REST 端点全链路可用；
- Web 前端（Playwright）核心渲染 0 控制台报错 / 0 资源 404；
- 既有回归全绿（Story 101/102/103/105 + scheduler + CRUD + proposals）。
