# Tasks: MCP 工具可用性修复与回归护栏（Epic 97 P0 · Task 923）

## 排查

- [x] 复现：MCP 调用 `search_tasks_enhanced` → `name '_api' is not defined`
- [x] 全量定位：`agentboard/mcp_server.py` 共 15 处 `_api(...)` 调用点
- [x] 归类影响面：批量操作 / 增强搜索 / 导入导出 / 审计 / 依赖 / Webhook 六大类
- [x] 核对 REST 侧 14 个对应端点均真实存在于 `/api/...`（`grep` api.py 确认）
- [x] 核对每个端点的传参约定（body vs query），确认 `json=` / `params=` 的正确用法

## 修复（agentboard/mcp_server.py）

- [x] 批量操作 3 处：`batch_update_task_status` / `batch_assign_sprint` / `batch_delete_tasks`
      —— 补 `/api` 前缀 + 位置参数 body 改 `json=`
- [x] `search_tasks_enhanced`：删除多值过滤死代码（for + setdefault），
      直接透传 list 给 httpx 展开为重复查询参数；补 `{"items": [...]}` 信封与 `{"error": ...}` 解包
- [x] 导出 2 处：`export_project_data` / `export_story_data`
- [x] 审计 1 处：`list_audit_logs`
- [x] 依赖 3 处：`add_task_dependency` / `get_task_dependencies` / `remove_task_dependency`
- [x] 导入 1 处：`import_tasks`
- [x] Webhook 4 处：`create_webhook` / `list_webhooks` / `delete_webhook` / `toggle_webhook`
- [x] 验证零残留：`grep -c "_api(" agentboard/mcp_server.py` → 0
- [x] `py_compile` 通过

## 回归护栏（tests/test_epic97_mcp_tool_availability.py）

- [x] AST 静态护栏 `test_no_undefined_global_calls_in_mcp_server`
      —— 完整局部绑定收集（参数 / 赋值 / 海象 / for / with / except / 推导式 / 嵌套 def / import 别名）
- [x] 窄断言 `test_no_legacy_api_helper_references` —— 禁止 `_api(` 复活
- [x] 窄断言 `test_http_helper_callers_use_absolute_api_paths` —— `_http` 字面量路径须以 `/api` 开头
- [x] 真实栈集成 `test_all_repaired_tools_work_against_real_stack`
      —— 真实 uvicorn 子进程，逐个真调 15 个工具；副作用操作回查 REST 确认落库
- [x] 多值语义 `test_search_multi_value_filter_actually_ors`
      —— 「多值结果 ⊇ 各单值结果之并」

## 护栏有效性反向验证

- [x] 用 `git show HEAD:agentboard/mcp_server.py` 导出修复前源码
- [x] 静态护栏对其**精确命中 15 处** `_api`，与人工排查数量吻合 → 护栏有效

## E2E（tests/test_epic97_mcp_tool_availability_e2e.py）

- [x] 自起真实 API + Web，Chromium 驱动 SPA
- [x] UI 登录 → 仪表盘渲染正常
- [x] MCP 工具真调（search / batch_update / export）打向同一套栈
- [x] 浏览器读回：点击导航 `打开` → `Backlog`，断言任务可见且状态为「待办」
      （证明「MCP 写入 → Web 可见」闭环贯通）
- [x] 零报错断言：console error / pageerror / 静态资源 404
- [x] 截图留证 `screenshots/epic97_mcp_tools_board.png`

## 回归信号治理

- [x] `tests/test_crud_smoke.py`：硬编码 `localhost:8000` 造成 9 个假阳性失败
      → 改为 `AGENTBOARD_SMOKE_BASE` 可覆盖 + 服务不可达时整模块 skip
- [x] 验证：同组测试 `9 failed, 11 passed` → `11 passed, 10 skipped`

## 验证与交付

- [x] 新增测试全绿：静态护栏 3 + 集成 2 + E2E 1
- [x] 回归无新增失败：`test_domain_boundaries` / `test_epic30_cache` / `test_api_keys`
      / `test_epic96_p0_proposals` / `test_admin_api_key_scope`
- [x] OpenSpec proposal / design / tasks 三件套
- [x] MCP 状态流转：Task 923 `backlog → todo → in_progress → in_review`
- [x] Story 160 / Epic 97 同步 `in_review`
- [x] git commit + push origin main

## 刻意不做

- [ ] ~~重启 `agentboard-mcp-1` 容器部署新代码~~
      —— 该容器占用 18001，正被 WorkBuddy 用于 MCP 通信，重启会切断连接。
      功能正确性已由自包含的集成测试与 E2E 完整覆盖；容器部署留待独立运维窗口。

## 后续建议

- [ ] 引入 ruff（F821 undefined-name）接入 CI，用成熟 linter 覆盖全仓库
      —— 需先清理存量告警，故不与本次修复耦合
- [ ] 为 MCP 工具建立统一的「工具冒烟」参数化测试，新增工具自动纳入覆盖
