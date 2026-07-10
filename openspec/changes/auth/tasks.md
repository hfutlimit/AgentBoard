# Tasks — 前端注册 / 登录集成

## 1. 前端鉴权骨架
- [ ] `web/static/app.js`：新增 `getToken / setToken / clearToken`（localStorage 读写）。
- [ ] 改造 `api()`：自动从 localStorage 注入 `Authorization: Bearer <token>`；收到 `401` 时清 token 并回到登录界面。
- [ ] `web/static/index.html`：预留登录 / 注册容器（默认隐藏，授权后展示 `#app`）。

## 2. 登录 / 注册界面
- [ ] `app.js` 增加 `renderAuth()`：用户名 / 密码输入 + 登录/注册切换；调用 `/api/auth/register`、`/api/auth/login`。
- [ ] 注册 / 登录成功后 `setToken` 并 `render()` 进入应用；失败（409 / 401）在卡片内展示错误文案。
- [ ] 启动守卫：加载时若有 token 且 `/api/auth/me` 通过则进应用；否则显示登录界面。

## 3. 应用内用户态
- [ ] 顶部栏展示当前用户名（来自 `/api/auth/me`）+「登出」按钮，点击 `clearToken()` 并回到登录界面。
- [ ] `style.css`：补充登录卡片、用户信息条样式（沿用 `.card/.badge/.muted` 体系）。

## 4. 可选：后端强制鉴权开关
- [ ] `api.py` 增加 `AGENTBOARD_REQUIRE_AUTH`：开启时 CRUD 端点经 `_current_user` 校验（默认关闭）。

## 5. 验证
- [ ] 手动走查：注册 → 登录 → 进入应用 → 登出 → 重新登录全流程无碍。
- [ ] 关联 `playwright-e2e` 变更：在真实浏览器补充登录 / 注册 UI 自动化用例。
- [ ] 更新 `openspec/specs/agentboard/spec.md` 的「Web UI / 鉴权」小节（实现完成后）。
