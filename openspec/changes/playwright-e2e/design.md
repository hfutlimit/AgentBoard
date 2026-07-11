# Design — 前端 Web 自动化测试（Playwright）

## 服务启动（复用既有模式）
复用 `test_web_flow.py` 的 `_start_server` / `_wait` 模式：
- 独立临时 SQLite（`AGENTBOARD_DB_URL=sqlite:///<temp>`）+ `AGENTBOARD_MCP_BACKEND=db`。
- 以子进程真实拉起 `agentboard.api:app`（空闲端口）+ `agentboard.web_app:app`（空闲端口，注入 `AGENTBOARD_API_URL` 指向 API）。

## 浏览器驱动
- 使用 `pytest-playwright` 的 `page` fixture（Chromium）。
- `page.goto(web_base + "/")`。
- 辅助函数 `ui_register(page, user, pw)` / `ui_login(page, user, pw)`：按 DOM 选择器填写并提交（与 `auth` 变更实现的界面结构对齐）。

## 用例（真实交互）
1. **注册 UI 流**：填写用户名/密码 → 提交 → 断言进入应用（项目列表出现）；`localStorage` 含 `agentboard_token`。
2. **登录 / 错误分支**：错误密码显示报错；重复注册显示报错。
3. **项目树 CRUD UI**：新建 Project → Epic → Story → Task/Bug，断言对应 DOM 节点出现。
4. **状态流转 UI**：切换任务状态，断言状态徽标更新。
5. **spec 编辑与 markdown 渲染**：保存 spec，断言渲染后的元素（如 `<h2>`）存在。
6. **持续前端优化回归**：在窄屏 viewport 断言侧栏堆叠且操作按钮可见；连续触发多条 Toast，断言提示同时存在并独立移除；从 Story 列表与看板打开任务抽屉，验证 description/spec、状态更新、遮罩与 Esc 关闭，且单击不改变当前路由。

## 稳定性
- 用 `page.wait_for_selector(...)` 等显式等待，避免硬编码 `sleep`。
- 对"提交后重渲染"的断言等待目标选择器出现。

## 跳过策略
- 若未安装浏览器二进制，用 `pytest.mark.skipif` 跳过（或文档要求先 `playwright install chromium`）。
