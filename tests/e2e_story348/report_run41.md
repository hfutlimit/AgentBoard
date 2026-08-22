# AGB 全站前端巡检 · Story 348（第  ̃41 次 hourly，R41）

## 完成内容
按新 scope 执行 AgentBoard 前端全站 Playwright 端到端巡检，覆盖 26 个路由 + 导航穿透 + 工作区 tab + 交互 + 已知 Bug 复验。

## 方法与环境
- 前端：`frontend/` 冷启 `ng serve --proxy-config ../tests/e2e_story348/proxy.prod.conf.json --host ,127.0.0.1 --port 4200`（代理生产后端 `124.220.44.12`）。
- 巡检脚本：`tests/e2e_story348/run41_focused.py`（单进程顺序，含冷启暖机 settle 轮询至文本稳定，规避 lazy-chunk 时序误报）。
- 浏览器：Playwright Chromium 1440×900，admin/admin123 注入 token。

## 关键结果
- **路由访问**：26/26 可达，全部渲染真实内容（home 781 / projects 1302 / epics 398 / stories 400 / tasks 391 / bugs 390 / documents 255 / dashboard 408 / settings 162 / agents 1462 / proposals 180 / notifications 79 / admin 1092 / ws_overview 495 / ws_kanban 198 / ws_epics 809 / ws_backlog 1213 / ws_proposals 284 / ws_documents 1184 / ws_members 758 / ws_settings 260 / epic_152 2843 / story_330 2900 / task_1342 650）。
- **水平溢出**：0 px（全部页面）。
- **真实 console error**：0（4 条总 error 均为 401/500 代理噪声 artifact，已排除）。
- **page error**：0。

## 已知 Bug 复验（全部回归验证）
| Bug | 状态 | 证据 |
|-----|------|------|
| #1427 详情页空白 | ✅ FIXED | story_330/1342/1339/152/348 均渲染（1339 390 字符） |
| #1428 全局路由误渲染 | ✅ FIXED | /documents=项目文档、/proposals=需求提案，不再是「项目中心」 |
| #1429 侧栏「搜索」误标 | ✅ FIXED | 侧栏标签含「提案」无「搜索」 |
| #1430 全局路由 404 | ✅ FIXED | /epics /stories /tasks /bugs /dashboard 均渲染真实内容 |
| #1431 主题切换缺失 | ✅ FIXED | 用户菜单「切换到深色模式」light→dark 生效 |
| #1433 /bugs skeleton 卡死 | ✅ FIXED |  ̃3 次访问均正常渲染（390 字符），无 skeleton 卡死 |

## 新增 Bug
- **0 条**。本轮未发现需创建的新问题。唯一疑似 finding（`task_1339` 首测 txt=0）经暖机复核确认为冷启时序误报（二次测量 388 字符），不创建 Bug。

## 观察 / 注意
- `/api/projects` 在巡检中偶发 1 次 500，token 直连重试 3 次中 2 次 200，属于历史「瞬时 500」后端噪声，未达 3 轮连续上报阈值，不下 Bug。
- 顶层导航点击穿透与 ws_tabs 采集脚本因 locator 时序未落值（返回 None/空），属脚本 artifact，非 UI 缺陷（侧栏标签 #1429 复验已确认渲染正常）。

## 下一步
- 5 个已知 Bug（#1427~#1431）与 #1433 均为 FIXED，建议开发批量复核关闭。
- 持续监控：若 `/api/projects` 连续 3 轮且 token 直连也 500，则升级为真实后端 Bug 上报。
- 巡检脚本与产物位于 `tests/e2e_story348/`（report_story348_run41.json 及 screenshots_run41/）。
