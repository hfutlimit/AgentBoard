# Design: Task 状态精简回归修复

## 问题分析

### 异常类双轨（根因）

Phase 4-9 拆分时，`features/*/service.py` 从 `service.py` 拷贝业务函数并改用
`core.exceptions` 的异常类；但顶层 `service.py` 的 facade 保留了 201 个 re-bind
函数中仍抛旧类的代码，同时**保留了旧异常类定义**。结果：

```
agentboard/service.py            → class InvalidValue(DomainError)  # 旧
agentboard/features/**/service.py → from ...core.exceptions import InvalidValue  # 新
```

- 测试 `pytest.raises(service.InvalidValue)` 匹配旧类，但实现抛新类 → 漏捕获；
- `api.py` 的 exception handler 已改为匹配 `core.exceptions`（Phase 5 注释），
  旧类实例会 500（InvalidValue 语义丢失）。

**决策**：顶层 `service.py` 的 5 个异常类改为从 `core.exceptions` re-export
（`from .core.exceptions import DomainError, NotFound, IllegalTransition, Duplicate, InvalidValue`），
删除本地 class 定义。`core.exceptions.Duplicate = Conflict`（旧别名已保留），
与 `service.Duplicate` 语义一致。

### helper 迁移缺口

`_parse_json_list` 在拆分时被 scheduling feature 使用，但未迁入 `core/service_helpers.py`。
**决策**：迁入 `core/service_helpers.py`（归属明确），顶层 `service.py` 与
`features/scheduling/service.py` 均从该模块导入，消除分叉。

### 跨域/全局引用缺口

- `features/scheduling/service.py`：缺 `json`（`json.dumps`）、缺
  `set_status`（`submit_task_for_review` 调任务状态机）→ 补导入。
- `features/scheduling/router.py`：`agent_state_hub` 定义于 `api.py` 顶层
  （Agent WS 广播 hub），拆分时未随之导入 → 从 `api` 导入
  （api.py 在 include_router 前已定义 hub，无循环问题）。

### 测试回归（拆分路径失效）

| 测试 | 原目标 | 现目标 |
|------|--------|--------|
| `test_epic118_claim_guard._patch_http` | `mcp_server._http` | `features.mcp.scheduling._http` |
| `test_epic118_claim_guard` AST 护栏 | `mcp_server.py` | `features/mcp/scheduling.py` |
| `test_epic122_s2m1` 事件 mock | `api.publish_workflow_event` | `features.work_items.router.publish_workflow_event` |
| `test_story_265_*` DB | 默认 `./agentboard.db`（脏库） | `tempfile.mktemp` 独立 SQLite |

## 方案

1. `core/service_helpers.py`：新增 `_parse_json_list`（含 `__all__`）。
2. `service.py`：异常类改 re-export；`_parse_json_list` 改导入；删除本地重复定义。
3. `features/scheduling/service.py`：补 `json`、`_parse_json_list`、`set_status` 导入。
4. `features/scheduling/router.py`：补 `agent_state_hub` 导入。
5. 三个测试文件按上表修正目标/DB 隔离。

## 风险与缓解

- `service.py` 本地代码仍 `raise InvalidValue` → 现指向 core 类，语义不变（构造签名兼容）。
- `agent_state_hub` 从 api 导入 → api.py 顶部定义、422 行 include_router，导入时序安全。
- 不触碰 18001 端口、不新增依赖、不改 REST/DB 契约。
