# Tasks

- [x] `worker_planner.py`：`offer_for` / `plan_project` / `complete_finished_stories` / `plan_once`
- [x] `worker_work.py`：抽出 `work_key` + `materialize_offer`（返回 `created` 以保幂等），`/offers` 复用
- [x] `worker_work.py`：新增 `POST /api/worker-work/projects` 声明项目范围（并对未列出项目置 enabled=False）
- [x] `worker_work.py`：`PROTOCOL = worker-work.serverplan.v1`
- [x] `worker_work.py`：`default_ticket_plan` / `build_qa_followup`，converge / QA 确认时 Server 补齐
- [x] `outbox_publisher._drain_once`：先 `plan_once()` 再 `drain_once()`（planner 自带节流）
- [x] `WorkerOwnedService`：协议 bump、`ReconcileLoop` → `PresenceLoop`（心跳 + 项目映射），删除 offer / story 收尾
- [x] `WorkerOwnedService`：无可用本机 Agent 时 ack-drop，替代每 2s 的 ReturnToTail 忙等
- [x] `CliLocator.BaseEnv`：补 `HOME`，WorkBuddy 等 Node CLI 复用本机登录态
- [x] `WorkPlanner`：删除 `Next` / `TicketPlan` / `AddQaFollowup` / `WorkOffer`，保留 prompt 与结果校验
- [x] .NET 测试同步（移除已迁移的 C# 规划断言）
- [x] 新增 `tests/e2e/happy_path/test_worker_planner.py`（18 例：类型→kind 映射、人审门、讨论轮次、覆盖过滤、plan_once 幂等、Story 收尾、默认 DAG）
- [x] 回归 `test_worker_owned_work.py` / `test_worker_work_ghost_rows.py`
