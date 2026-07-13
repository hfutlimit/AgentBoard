# Design

## SPA 鉴权
- 头部 `#auth` 区：未登录显示「登录」「注册」按钮；已登录显示用户名 + 「退出」。
- token 存 `localStorage`（key `agentboard_token`）。
- `api()` 在存在 token 时附加 `Authorization: Bearer <token>`。
- 登录/注册弹窗复用 `#modal` 容器；提交调用 `/api/auth/login`、`/api/auth/register`，成功则存 token 并重渲染鉴权区。
- 登录成功后调用 `/api/auth/me` 显示用户名；token 失效时清除并退回未登录态。

## Playwright 测试
- fixture 以子进程启动真实 api（`agentboard.api:app`）与 web（`agentboard.web_app:app`，`AGENTBOARD_API_URL` 指向 api）；
- 用 `sync_playwright` 启动 chromium，`page.goto(web)`；
- 通过 DOM 选择器驱动：点击 `#regbtn` / `#loginbtn`，填充表单，断言 `#who` / `#lerr` 等；
- CRUD 复用 SPA 已有表单（`#f` 建项目、`#fc` 建 epic/story/task、`#fe` 编辑 task），断言列表渲染结果。
