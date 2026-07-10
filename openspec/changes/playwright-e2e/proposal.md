# Change: 前端 Web 自动化测试（Playwright）

## Why
现有 `tests/test_web_flow.py` 用 `httpx` 直接对"已启动的 API"发请求来**模拟** SPA 行为，并不驱动真实浏览器 DOM / 交互。它验证的是"接口等价行为"，无法捕获 UI 层回归（如表单提交、路由渲染、状态徽标更新、markdown 渲染）。

需要一个**真实浏览器**级别的端到端测试，覆盖登录 / 注册界面流与项目树 CRUD 的真实点击交互。

## What Changes
- 引入 Playwright（Chromium）+ `pytest-playwright`。
- 新增 `tests/test_playwright_e2e.py`：启动真实 API + Web 服务，用 Chromium 打开 SPA，真实操作 UI。
- 与 `test_web_flow.py` 互补：前者验证 UI 行为，后者验证接口等价。

## Impact
- 新增测试依赖 `playwright` / `pytest-playwright`（`requirements.txt`）。
- 运行时需浏览器二进制（`playwright install chromium`）；建议本地 / CI 均可执行。
- 不改变应用代码；仅新增测试。

## Status
Draft（待实现）
