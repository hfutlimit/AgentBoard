# AgentBoard 安全评审报告（identity / auth / admin / search 四个 feature）

- 评审日期：本会话
- 范围：`agentboard/features/identity/{models,service}.py`、`agentboard/features/auth/router.py`、`agentboard/features/admin/router.py`、`agentboard/features/search/router.py`
- 交叉核查：`agentboard/api_helpers.py`、`agentboard/service.py`、`agentboard/api.py`、`agentboard/schemas.py`、`agentboard/core/infrastructure/auth.py`、`agentboard/core/service_helpers.py`、`agentboard/core/api/deps.py`、`agentboard/features/notifications/service.py`、`agentboard/features/work_items/models.py`、`agentboard/features/projects/models.py`
- 结论：评审未修改任何文件。

---

## A. 亮点

- **生产启动安全闸**：`validate_runtime_security()`（`core/infrastructure/auth.py:31-41`）在生产环境拒绝默认密钥（<32 字节）、拒绝 `AGENTBOARD_REQUIRE_AUTH=0`、拒绝 CORS `*`，且在 `core/api/app.py:33` 与 `api.py:52` 启动时执行。
- **密码哈希与比较实现正确**：pbkdf2_sha256 600k 轮、随机 salt、`hmac.compare_digest` 恒定时间比较（`auth.py:44-70, 103`）。
- **注册唯一性竞态处理到位**：先查后插 + `_commit(duplicate=...)` 把 IntegrityError 统一映射为 Conflict（`identity/service.py:54-59` + `core/service_helpers.py:68-84`）——并发注册/邮箱唯一性安全。
- **API Key 最小化存储**：明文只返回一次，库中只存 prefix + sha256 摘要（`identity/models.py:22,29-30`；`auth.py:119-127`）；列表/详情用 `_api_key_response` 白名单字段，**不含 key_hash**（`api_helpers.py:241-247`）。
- **API Key 权限模型**：`permission_allows` 支持命名空间通配（`api:*`，`auth.py:146-151`）；`_current_user` 校验 `enabled` + `required_permission`（`api_helpers.py:72-78`）。
- **所有权校验严谨**：revoke/toggle/get API Key 均按 `user_id` 过滤（`identity/service.py:141-158, 214-215`）；改密码必须验当前密码（`identity/service.py:109`）。
- **登录不区分"用户不存在/密码错误"**：统一 401 "invalid username or password"（`auth/router.py:40-41`），无直接枚举面。
- **部分搜索已做可见性收敛**：proposals/tickets/schedules/runs 按 ProjectMember 收敛、admin 全量（`service.py:317-460`），notifications 按 user_id 隔离（`features/notifications/service.py:60-72`）——说明团队有收敛意识。
- **分页统一收口**：`_paginate` 上限 200（`service_helpers.py:56-63`），路由层 `ge/le` 双保险（`admin/router.py:149,175,204`）。
- **admin/users、admin/projects 无条件 `_require_admin`**（`api_helpers.py:307-320`），不依赖环境开关。
- **审计中间件有脱敏意识**：对 `"password"`/`"token"` 键脱敏 + 2000 字符截断（`api.py:293-296`）；`/api/audit-logs` 自身不写审计（`api.py:282`）。

---

## B. 职责 / 重复问题

- **feature 边界按 URL 段切分而非领域**：`identity/service.py` 装的是认证业务（register/authenticate/change_password），而 `auth/router.py` 却装 API Key 与 `/users/me/projects` 端点；`admin/router.py` 混入 sprints/dependencies/overview/cache 业务端点（`admin/router.py:47-111,116-141,219-226`）。auth 与 identity 谁管凭据、谁管会话无清晰边界。
- **core/api/deps.py 的 `get_current_user_optional` 在 5 个 router 中零使用**：全部手写 `authorization: str | None = Header(None)` + `api_helpers._current_user`；token 解析存在两套实现（`deps.py:28-38` vs `api_helpers.py:67-82`），且 deps 版不支持 API Key。
- **`_ser` 双份拷贝**：`service.py:183-190` 与 `core/service_helpers.py:142-158` 完全相同；`Duplicate = Conflict` 别名在 `core/exceptions.py:49` 与 `identity/service.py:34` 重复定义。
- **identity/service.py 与老 service.py 双份同步**：`update_api_key`/`get_api_key`/`lookup_api_key_by_hash`/`touch_api_key` 标着"同步自 service.py"（`identity/service.py:197-224`），两处拷贝易失步。
- **identity/service.py 内部卫生**：`get_user`（:76）与 `get_user_by_id`（:181）重复；`_count_query`（:188）、`paginated_result`（:227）、`_ = "deprecated"`（:194）死代码；`models._now()`（:208,223）混用老 facade 而本文件自称"独立自包含"。
- **密码规则分层不对称**：schema 层 min 8/max 1000-1024（`schemas.py:149,162,280`），service 层只查 min 8（`identity/service.py:52-53,111-112`）——绕过 HTTP 的调用方（MCP/worker）不受长度上限约束。

