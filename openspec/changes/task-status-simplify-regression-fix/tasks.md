# Tasks: Task 状态精简回归修复

## 1. 异常类统一到 core.exceptions
- [x] `agentboard/service.py`：删除本地 `DomainError`/`NotFound`/`IllegalTransition`/`Duplicate`/`InvalidValue` class 定义，改为 `from .core.exceptions import ...`
- [x] 验证 `service.InvalidValue is core.exceptions.InvalidValue`

## 2. helper 迁移
- [x] `agentboard/core/service_helpers.py`：新增 `_parse_json_list`（含 `__all__`）
- [x] `agentboard/service.py`：删除本地 `_parse_json_list` 定义，从 `core.service_helpers` 导入
- [x] `agentboard/features/scheduling/service.py`：import 列表补 `_parse_json_list`

## 3. scheduling 缺导入修复
- [x] `features/scheduling/service.py`：补 `import json`、`set_status`（跨域任务状态机）
- [x] `features/scheduling/router.py`：补 `agent_state_hub`（api.py 顶层 WS hub）

## 4. 测试回归修正
- [x] `tests/test_epic118_claim_guard.py`：`_patch_http` → `features.mcp.scheduling._http`；AST 护栏 → scheduling.py
- [x] `tests/test_epic122_s2m1.py`：事件 mock → `features.work_items.router.publish_workflow_event`
- [x] `tests/test_story_265_task_status_simplify.py`：独立临时 SQLite DB URL

## 5. 验证
- [x] `pytest tests/test_story_265_task_status_simplify.py -q` → 17 passed
- [x] 状态机/评审/认领四组回归零失败（unit/ + story265 + epic118 + epic123 + task_state_machine 单独跑全绿）
- [x] 非 E2E 全套回归零失败（已知 flaky：多文件 sys.modules 重载互相干扰，单独跑全绿；排除直连脚本后收集期不再报错）
- [x] `import agentboard.service; import agentboard.api` OK
- [x] E2E（核心页面渲染 0 报错，Playwright 无 JS error）

## 6. 本次 Review 额外发现并修复（2026-08-15 本地部署测试）
- [x] `features/projects/router.py`：bulk-archive/unarchive 非 admin 分支引用 `ProjectMember` 未导入 → 500（admin 路径绕过未暴露）；补 `from ...features.projects.models import ProjectMember`
- [x] `service.py update_task`：PATCH `/api/tasks/{tid}` 直改 status 绕过状态机（允许 done→todo 非法迁移）；改为 status 委托 `set_status` 强制迁移 + `status_reason` 透传
- [x] `features/work_items/router.py`：PATCH 端点补捕获 `IllegalTransition` → 400
- [x] `schemas.py TaskPatch`：补 `status_reason` 字段
- [x] 本地独立 SQLite 部署：API 冒烟 21/21 passed（注册/登录/项目中心/归档/状态机/评论/搜索/文档）；Web 服务渲染正常，Playwright 无 JS error
