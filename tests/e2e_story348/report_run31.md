# AGB 全站前端巡检报告 — Story 348（第 31 次 hourly 巡检）

**执行时间**：2026-08-21 11:35（hourly 自动触发）
**巡检范围**：AGB 项目（project_id=3）/ Story 348「页面持续修复 · 新版前端 UI 问题收集」
**目标**：对 AgentBoard 前端全部页面与功能做端到端 Playwright 巡检，发现问题以 Bug 关联到 Story 348。

## 环境与流程
- 前端：`ng serve`（从 `frontend/`，`--proxy-config ../tests/e2e_story348/proxy.prod.conf.json` 代理生产后端 `http://124.220.44.12`）冷启成功（编译 10.1s，http 200）。
- 浏览器：Playwright Chromium（`.venv`，1440×900，locale zh-CN），登录 admin/admin123，SPA 内交互。
- 脚本：`inspect_all_v6.py`（26 路由 + 顶层导航点击穿透 + 交互）+ `inspect_v14_recheck.py`（冷启暖机复核）。
- 真实指标：26/26 路由可达；**真实 console error 0**（1 artifact）、**page error 0**、**真实 API 失败 0**、**水平溢出 0**。

## 本轮结论
**新建 Bug：0 条**。

### 初判 finding 全部裁定为「冷启时序误报」
主巡检 `inspect_all_v6` 初判 10 条 P1「主内容文本=0 字符」空白（/、/projects、/epics、/tasks、/bugs、/documents、/settings、/agents、/notifications、/admin）。经 `inspect_v14_recheck.py` 暖机复核（轮询直到文本稳定 / 最长 20s）：
- 全部页面暖机后正常渲染：home=775、projects=1304(h1 项目中心 11)、epics=398(h1 全局 Epics 概览)、tasks=391(h1 全局 Tasks 概览)、bugs=390(h1 全局 Bugs 概览)、documents=255(h1 项目文档 0)、settings=650(h1 个人设置)、agents=1462(h1 Agent 池 7)、notifications=79(h1 通知中心 0)、admin=1092(h1 ⚙ 管理员后台)。
- 同一路由前后两次测量（775↔0）互相矛盾，确认为 dev server 冷启 lazy chunk 编译中的 skeleton 态，非真实缺陷。

### 已知 Bug 复验（本轮真实测量）
| Bug | 内容 | 本轮状态 | 说明 |
|---|---|---|---|
| #1427 详情空白 | story/task 详情空白 | ✅ FIXED | 330/1342/1339/152 均正常渲染；story_348 is_404=True 为评论文本含「页面不存在」误判 |
| #1428 全局路由误渲染 | /documents、/proposals 误渲染为项目中心 | ✅ **FIXED** | documents h1=「项目文档 0」、proposals h1=「需求提案 0」，不再等于「项目中心」 |
| #1429 侧栏「搜索」误标 | 侧栏 proposals 标签误写为「搜索」 | ✅ FIXED（已 done） | 侧栏 = [概览,看板,Epics,工作项,提案,文档,成员与 Agents,设置]，含「提案」无「搜索」 |
| #1430 全局路由 404 | /epics /stories /tasks /bugs /dashboard 命中 404 | ✅ **FIXED** | 全部渲染真实内容（全局 Epics/Stories/Tasks/Bugs 概览 + 项目大脑/总览） |
| #1431 主题切换缺失 | 无暗色/亮色切换控件 | 🔴 STILL | header 枚举按钮 0 命中、dataset.theme 不翻转；仍未修复 |

## 关键决策 / 变化
- **本轮最大变化**：#1428 与 #1430 相对上一轮（第 30 次）由「STILL」转为「已修复」。生产前端最近的部署修复了全局路由注册与 /documents、/proposals 渲染，建议开发复核后关闭这两条 Bug。
- #1431（主题切换缺失）已连续多轮复现，仍为唯一未解决的真实缺陷。

## 产物
- `tests/e2e_story348/inspect_all_v6.py`（主巡检脚本，未改）
- `tests/e2e_story348/inspect_v14_recheck.py` + `report_v14_recheck.json`（新增暖机复核）
- `tests/e2e_story348/report_story348.json`（主巡检报告）、`report_run31.md`
- 截图 `screenshots_v6/`、`screenshots_v14/`（gitignored 本地证据）

## 下一轮注意
1. 持续监控 #1431 修复；建议开发复核关闭 #1427 / #1428 / #1429 / #1430。
2. 冷启 0 字符必走暖机复核，禁止直接报 P1 空白（本轮再次印证）。
3. 生产后端偶发不可达已在脚本登录重试中规避；若 MCP 报网络错，先 `curl` 探活后重试。