---

## C. 问题清单（按严重度排序）

1. `[严重] agentboard/features/admin/router.py:197-214（+ api.py:277-348 + features/work_items/models.py:106）`
   `/api/audit-logs` 路由无任何鉴权参数、无 admin 校验、无 user_id 隔离；审计表存 `request_body`，脱敏正则只匹配精确键 `"password"`（`api.py:293-294`），`PasswordChange` 的 `current_password`/`new_password` 键不匹配 → **明文密码落库**。生产（REQUIRE_AUTH=1）下任意登录用户、默认 dev 下匿名，均可枚举全部用户的审计日志（请求体/IP/UA）。
   → 影响：任意用户窃取他人明文密码与敏感请求体。
   → 修复：`_require_admin` + 按 user_id 隔离；脱敏改为递归 JSON 键名无关过滤（`password`、`*password*`、`token`、`api_key`、`secret` 全键名）。

2. `[严重] agentboard/features/admin/router.py:146-167（+ service.py:183-190 / service_helpers.py:142-158）`
   `_ser()` 按 `__table__.columns` 全列序列化，`admin_list_users`/`admin_update_user` 的响应包含 User 的 **`password_hash`**（pbkdf2 哈希原样返回）。
   → 影响：密码哈希泄露给管理面（离线 GPU 爆破弱密码），违反最小暴露。
   → 修复：用户序列化走 `_user_response` 白名单（`api_helpers.py:229-238`），或 `_ser` 支持 exclude 列。

