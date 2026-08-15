# Proposal: Task 状态精简（Story 265）重构回归修复

## 背景

Epic 139「精简 task 状态」实现（commit `dcee2f7`）已完成并通过 91/91 测试，
但 2026-08-14 的 Phase 1-9 vertical-slice 重构（`features/*` 拆分、`core/` 抽层）
引入系统性回归，导致 `agentboard` 包导入与测试失败：

1. **循环导入**（Code Review 自动化 comment #566 记录）：`models.py` → `domains/*/__init__` → `features/*/service`（`from ... import models` 反向依赖）→ `models.py`。已在并发修复（commit `8ccc0fd` ~ `ec4e747`）中解决。
2. **本提案覆盖的遗留问题**（并发修复后仍存在）：
   - 顶层 `service.py` 仍定义**旧异常类**（`DomainError`/`NotFound`/`Duplicate`/`InvalidValue`/`IllegalTransition`），而 feature 层已改为抛 `core.exceptions` 同名新类 → `pytest.raises(service.InvalidValue)` 无法捕获，验收测试 11 error。
   - `_parse_json_list` 仍定义在顶层 `service.py`，但 `features/scheduling/service.py` 调用而未导入 → `NameError`。
   - `features/scheduling/service.py` 缺 `json` 导入、缺 `set_status` 跨域导入。
   - `features/scheduling/router.py` 引用 `agent_state_hub`（定义于 `api.py` 顶层）未导入。
   - 测试回归：Phase 5/6 拆分后 `test_epic118_claim_guard.py` 仍 mock `mcp_server._http`/AST 目标旧路径；`test_epic122_s2m1.py` 仍 mock `api.publish_workflow_event`（已移入 feature router）。
   - `test_story_265_task_status_simplify.py` 缺独立 DB URL（与其他测试不一致，落到工作目录脏库导致 `_alembic_tmp_stories already exists`）。

## 目标

- Story 265 验收测试（17 项）全绿；
- 相关状态机/评审/认领护栏测试全绿；
- 非 E2E 全套回归零失败；
- 零新增依赖、零既有契约破坏。

## 验收

- `pytest tests/test_story_265_task_status_simplify.py -q` → 17 passed
- `pytest tests/ -k "not e2e"`（排除旧直连脚本）→ 零失败
- `import agentboard.service; import agentboard.api` → OK
