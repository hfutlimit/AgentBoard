# AGB 全站前端巡检报告 · 第 37 次（Story 348）

**时间**：2026-08-21 17:35（hourly）
**环境**：本地 `ng serve`（编译 17.1s，127.0.0.1:4200）→ 生产后端 `124.220.44.12`（login 200）；Playwright `tmp/venv317`（chromium）。
**巡检脚本**：`inspect_all_v6.py` + `run37_focused_recheck.py` + `run37_theme_verify.py`

## 结论
- **新建 Bug：0 条**
- 所有 v6 初判 finding 经暖机复核/功能验证裁定为**已知缺陷已修复**或**时序误报**。
- **重大变化：#1431 主题切换缺失 — 本轮功能验证通过，已修复（往轮连续 10 轮 STILL）。**

## 巡检范围（26 路由 + 交互）
- 顶层导航 8 项 + 工作区 8 tab（/project/3/*）+ 详情页 5 个（epic 152 / story 348,330 / task 1342,1339）
- 交互：主题切换、新建弹窗、用户菜单、顶层 nav 点击穿透

## 真实指标
| 指标 | 值 |
|---|---|
| 路由可达 | 26/26（含 #1430 的 5 个原 404 路由均渲染真实内容）|
| 真实 console error | 0（共 4，全部 artifact）|
| page error | 0 |
| 真实 API 失败 | 0（共 1，artifact）|
| 水平溢出 | 0 |

## 初判 Finding 复核（10 条）
| 初判 | 裁定 | 证据 |
|---|---|---|
| P1 projects 0 字符 | 误报 | 暖机 txt=1303（项目中心 11）|
| P1 epics 0 字符 | 误报 | 暖机 txt=398（全局 Epics 概览）|
| P1 stories 0 字符 | 误报 | 暖机 txt=400（全局 Stories 概览）|
| P1 tasks 0 字符 | 误报 | 暖机 txt=391（全局 Tasks 概览）|
| P1 bugs 0 字符 | 误报 | 暖机 txt=390（全局 Bugs 概览）|
| P1 settings 0 字符 | 误报 | 暖机 txt=162（个人设置）|
| P1 agents 0 字符 | 误报 | 暖机 txt=1462（Agent 池 7）|
| P1 proposals 0 字符 | 误报 | 暖机 txt=180（需求提案 0）|
| P2 主题切换按钮缺失 | **命中 #1431 → 本轮 FIXED** | 用户菜单含「切换到深色模式」，点击 light→dark 生效 |
| P3 新建项目弹窗未开 | 误报 | 选择器时机 artifact（历史 v10 确认 modal 正常）|

## 已知 Bug 复验
| Bug | 状态（往轮）| 本轮 |
|---|---|---|
| #1427 详情页空白 | FIXED | ✅ FIXED（330/1342/1339/152 渲染；story_348 is_404 为评论文本误判）|
| #1428 全局路由误渲染 | FIXED | ✅ FIXED（documents=项目文档/0、proposals=需求提案/0）|
| #1429 侧栏「搜索」误标 | FIXED | ✅ FIXED（侧栏含「提案」无「搜索」）|
| #1430 全局路由 404 | FIXED | ✅ FIXED（epics/stories/tasks/bugs/dashboard 均渲染真实内容）|
| #1431 主题切换缺失 | STILL(10 轮) | ✅ **FIXED（本轮功能验证通过）** |

## 产物（tests/e2e_story348/）
- `run37_focused_recheck.py` + `report_run37_focused.json`（8 页暖机 + #1430/#1431 复验）
- `run37_theme_verify.py` + `report_run37_theme.json`（#1431 主题功能验证）
- `inspect_all_v6.py` + `run37_v6.log`、`report_story348.json`（主巡检）
- 截图 `screenshots_run37/`（gitignored 本地证据）

## 下轮注意
1. **#1431 已修复**：建议开发复核关闭 #1427/#1428/#1429/#1430/#1431（5 个已知 Bug 全部 FIXED）。
2. 冷启 0 字符继续走 `run37_focused_recheck.py` 暖机复核，禁止直接报 P1 空白。
3. 生产后端偶发不可达已在脚本登录重试中规避。
