# Tasks: Admin Portal 基础框架

## 实现
- [x] `ng generate application admin-portal --routing --style=css --ssr=false --skip-tests`（在 frontend 工作区新增第二应用，复用 node_modules）
- [x] `app.config.ts`：启用 `provideRouter` + `provideHttpClient`
- [x] `app.routes.ts`：路由 `''→login`、`login`、`dashboard`(authGuard)、`**→login`
- [x] `auth.guard.ts`：函数式 `CanActivateFn`，校验 `localStorage['admin_portal_token']`
- [x] `api.service.ts`：`login()` / `me()`，自动注入 `Authorization: Bearer <token>`
- [x] `login/`：登录表单（用户名/密码）、提交调 `/api/auth/login`、存 token、跳 dashboard、错误告警、回车提交
- [x] `dashboard/`：受守卫保护的占位仪表盘，调 `/api/auth/me` 展示用户名，退出登录
- [x] `styles.css`：全局 premium 主题（light/dark 自适应、品牌渐变、卡片/按钮样式）
- [x] `proxy.conf.json`：dev `/api → 127.0.0.1:58125`
- [x] 修复：登录响应字段为 `token`（非 `access_token`），避免 `me()` 401

## 验证
- [x] `ng build admin-portal` 构建通过（login/dashboard 懒加载 chunk 正常产出）
- [x] E2E `tests/test_admin_portal_login_e2e.py`：登录渲染 / 错误凭据告警 / 正确登录存 token 跳转 / 守卫拦截未登录 / 重新登录；0 pageerror / console 错误 / .js+.css 404，无预期外 401
- [x] 回归：后端 `pytest test_epic30_cache.py` 8 passed（零后端改动）

## 状态流转
- [x] Task 850（初始化 admin-portal Angular 项目）→ in_review（backlog→todo→in_progress→in_review）
- [x] Task 851（实现登录页）→ in_review（同上合法链）
- [x] Task 856（样式与主题）→ in_review（同上合法链）
- Story 71 / 其 Epic 保持部分完成状态（仅 3/7 任务推进），不误标 done
