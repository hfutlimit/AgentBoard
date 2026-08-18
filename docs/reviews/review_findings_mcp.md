# AgentBoard MCP 工具层评审报告

> 评审对象：`agentboard/features/mcp/` 下 10 个文件（__init__ / shared / auth / admin / work_items / projects / proposals / documents / scheduling / notifications），
> 交叉核对：`agentboard/mcp_server.py`（136 个 `@mcp.tool()`，全文 1636 行）、REST 鉴权链（`api.py` 中间件、`api_helpers.py`、`features/admin/router.py`、`features/auth/router.py`、`core/api/deps.py`、`core/infrastructure/auth.py`）、FastMCP 3.4.4 能力面（`tool(auth=...)`、`ToolAnnotations`）。
> 只读评审，未修改任何文件。

---

## A. 亮点

- **分层清晰、职责收敛**：`features/mcp/*.py` 是纯 REST 薄包装（不直连 DB），`shared.py` 统一 token 注入与 HTTP 调用（`shared.py:39-42` 统一拼 `Authorization: Bearer`），`mcp_server.py` 集中定义工具；backward-compat facade（`mcp_server.py:27-41`）自动 re-export 且用 `not hasattr` 避免覆盖已有名字。
- **错误文本可控**：REST 层 DomainError 家族有全局 handler（`api.py:176-190`：NotFound→404 / Duplicate→409 / InvalidValue→422 / IllegalTransition→400），`_http` 把 4xx/5xx 折叠成 `{"error": detail}`（`shared.py:45-49`），正常场景 LLM 看到的是 detail 文本而非 Python traceback——"裸异常冒泡"在非网络错误路径上不存在（MCP 层不直接调 service，DomainError 不会在 MCP 层抛）。
- **越权意识已有雏形**：`_proj_list`（`projects.py:18-32`）是唯一主动收敛可见性的地方——admin 走全量、普通用户走 `/api/users/me/projects` 成员作用域，注释明确"防越权的正确边界是配非管理员 key"。
- **proposals 组是质量标杆**：`proposal_claim` 走服务端 CAS 端点并留下长注释警告"不要改回 GET-PUT"（`mcp_server.py:1335-1338`）；`_is_http_error` 用 `set(keys)=={"error"}` 精确区分"实体自带 error 字段"与传输错误（`mcp_server.py:1304-1311`）；`proposal_finalize` 有 converged_spec 空值校验与中文友好错误（`mcp_server.py:1386-1387`）；`_proposal_replay`（`proposals.py:25-63`）是纯函数、无副作用、输出结构化，helper 层的正确形态。
- **REST 侧有兜底**：admin 端点全部经 `_require_admin`（`admin/router.py:152/163/178/189`），且同时支持 Bearer 与 `abk_` key（`api_helpers.py:307-320`）；`project_access_middleware`（`api.py:350-410`）在 `REQUIRE_AUTH=1` 时对项目资源做成员/owner 校验。

---

## B. 职责/重复问题

- `_http`/`_current_token`/`API_URL` **双份实现**：`shared.py:22-49` 与 `mcp_server.py:43,72-91` 各一份，`shared.py` 靠"查 `mcp_server` 命名空间拿 httpx/API_URL"兼容测试 monkey-patch（`shared.py:7-11,33-38`）——同一函数两处维护，改动必须同步。
- **工具层与 helper 层职责边界模糊**：参数拼装（`{k: v for k, v in ... if v is not None}`）在 `mcp_server.py` 约 12 个 `update_*` 工具重复（172/220/240/296/441/507…），limit/offset 分支在 `projects.py:24-26,61-63,100-104` 等重复；`resp.get("items", resp)` 拆包在 helper 层与工具层（如 `mcp_server.py:921`）共约 10 处重复。
- **与 REST 的校验重复且不一致**：枚举/长度/分页 REST 已用 Pydantic/`Query(ge=,le=)` 收口（如 `admin/router.py:149`），MCP 层却多数零校验直接透传，形成"REST 收口、MCP 透传"的中间态，两处规则漂移。
- **跨模块常量错位**：`_MEMORY_*` 定义在 `mcp_server.py:1237-1238`，却被 `documents.py:104` 引用（见 C-1）；各 feature 模块模板化的 `import os / typing.Any / httpx`（`auth.py:11-13` 等 8 个文件）全部未使用，属于复制粘贴残留。

