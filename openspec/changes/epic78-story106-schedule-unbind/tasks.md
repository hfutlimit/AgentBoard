# Epic 78 · Story 106 — Tasks

- [x] T1 `models.py` AgentSchedule 新增 agent/task_id/task_priority/task_type/epic_id 列
- [x] T2 Alembic 迁移 `m0n1o2p3q4r5`（双后端兼容）
- [x] T3 `service.py`：create_schedule/update_schedule 支持新字段（含显式置空）+ `pick_eligible_task`
- [x] T4 `scheduler.py` `_trigger_one`：固定 task / 自动选 eligible / 无 eligible 跳过
- [x] T5 `executor.py` `build_run_context`：agent 读 schedule.agent（fallback env）
- [x] T6 `api.py` ScheduleIn/SchedulePatch 透传 + 校验
- [x] T7 `mcp_server.py` create_schedule/update_schedule 透传
- [x] T8 单测 `tests/test_schedule_unbind.py`
- [x] T9 前端：schedule 创建表单 agent 下拉 + 列表 agent 徽标（增量）
- [x] T10 回归 + E2E 验证 + 文档（docs/requirements.md FR 补充）
