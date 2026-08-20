# Epic 151 / Story 326 / Task 1297 踩坑 — MembersTab 数据边界

**日期**：2026-08-20
**Story**：Epic 151 / Story 326 / Task 1297「MembersTab 数据边界」
**关联提交**：(待写)

## 背景

Epic 149 静态 Review 阻断级 2：
- MembersTab 文案「参与本项目的 Agent 池」与后端数据边界不一致
- 后端 `/api/agents` 无 project 过滤 + 返回 `_ser` 全列（含 cli_command/auth_key/probe_message/user_id）
- 任意登录用户可拉全表

修复 = 字段收窄 + 软鉴权 + 文案与数据对齐。

## 关键发现

### 1. `_ser` 函数在 `core/service_helpers.py:185`，不在 `scheduling/service.py`

`features/scheduling/router.py:141-145` 调 `service._ser(x)` —— 看起来函数应在
`scheduling/service.py`，但实际定义在 `agentboard/core/service_helpers.py:185`，
由 `service.py` 重导出。修法：
- 加 `Agent.to_public_dict()` 方法（`features/projects/models.py`）
- list_agents endpoint 改用 `to_public_dict()`，保持函数调用局部化
- 完整字段保留 `_ser`（admin / WebSocket broadcast 仍需要）

### 2. SQLAlchemy `expire_on_commit` + `admin.id` 在 commit 后触发 detached lazy load

`service.register_user` + `set_user_admin` + `s.commit()` 后再 `return admin.id`
会触发 `DetachedInstanceError`，因为 `expire_on_commit=True`（SQLAlchemy 默认）使
instance commit 后所有列 expire，session 关闭后再访问列触发 lazy load。

修法：在 commit 前 cache 关键 PK 到局部变量：
```python
admin_id_cached = admin.id
s.commit()
return admin_id_cached
```

### 3. TestClient 跨测试 `dependency_overrides` 残留导致 Detached

`StaticPool` + `Base.metadata.create_all()` 创建新 engine 后，旧的
`app.dependency_overrides[get_session]` 还指向已 dispose 的 engine → 新 session
指向旧 instance → DetachedInstanceError。

修法：`_setup_app` 第一行就 `app.dependency_overrides.clear()`；每个测试
`finally` 调 `_teardown_app()` 清空。

### 4. AgentRegisterIn schema 期望 `roles: str`（JSON 字符串），不是 list

```python
class AgentRegisterIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    roles: str = "[]"   # ← JSON 字符串，不是 list
    capabilities: str | list[str | dict] = "[]"
```

测试 fixture 传 `roles=["reviewer"]` → 422 "missing field"（实际是类型不对）。
修法：传 `'["reviewer"]'` JSON 字符串。`_parse_json_list` 同时支持 list + str
（str 走 `.strip()`，list 走原样）。

### 5. `validate_cli_command` 拒绝含 `&&` / `;` / `|` 等元字符的 CLI 模板

测试 fixture 想用 `"echo hello && rm -rf /"` 测字段收窄 → 422 InvalidValue
（service 入口拦截）。修法：换成 `"codebuddy --model {model}"`（合法 CLI 模板，
含 `{model}` 占位符也允许）。

### 6. 项目页 tab 容器有两个：navy 侧栏 (`project-nav-v7`) + emoji tab-bar

`app.html:249-274` 是新版 navy 侧栏（8 tab，使用 `selectProjectTab()` 调 signal）；
`app.html:540-...` 是旧版 emoji tab-bar（部分 tab 重叠）。

E2E 选「成员与 Agents」用：
```python
page.locator("button.project-nav-button-v7:has-text('成员与 Agents')").first
```
不能用 `a:has-text('成员')`（会匹到其他）。

### 7. /projects/{id}/sections 只识别 overview/proposals/documents/schedules

`loadRoute()` 里 section 解析只支持这 4 个值，其它默认 `overview`。所以
`/project/1/members` 实际进 overview tab，不是 members tab。要进 members 必须
手动点 navy 侧栏 `button.project-nav-button-v7:has-text('成员与 Agents')`。

### 8. PowerShell 把 ANSI color 当作 stderr RemoteException

`ng build` 成功后输出含 ANSI color（`[1m[32m√[39m[22m`），但 PowerShell 在
bash tool 包装下把 stderr 当 NativeCommandError 抛。修法：直接验证 dist 是否
存在（`frontend/dist/frontend/browser/index.html`），不看 exit code。

## 验证

- 后端 unit test：`tests/test_agent_public_dict.py` 4/4 PASS
  - `test_to_public_dict_strips_sensitive_fields`
  - `test_list_agents_endpoint_returns_public_dict`（API 直连）
  - `test_list_agents_endpoint_requires_auth_when_flagged`（REQUIRE_AUTH=1 → 401）
  - `test_list_agents_service_order_by_created_desc`
- 端到端：`tests/e2e_epic149/test_x_a2_members_data_boundary.py` PASS
  - PART A: API 字段收窄（sample keys 无 cli_command/auth_key/probe_message/user_id）
  - PART B: dev 模式无 token 200
  - PART C: 前端 heading subtitle + section title + badge 文案对齐
- 单元/集成回归：`test_crud_smoke.py` 9 SKIPPED（外部 API），`test_agent_public_dict.py` 4 PASS
- 前端 vitest：3 files / 69 passed / 1 skipped（无 regression）

## 改进要点（Future Work）

- 抽 `to_admin_dict()` 给 admin 端点用（保留全部字段）—— 未来 admin 面板需要
- `/api/agents/{agent_id}` GET 单条也走 `to_public_dict`（当前是 `service._ser(agent)`，
  含全列 — Task 1297 后端修了 list 漏了单条）
- 软鉴权加 `Authorization: Bearer <api_key>` 走 abk_ 通道（当前只验 Bearer user token）
