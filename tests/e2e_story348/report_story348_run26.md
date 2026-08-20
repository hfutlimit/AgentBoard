# AGB 全站前端巡检报告 — Story 348（第 26 次执行）

**运行时间**: 2026-08-21 06:35 (hourly)  
**环境**: `ng serve` (frontend/, 冷启 10.2s) + proxy `tests/e2e_story348/proxy.prod.conf.json` → 生产后端 `http://124.220.44.12`  
**脚本**: `inspect_all_v6.py`（顶层导航点击穿透 + 26 路由覆盖 + 主题/弹窗/搜索/评论交互）+ `inspect_v6b_recheck.py`（暖机复核 9 全局页 + 侧栏导航 dump）+ `inspect_v6c_theme_dialog.py`（主题/弹窗 DOM 枚举）+ `inspect_v6d_dropdown_dialog.py`（用户菜单 + 弹窗深究）  
**Playwright**: Chromium 1440×900, admin/admin123, localStorage token 注入

---

## 一、范围与覆盖

| 类别 | 覆盖 |
|---|---|
| 顶层导航点击穿透 | header/topbar/sidebar 全部 `a[href^="/"]`：首页(/)、项目(/projects)、工作台(/project/3)、通知(/notifications)、我的(/settings) |
| 全局一级路由 | / , /projects, /epics, /stories, /tasks, /bugs, /documents, /dashboard, /settings, /agents, /proposals, /notifications, /admin |
| Workspace 8 tab | /project/3/{overview,kanban,epics,backlog,proposals,documents,members,settings} |
| 详情页 | /epic/152, /story/348, /story/330, /task/1342, /task/1339 |
| 交互 | 顶层导航点击、Workspace tab 点击、主题切换、新建项目弹窗、documents 搜索、story/348 评论框 |

---

## 二、结果汇总

| 指标 | 值 |
|---|---|
| 路由访问成功 | 26/26（含 5 个确认 404 的全局路由） |
| 真实 console error | 0（1 条瞬时产物噪声） |
| page error | 0 |
| 持续 API 失败 | 0（1 条瞬时产物噪声） |
| 水平溢出 | 0 |
| **新建 Bug** | **2**（#1430, #1431） |

---

## 三、本轮新建 Bug

### Bug #1430（P2 / medium）— 全局路由 /epics /stories /tasks /bugs /dashboard 访问返回 404
- **现象**: 5 个巡检 scope 列出的一级导航路由均渲染「页面不存在」404（主内容仅约 10 字符）。
- **根因（源码确认）**: `frontend/src/app/app.routes.ts` 全局路由表**未定义**这 5 条路由，访问命中 `**` 通配 → RouteAnchor → `app.ts loadRoute` 显示 404。Epics/Stories/Tasks/Bugs 仅能经工作区（/project/3/epics）及详情页（/story/:id、/task/:id）访问。
- **验证**: v6b 对 5 路由做 networkidle + 轮询测量（最长 20s），final_text_len 恒=10、is_404=true（稳定非瞬态）；对照 /documents、/proposals（#1428）、/agents、/settings、/admin 均正常。
- **证据**: `screenshots_v6_recheck/recheck_{epics,stories,tasks,bugs,dashboard}.png`
- **与 #1428 区别**: #1428 是路由存在但内容渲染错（项目中心）；本 Bug 是路由根本未定义→404。

### Bug #1431（P2 / medium）— 暗色/亮色主题切换控件缺失
- **现象**: UI 中**无任何主题切换入口**。巡检 scope 要求的「暗色/亮色主题切换」不可用。
- **验证**:
  - v6/v6c：首页全部 16 个 button 枚举，无 ☀/🌙/主题/sun/moon 标识按钮；theme candidates=[]。
  - v6d：用户菜单下拉项 = [A admin, 协作成员, 个人设置, 退出登录]，无主题项；全页扫描含「主题/暗色/亮色/深色」文本元素 = 空；`document.documentElement.dataset.theme` 恒='light' 不翻转。
