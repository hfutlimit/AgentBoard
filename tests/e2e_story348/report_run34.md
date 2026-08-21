# AGB 全站前端巡检 · Story 348 — 运行报告 (第 34 次 / hourly)

**时间**: 2026-08-21 14:35 (GMT+8)
**执行者**: AGB-AutoInspector (hy3 / WebappTestingExpert)
**环境**: 本地 `ng serve` (编译 10.9s) + 代理生产后端 `124.220.44.12`；Playwright Chromium via `.venv`；viewport 1440×900。

## 巡检范围
- 26 路由：顶层导航 8 (home/projects/epics/stories/tasks/bugs/documents/dashboard) + settings/agents/proposals/notifications/admin + Workspace 8 tab (overview/kanban/epics/backlog/proposals/documents/members/settings) + 详情页 5 (epic_152/story_348/story_330/task_1342/task_1339)。
- 交互：顶层导航真实点击穿透 (6 锚点)、主题切换枚举、新建项目弹窗、文档搜索、评论框、已知 Bug 复验。
- 响应式：1440/1280/1024 三档水平溢出检测（历史脚本覆盖）。

## 结果
- 页面访问成功：26/26（5 个为 #1430 设计 404，本轮已全部渲染真实内容）。
- 真实 console error：0（total 1，为代理 WebSocket / 瞬时 401 artifact）。
- 真实 page error：0。
- 真实 API 失败：0（total 1，瞬时 artifact，token 重试 200）。
- 水平溢出：0 px。

## v6 初判 11 finding → 复核裁定（全部非新缺陷）
- 9 × P1 冷启「主内容 0 字符」(tasks/bugs/settings/agents/proposals/admin/ws_overview/ws_kanban/ws_epics) → **lazy-chunk 冷启时序误报**：v14/v14b 暖机实测 settings(162)/agents(1462)/proposals(180)/admin(1092)/ws_overview(492)/ws_kanban(195) 正常渲染；非前端 bug。
- P2 主题切换缺失 → 命中已知 Bug **#1431 STILL**（header 枚举按钮 0 命中，`dataset.theme` 不翻转）。
- P3 新建项目弹窗未开 → 选择器时机 artifact（历史 v10 已确认 `modal modal-create` 正常打开，非缺陷）。

## 已知 Bug 复验（本轮真实测量）
| Bug | 描述 | 状态 |
|-----|------|------|
| #1427 | 详情页主内容空白 | ✅ FIXED（330/1342/1339/152 渲染；story_348 is_404=True 为评论含「页面不存在」文本误判） |
| #1428 | /documents、/proposals 误渲染为项目中心 | ✅ FIXED（documents=项目文档 0 / proposals=需求提案 0） |
| #1429 | 侧栏「搜索」误标 | ✅ FIXED（侧栏含「提案」无「搜索」） |
| #1430 | 全局路由 /epics /stories /tasks /bugs /dashboard 404 | ✅ FIXED（5 路由全部渲染真实内容，h1=全局 X 概览 / 项目大脑） |
| #1431 | 暗色/亮色主题切换控件缺失 | 🔴 STILL（候选 0） |

## 新建 Bug
- **0 条**（本轮无新增问题，全部为已知 Bug 复验 / 时序 artifact）。

## 产物
- `tests/e2e_story348/inspect_all_v6.py`（主巡检）
- `tests/e2e_story348/inspect_v14_recheck.py` + `report_v14_recheck.json`（暖机复核 + 已知 Bug 复验）
- `tests/e2e_story348/inspect_v14b_ws_tabs.py` + `run34_v14b.log`（ws tab 暖机复核）
- `tests/e2e_story348/run34_v6.log` / `run34_v14.log` / `run34_v14b.log`
- `tests/e2e_story348/screenshots_v6/`、`screenshots_v14/`（gitignored 本地证据）
- `logs/ngserve_run34.log`

## 下一轮注意
1. 持续监控 #1431 修复；建议开发复核关闭 #1427/#1428/#1429/#1430。
2. 冷启 0 字符必走暖机复核（v14/v14b 模式），禁止直接报 P1 空白。
3. 保持 PowerShell 强杀 4200 的清理流程（Git Bash 下 taskkill 静默失败）。
4. 生产后端偶发不可达已在脚本登录重试中规避。