---

## C. 问题清单（按严重度排序）

### 严重

1. `[严重] documents.py:103-104（常量定义在 mcp_server.py:1237-1238）` `_memory_title` 引用本模块未定义的 `_MEMORY_AGENT_PREFIX`/`_MEMORY_PROJECT_TITLE` → **调用即 NameError**：`append_agent_memory`（`mcp_server.py:1268-1288`）100% 崩溃，`get_project_memory` 传 `agent` 时崩溃 → 修复：把两个常量移到 `documents.py`（或 `shared.py`），`mcp_server.py` 改为 import。

2. `[严重] mcp_server.py:150 起全部 136 个工具 + shared.py` **工具层零鉴权、零权限映射**：全仓 `permission_allows` 只在 `api.py:96` 与 `api_helpers.py:76` 被调用，且只查 `api:read`/`api:write` 粗粒度；工具名是 `list_projects` 等 snake_case，**与 `feature:action` 命名空间完全不匹配**（既无映射也无工具）；`MCP_REQUIRE_AUTH` 默认 `"0"`（`mcp_server.py:45`），streamable-http 下 8001 端口裸奔（`mcp_server.py:1623-1632` 只校验 secret 长度，无任何鉴权）→ 任何能连上端口的客户端可全量调用含 `delete_project`/`admin_delete_project` 的所有工具 → 修复：`MCP_REQUIRE_AUTH` 默认开启 + 每个工具用 FastMCP 3.4 的 `tool(auth=AuthCheck)` 做调用方校验 + 工具名改 `feature:action` 命名空间并对齐 `permission_allows`。

3. `[严重] shared.py（无公共成员校验 helper）+ projects.py:18-32` **默认配置（REQUIRE_AUTH=0）下任意 project_id/user_id 直达**：`project_access_middleware`（`api.py:350-410`）只在 `REQUIRE_AUTH=1` 生效；`shared.py` 没有检查项目成员/所有权的公共 helper（唯一例外是 `_proj_list` 的局部 `me` 探测）→ 默认部署下 `_proj_get(任意)`、`_member_remove(project_id, user_id=任意)`（`projects.py:148-149`）、`_task_delete(任意)`、`_admin_delete_project(任意)`（`admin.py:27-28`）全部无成员校验 → 修复：shared.py 提供 `require_project_member(project_id)` helper（token→uid→成员校验，403 转友好错误）并接入所有项目级工具，或 MCP 启动时强制 `REQUIRE_AUTH`。

4. `[严重] scheduling.py:77-106` **`_agent_claim_task` 并发护栏是 GET-then-POST TOCTOU**：先 `GET /api/schedules/1/runs` 查 active run（90），再 `POST /api/schedules/1/runs`（99-100）创建，两步无原子性；`idempotency_key` 含 `uuid4().hex`（98）→ 每次重试都是新 key，服务端幂等去重**永远不会触发**；`runs` 为 error dict 时 `isinstance(runs, list)` 静默跳过幂等检查 → 并发下重复 Run/重复推进 in_progress。讽刺的是 `mcp_server.py:1335-1338` 的 proposal_claim 注释恰在警告这个反模式 → 修复：改用已有 CAS 端点 `POST /api/tasks/{task_id}/claim`（`mcp_server.py:1533` 已在用）+ 固定 `agent_name-task_id` 的幂等键 + schedule 参数化（去掉硬编码 1）。

5. `[严重] mcp_server.py:789-814（list_audit_logs 工具）配合 admin/router.py:197-214` **审计日志无鉴权泄露**：REST 端点无任何 auth 依赖（对照同文件 admin 端点都有 `_require_admin`），审计条目含 `request_body`（`api_helpers.py:346` 落库、`api.py:293` 脱敏仅限 password/token）→ `REQUIRE_AUTH=1` 下任意登录用户也可经 MCP 读全量审计（含他人请求体、user_id 检索）→ 修复：REST 端点加 `_require_admin`，MCP 工具加 admin 校验。

