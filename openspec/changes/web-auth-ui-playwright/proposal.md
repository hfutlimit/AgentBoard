# Change: Web 鉴权页面（注册/登录）+ Playwright 端到端自动化

## Why
当前 Web SPA 没有注册/登录界面，用户无法在页面上完成身份认证；且缺乏浏览器级（真实 DOM）自动化测试。需要：
1. 在 SPA 中加入真实可操作的注册/登录页面，并持久化 token；
2. 用 Playwright 驱动真实浏览器覆盖这些页面与核心 CRUD 流程。

## What Changes
- SPA（`web/static/index.html` + `app.js`）：新增头部鉴权区（登录/注册按钮）、登录/注册弹窗、localStorage 持久化 token、`api()` 自动附带 `Authorization`；
- 新增 Playwright 端到端测试 `tests/test_web_playwright.py`：启动真实 api + web，浏览器驱动注册/登录（含错误分支）与 `project → epic → story → task/bug` 的创建/修改/列表读取。

## Impact
- Web UI 新增鉴权交互；不影响现有 CRUD 端点（仍单用户开放）。
- 新增开发依赖 `playwright`（仅测试用）。
- API 行为不变。

## Status
Draft（实现中）