3. `[严重] agentboard/features/search/router.py:21-52,72-81（+ service.py:279-315 + api_helpers.py:159-226）`
   `search_stories/epics/sprints/agents` 路由**无 authorization 参数、无项目可见性收敛**；`project_access_middleware` 的 `_resolve_project_id_from_request` 不解析 `/api/search/*`（`api_helpers.py:159-226` 无 search 分支）→ 生产模式下任何登录用户可跨项目检索全部 Story/Epic/Sprint/Agent（标题/描述/goal/roles/**cli_command**）；dev 默认模式完全匿名。同一文件内 proposals/tickets/schedules/runs/notifications 却都做了收敛（:64,94,109,124,139），唯独前四个遗漏。
   → 影响：跨项目数据（含 Agent 运行配置）泄露。
   → 修复：镜像 `search_proposals` 的 ProjectMember 收敛（admin 全量）+ 强制 `_current_user`；`_resolve_project_id_from_request` 对 search 路由返回收敛所需上下文或直接要求路由内鉴权。

4. `[中等] agentboard/features/auth/router.py:24-33,37-42`
   注册/登录**无限流、无锁定、无验证码**；注册每次消耗 600k 轮 pbkdf2。
   → 影响：登录暴力破解；注册洪水造成 CPU/DB DoS；注册 409 回显用户名辅助枚举（`auth/router.py:32`）。
   → 修复：按 IP+用户名限流（滑动窗口）、失败指数退避、注册限速。

5. `[中等] agentboard/features/identity/service.py:56-57（+ auth/router.py:26-33）`
   首个注册用户自动 is_admin，`has_users` 检查与插入之间是 **TOCTOU 竞态**（并发首注册可产生多个 admin）；且 `validate_runtime_security`（`auth.py:31-41`）不强制 `AGENTBOARD_ALLOW_REGISTRATION=0` 或初始管理员引导 → 公网开启注册时抢注即得管理员。
   → 影响：权限提升/系统接管。
   → 修复：原子化首管理员判定（DB 行锁/唯一哨兵行），生产启动校验强制关闭开放注册或要求显式初始 admin。

6. `[中等] agentboard/features/admin/router.py:231-268（+ api_helpers.py:121-142）`
   `/api/admin/ticket-requests/pending` 与 `/reclaim-stale` 的 admin 校验仅在 `_auth_is_required()` 为真时生效；默认 dev 模式下**匿名可读全局 pending 池并触发 reclaim-stale 状态变更**；且 `_caller_uid_admin` 不校验 API Key 的 permissions（与 `_require_admin` 不一致）。
   → 影响：默认配置下管理员级操作公开。
   → 修复：与 admin/users 一致无条件 `_require_admin`；统一走 `_current_user` 权限校验。

7. `[中等] agentboard/features/search/router.py:22-24,35-37,47-49（+ service.py:281,288,295,305）`
   搜索 `%{q}%` 未转义 `%`/`_`/`\`，q 仅有 `min_length=1` 无 max_length；非锚定 LIKE 全表扫描。
   → 影响：通配符放大匹配面 + 无上限输入叠加无鉴权/无限流 = 搜索成本放大（CPU/IO DoS）。
   → 修复：`escape()` 通配符、q 上限（如 200）、必要时全文索引。

8. `[中等] agentboard/core/infrastructure/auth.py:78-105（+ identity/service.py:106-114）`
   无状态 token **无撤销机制**：改密码、禁用/删除 API Key 后已签发 token 仍有效（无 password 版本号、无黑名单）；TTL 48h 无刷新。
   → 影响：token 泄露后改密无法作废，会话长期有效。
   → 修复：token 载荷携带 password_hash 版本（或用户版本号），校验时比对；或维护撤销表。

9. `[中等] agentboard/features/identity/service.py:65-73`
   用户不存在时直接 return None（跳过 pbkdf2）→ 响应时间差可枚举用户名，配合问题 4 无限制可放大。
   → 影响：用户名枚举（timing side channel）。
   → 修复：用户不存在时也执行一次 dummy verify 保持恒定时间。

10. `[中等] agentboard/features/auth/router.py:80-86（+ identity/service.py:119-134）`
    `create_api_key` 无 `required_permission`（空权限 key 也能再建 key）；服务层不校验 permissions 格式（仅 schema 层，`schemas.py:294-300`）；权限模型允许用户自授任意 namespace（含 `admin:*`），一旦未来端点以 permission 字符串而非 `user.is_admin` 判权即提权。
    → 影响：凭证泛滥 + 潜在提权后门。
    → 修复：创建/更新 key 需 api:write；服务层复用 `_PERMISSION_RE` 白名单；敏感 namespace 判定强制绑定 is_admin。

11. `[中等] agentboard/features/admin/router.py:47-111,219-226`
    sprint/dependency 业务端点（含 delete_sprint、delete_dependency 等写操作）**无路由内鉴权**，保护完全依赖 middleware 项目解析；孤儿数据（`get_xxx_project_id` 返回 None）会被 `_resolve_project_id_from_request` 放行（`api_helpers.py:159-226`）。
    → 影响：中间件解析失败即脱管；delete 类操作仅 member 级。
    → 修复：迁回对应 feature + 路由内显式 `_current_user`/owner 校验。

12. `[轻微] agentboard/features/auth/router.py:33,42（+ auth.py:25）`
    注册/登录响应直接返回 token；`AGENTBOARD_SECRET` 默认 `dev-insecure-secret-change-me`（有生产启动校验兜底，但 dev/测试环境任何人可伪造任意 uid 的 token）。
    → 影响：测试环境 token 伪造面。
    → 修复：dev 也生成随机密钥（启动时若为默认值则警告+随机化）。

13. `[轻微] agentboard/api_helpers.py:121-142 vs 67-82`
    `_caller_uid_admin` 每次请求开 2 个独立 SessionLocal（不复用请求 session），且不校验 API Key permissions；`last_used_at` 只在 require_business_auth middleware 更新（`api.py:92-93`），`_current_user` 路径不更新 → dev 模式 last_used_at 永不更新。
    → 影响：会话开销 + 审计字段失真 + 权限校验不一致。
    → 修复：统一走 `_current_user`，复用请求 session，统一 touch。

14. `[轻微] agentboard/features/admin/router.py:133-141`
    `cache_stats` 无路由内鉴权（依赖 middleware，docstring 自述）。
    → 影响：任意登录用户/匿名可读缓存统计。
    → 修复：加 `_current_user` 或明确 admin。

---

## D. 成熟度评级：**beta**

理由：安全基座（HMAC 无状态 token、pbkdf2 600k、生产启动校验、API Key 摘要存储、统一异常映射）设计正确且有生产兜底，但存在审计日志明文密码、`_ser` 泄露 password_hash、跨项目搜索无收敛三处数据泄露级 P0，加上限流缺失与首管理员竞态，未达 stable 门槛。

---

## E. 一句话总结

基座扎实但边界失守：三处数据泄露（审计日志明文密码 / `_ser` 全列序列化 / search 无可见性收敛）是上线前必须修的 P0，其余为限流、竞态与 feature 边界整理问题。