6. `[严重] mcp_server.py:176-179 / 1009-1012 / 713-718 / 452-455 / 245-252 / 1599-1618 / 1401-1445` **LLM 一句话触发不可逆副作用、无确认机制**：`delete_project`/`admin_delete_project`（级联删除全部数据）、`batch_delete_tasks`（≤100 条）、`complete_sprint`（未完成任务退回 backlog）、`confirm_story`（触发 Agent 自动编排）、`scan_review_timeouts`（全局自动重派 reviewer/置 blocked）、`proposal_convert`/`proposal_create_ticket`（创建 Story/Task）——MCP 无原生确认，但 FastMCP 3.4 支持 `annotations=ToolAnnotations(destructiveHint=True, readOnlyHint=False, idempotentHint=...)`（客户端可弹确认）→ 修复：破坏性工具标注 `destructiveHint` + 增加 `confirm` 参数/两段式（dry-run → execute）。

### 中等

7. `[中等] shared.py:43-44` **网络异常未捕获**：`httpx.Client.request` 在 try 之外，连接失败/超时裸抛 `ConnectError`/`TimeoutException` 冒泡到 FastMCP → LLM 看到异常而非友好错误；REST 500 时 FastAPI 返回无细节的 `Internal Server Error` → 修复：`_http` 整体 try/except 折叠为 `{"error": f"REST 调用失败: {e}"}`。

8. `[中等] shared.py:45-49` **HTTP 状态码被丢弃**：403/404/409/422 全部折叠为 `{"error": detail}`，LLM 无法区分"无权限"与"不存在"、无法识别 409 幂等冲突 → 修复：错误信封加 `status` 字段（`{"error":..., "status": 403}`）。

9. `[中等] mcp_server.py:765-772` **`search_tasks_enhanced` 错误静默吞掉**：`"error" in resp → return []`，LLM 把失败当"无结果"继续执行下游；与 `search_tasks`（339-348 返回 error dict）行为不一致 → 修复：统一错误信封。

10. `[中等] projects.py:32 / mcp_server.py 各 list_* 工具` **返回类型注解与实不符**：`list_projects -> list` 但错误时返回 dict（`_proj_list` 的 `resp.get("items", resp)` 会把 error dict 原样返回）；`claim_task` 等描述 list 也可能返回 error dict → FastMCP 输出 schema 校验可能抛错、LLM 按注解误判 → 修复：统一 `list | dict` 注解或统一错误信封。

11. `[中等] projects.py:27-31` **`_proj_list` 的 admin 嗅探脆弱**：先 `GET /api/auth/me` 判 is_admin，无有效凭证时 me=`{"error": "unauthorized"}` → 静默走成员分支；stdio + 未设 `AGENTBOARD_MCP_TOKEN` 时 `list_projects` 直接返回 401 error dict；用 `abk_` key 时依赖 key 带 `api:read` → 角色判断与凭证类型耦合、多一次请求 → 修复：由 REST 提供 `scope=me` 语义或在 MCP token claims 携带 is_admin。

12. `[中等] shared.py:22-27 + mcp_server.py:72-77` **全会话共享身份**：stdio 模式无 access token → 回退环境变量 `AGENTBOARD_MCP_TOKEN`，同一进程所有调用同一身份，无法区分调用者；审计 `user_id`（`api.py:309`）在 stdio 下全部失真 → 修复：stdio 模式要求按会话注入身份。

13. `[中等] mcp_server.py:585-594` **`auth_register`/`auth_login` 作 MCP 工具且注册默认开放**：`AGENTBOARD_ALLOW_REGISTRATION` 默认 `"1"`（`auth/router.py:26`），且 register/login 在鉴权中间件白名单内（`api.py:77`）→ 任何 MCP 客户端可无凭证注册账户并拿 token → 修复：注册工具默认禁用（MCP 场景用预置服务账号）。

14. `[中等] scheduling.py:90-93` **幂等检查全量拉取 runs**：`GET /api/schedules/1/runs` 无分页遍历全部 run，O(n) 扫描；run 表增长后延迟放大 → 修复：服务端提供按 task_id 过滤的查询端点。

