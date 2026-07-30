# Change: 修复 mcp_server.py 中 15 处 `_api` 未定义调用（Epic 97 P0）

## Why

AgentBoard 的自动开发闭环以「**MCP 优先**」为核心原则——MCP 是项目进度的唯一权威来源，选任务、查状态、改状态全部经由 MCP。

但巡检实测发现，调用 `search_tasks_enhanced` 会直接返回：

```
Error calling tool 'search_tasks_enhanced': name '_api' is not defined
```

根因是一次**未完成的重构**：早期的 HTTP 辅助函数 `_api` 被重命名为 `_http`（同时约定路径必须带 `/api` 前缀），但有 **15 处调用点没有同步更新**。

这个缺陷之所以能长期潜伏，是因为 Python 只在**运行时**解析全局名字：模块导入完全正常、`py_compile` 通过、FastMCP 也能成功注册这些工具并把它们暴露在工具列表里——只有当 Agent 真正调用时才炸。工具「看起来存在、实际不可用」，比工具缺失更具误导性。

### 影响面（6 大类共 15 个工具全线失效）

| 类别 | 失效工具 |
|---|---|
| 批量操作 | `batch_update_task_status` / `batch_assign_sprint` / `batch_delete_tasks` |
| 增强搜索 | `search_tasks_enhanced`（自动开发选任务的主力工具） |
| 数据导出 | `export_project_data` / `export_story_data` |
| 审计日志 | `list_audit_logs` |
| 任务依赖 | `add_task_dependency` / `get_task_dependencies` / `remove_task_dependency` |
| 数据导入 | `import_tasks` |
| Webhook | `create_webhook` / `list_webhooks` / `delete_webhook` / `toggle_webhook` |

### 连带发现的三个次生缺陷

1. **路径缺 `/api` 前缀**：`_http` 只做 `base_url + path` 拼接，`"/tasks/search"` 会静默 404。
2. **body 传参方式错误**：`_api("POST", path, payload)` 用位置参数传 body，而 `_http(method, path, **kw)` 只接受关键字参数 —— 即使名字修对了也会 `TypeError`。
3. **多值过滤是死代码**：`search_tasks_enhanced` 里的
   ```python
   for s_val in status:
       params.setdefault("status", status if not params.get("status") else params["status"])
   ```
   循环体与循环变量无关，语义等价于一次 `params["status"] = status`，纯属噪音。

## What Changes

- `agentboard/mcp_server.py`：15 处 `_api(...)` → `_http(...)`，统一补齐 `/api` 前缀；body 一律走 `json=`，query 一律走 `params=`。
- `search_tasks_enhanced`：删除死循环，直接把 `status` / `priority` 交给 httpx —— list 会被自动展开成重复查询参数（`status=todo&status=in_progress`），恰好对齐 FastAPI 端的 `list[str]` 声明；单值 str 也照常工作。同时补全对 `{"items": [...]}` 分页信封与 `{"error": ...}` 的解包处理。
- `tests/test_epic97_mcp_tool_availability.py`（新增）：**AST 静态护栏 + 真实栈集成**双层防线。
- `tests/test_epic97_mcp_tool_availability_e2e.py`（新增）：Playwright 端到端，验证「MCP 写入 → Web 可见」闭环。
- `tests/test_crud_smoke.py`：该模块依赖外部常驻服务却硬编码 `localhost:8000`，在未起栈的环境里稳定产生 9 个假阳性失败、淹没真实回归信号。改为可通过 `AGENTBOARD_SMOKE_BASE` 覆盖，且服务不可达时整模块 skip 而非 fail。

**零 REST 契约变更** —— 本次修复完全发生在 MCP 客户端侧，后端 `api.py` / `service.py` / 数据库均未触碰。

## Impact

- 15 个 MCP 工具恢复可用，自动开发闭环的「选任务」主力工具 `search_tasks_enhanced` 重新在线。
- 回归风险极低：所有改动均为「原本必定抛异常」的代码路径，不存在行为回退的可能。
- 部署说明：本次改动位于 MCP 服务进程内。**本轮刻意不重启 `agentboard-mcp-1` 容器**——它占用 18001 端口，正被 WorkBuddy 自身用于 MCP 通信，重启会切断连接。功能正确性已由真实栈集成测试完整覆盖（测试内自起 uvicorn，不依赖该容器）。

## Status

Implemented（in_review）
