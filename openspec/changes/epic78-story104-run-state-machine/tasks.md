# Epic 78 · Story 104 — 任务清单：AgentRun 状态机驱动 + report_run_result

**status**: in_review
**date**: 2026-08-04

## 任务

- [x] AgentRun 模型新增 `summary`（Text）/ `log_ref`（String(512)）可空列
  （`agentboard/domains/scheduling/models.py`）
- [x] Alembic 双后端迁移 `l4m5n6o7p8q9_add_run_summary_logref.py`
  （down_revision = `k8l9m0n1o2p3`）
- [x] service 层：`update_run` 支持 summary/log_ref；新增 `RUN_TRANSITIONS`
  + `report_run_result`（状态机校验 + 幂等 + finished_at 落库）
- [x] REST：`RunPatch` 扩展 summary/log_ref；新增 `RunReportIn` +
  `POST /api/runs/{rid}/report`（404/422/409 语义）
- [x] executor：`execute_run` 统一状态机主循环（认领→running→自动分派
  launch/trigger→轮询 DB 回写优先/退出码/超时兜底→finalize 写 summary+log_ref）
- [x] CLI：`--execute <id>` 统一入口
- [x] MCP：`report_run_result(run_id, status, summary?, log_ref?)` 工具
- [x] 单测 `tests/test_epic78_story104_state_machine.py` 13 passed
- [x] E2E `tests/test_epic78_story104_state_machine_e2e.py` 2 passed（Playwright 0 报错）
- [x] 回归：Epic 78 全量 72 + scheduler/backend 86 + CRUD/proposals 17 passed
- [x] MCP 状态同步：Task 969 → in_review；Story 104 → in_review