### 轻微

15. `[轻微] work_items.py:25 / mcp_server.py:275,293,340` 枚举/长度零校验：`type/priority/status/cron_expr` 等关键枚举 MCP 层不校验，缺参时 FastMCP 抛框架英文错误而非业务文案；`type` 作参数名 shadow 内置（合法但易误读）→ 修复：工具签名用 `Literal` 或前置校验。

16. `[轻微] mcp_server.py:510-517,1121-1133,1164-1177` 哨兵约定分散：`""`/`0` 作"清除"（update_schedule）、`remove_from_folder`/`move_to_root` 布尔哨兵——魔法值语义 LLM 易混淆 → 建议统一为显式 nullable 字段。

17. `[轻微] notifications.py:19` `unread_only` bool 经 httpx 序列化依赖 FastAPI 大小写宽容解析（`True`→`true`），行为隐晦 → 建议显式 `"true"/"false"`。

18. `[轻微] auth.py:11-13 / admin.py:11-13 / work_items.py:11-13 / documents.py:11-13 / scheduling.py:11-13 / notifications.py:11-13 / projects.py:11-13 / proposals.py:11-13` 模板残留的 `import os / typing.Any / httpx` 全部未使用（httpx 真正调用在 shared.py），documents.py:104 的 NameError 正是模板复制未清理的恶果 → 清理。

19. `[轻微] work_items.py:18-23 / projects.py:24-26` 分页参数（limit/offset 分支）与 REST `Query(ge=,le=)` 重复实现且无边界校验，负数 limit 直接透传 → 建议在 shared 提供统一分页构造器。

---

## D. 成熟度评级：beta（偏 beta 下沿）

理由：结构面已成型（分层、facade、helper 化、CAS 认领、全量重放、错误信封雏形），REST 层 DomainError 处理完整，功能覆盖面大；但存在**确定性崩溃**（记忆工具 NameError）、**零工具级鉴权**、**默认配置全开放**、**TOCTOU 认领竞态**与**审计日志泄露**等安全/健壮性缺陷——远未达 stable；若以"生产多租户安全基线"衡量应视为 alpha，但单服务账号、本机/受信网络部署下可用，故定 beta。

---

## E. 一句话总结

MCP 工具层的"传输正确性"（REST 薄包装 + 错误折叠 + CAS 语义）做得到位，但"调用者身份与权限"整层缺失——`permission_allows` 从未接入、工具名与权限命名空间零映射、默认配置无鉴权、且记忆工具存在必现 NameError，建议优先修 C-1~C-6 再谈上线。

---

## 附：评审重点对应结论

- **鉴权**：无。MCP 层 136 个工具完全不校验调用者；`permission_allows` 未接入 MCP 层（全仓仅 `api.py:96` / `api_helpers.py:76` 使用且只查 `api:read`/`api:write`）；工具名（snake_case）与 `feature:action` 权限命名空间零映射。身份只靠 `_current_token` 把 FastMCP access token 或环境变量 token 透传给 REST。
- **参数校验**：全透传依赖 REST（Pydantic/Query 兜底）；FastMCP 签名只保证类型、不保证枚举/长度；缺必填参时抛框架级错误而非业务文案。
- **错误返回**：正常路径友好（`{"error": detail}`，REST DomainError handler 兜底）；网络异常路径（`shared.py:43-44`）裸抛 httpx 异常；`search_tasks_enhanced` 错误被吞成 `[]`。
- **越权**：默认 `REQUIRE_AUTH=0` 下成立（成员校验中间件不生效）；shared.py 无公共项目成员 helper，仅 `_proj_list` 有局部收敛。
- **与 REST 重复**：参数拼装 dict、limit/offset 分支、`items` 拆包三重复；`_http`/`_current_token`/`API_URL` 双份实现。
- **危险面**：delete_project / admin_delete_project / batch_delete_tasks / complete_sprint / confirm_story / scan_review_timeouts / proposal_convert / proposal_create_ticket 等不可逆或大规模副作用工具无确认机制（FastMCP 3.4 的 `ToolAnnotations.destructiveHint` 与 `tool(auth=...)` 未使用）。