- **证据**: `screenshots_v6_recheck/user_menu_open.png`、`theme_via_menu.png`
- **说明**: 历史巡检（第 22~25 轮）曾将 theme_toggle 判为「选择器误报」并假设机制存在；本轮经完整 DOM 枚举确认控件**确实缺失**，非选择器问题。

---

## 四、已知 Bug 复验

| Bug | 状态 | 证据 |
|---|---|---|
| #1427 详情页空白 (story/330, task/1342, task/1339) | ✅ **FIXED** | v6 暖机复核 5 详情页均正常渲染（txt 2845/8967/2903/650/388），无空白。 |
| #1428 全局 routes 误渲染 (/documents, /proposals) | 🔴 **STILL REPRODUCES** | v6b 复验：/documents、/proposals h1 仍 = `项目中心\n11`，renders_as_project_center=True。 |
| #1429 侧栏「搜索」误标 | ✅ **FIXED** | v6 侧栏真实 tab = [概览,看板,Epics,工作项,提案,文档,成员与 Agents,设置]，含「提案」无「搜索」。 |

---

## 五、v6 初判 finding 复核（9 条全部经暖机/深究判定）

| v6 初判 finding | 复核结论 | 根因 |
|---|---|---|
| 7× 全局页主内容 txt=0（stories/tasks/bugs/documents/dashboard/settings/agents/proposals） | 时序误报 | 冷启 dev server 下 Lazy chunk + 数据加载慢，首测窗口内未渲染；v6b 暖机（networkidle + 轮询）后 /documents(1305)/settings(650)/agents(1462)/proposals(1305) 均正常，/epics/stories/tasks/bugs/dashboard 稳定 404（见 #1430）。 |
| 主题切换按钮未找到 | **真实缺陷** | 完整 DOM 枚举确认控件缺失 → 新建 #1431。 |
| 新建项目弹窗未打开 | 时序误报 | v6d 确认 `modal modal-create` 正常打开并含表单字段（搜索框/SELECT/checkbox），仅 selector 命中时机问题。 |

---

## 六、本轮产物

- `tests/e2e_story348/inspect_all_v6.py` — 主巡检（顶层导航点击穿透 + 26 路由 + 交互）
- `tests/e2e_story348/inspect_v6b_recheck.py` + `report_v6b_recheck.json` — 暖机复核 + 侧栏导航 dump
- `tests/e2e_story348/inspect_v6c_theme_dialog.py` + `report_v6c_theme_dialog.json` — 主题/弹窗 DOM 枚举
- `tests/e2e_story348/inspect_v6d_dropdown_dialog.py` + `report_v6d_dropdown_dialog.json` — 用户菜单 + 弹窗深究
- `tests/e2e_story348/report_story348.json` — v6 原始数据
- `tests/e2e_story348/screenshots_v6/`、`screenshots_v6_recheck/`（gitignored 本地证据）
- 本文件 `report_story348_run26.md`

---

## 七、建议

**开发**:
- 复核关闭 **#1427**（详情空白）与 **#1429**（侧栏搜索误标）：本轮验证通过。
- 继续修复 **#1428**（全局 /documents、/proposals 误渲染为项目中心）。
- 评估 **#1430**（5 个全局列表路由 404）：确认产品意图——补全局路由 + 视图，或在 UI 导航移除死链。
- 评估 **#1431**（主题切换控件缺失）：确认设计落点（header 图标 / 用户菜单项）并补充控件与 `dataset.theme` 切换逻辑。

**脚本下轮优化**:
1. 全局页测量统一改用 networkidle + 轮询稳定判据（已落地 v6b），避免冷启 skeleton 误报。
2. 导航验证改用点击穿透 + landed pathname（已落地 v6），直接判定 UI 可达性与 404。
3. 主题验证改为全 button 枚举 + 用户菜单展开扫描（已落地 v6c/v6d），彻底消除「假设存在」盲区。
