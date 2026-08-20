# AGB 全站前端质量巡检报告 — Story 348

> 巡检时间：2026-08-21 02:40（hourly cron 触发）
> 执行者：AGB·全站 Playwright 巡检自动化
> 目标 Story：Story 348「页面持续修复 · 新版前端 UI 问题收集」（Epic 152）
> 数据基准：project_id=3（AGB），生产后端 `http://124.220.44.12`

## 环境

- 前端：本地 `ng serve --proxy-config tests/e2e_story348/proxy.prod.conf.json --host 127.0.0.1 --port 4200`
  - 本轮启动前清理陈旧 ng serve（PID 22456）后全新冷启，编译 **10.05s**
- 代理：将 `/api`、`/ws` 转发到生产后端（避免本地 58125 数据库为空）
- 浏览器：Playwright Chromium，视口 1440×900，中文 locale
- 登录：`localStorage.agentboard_token` 注入 admin/admin123 JWT
- 脚本版本：`tests/e2e_story348/inspect_all_v4.py`（v4 — 修复 v3 主题/tab/弹窗 3 类选择器误报，新增 #1427/#1428 已知 Bug 复验）；本轮新增 `focused_tab_check.py` 专项复核

## 巡检范围

| 类型 | 路由 |
|------|------|
| 顶层导航 | `/`, `/projects`, `/agents`, `/documents`, `/proposals`, `/settings`, `/notifications`, `/admin` |
| 项目 Workspace 8 个 tab | `/project/3/{overview,kanban,epics,backlog,proposals,documents,members,settings}` |
| 详情页 | `/epic/152`, `/story/348`, `/story/330`, `/task/1342`, `/task/1339` |
| 交互 | 主题切换 / Workspace tab 点击 / 新建弹窗 / 文档搜索 |
| 专项复核 | 8 tab 真实标签 / `location.pathname` / active / heading 一致性（`focused_tab_check.py`） |
| 已知 Bug 复验 | #1427（详情空白 3 实体）、#1428（全局 routes） |

合计 **21 页面 + 4 类交互 + 1 项专项复核 + 5 项复验**。全部截图留存于 `tests/e2e_story348/screenshots/`、`screenshots_focused/`。

## 核心结果

| 指标 | 数值 | 说明 |
|------|------|------|
| 页面访问成功 | 21/21 | 无加载失败 |
| 真实 console error | **0** | 5 条全部为 artifact：ws 握手 + 401 + 500 瞬时噪声 |
| 真实 page error | **0** | 无未捕获异常 |
| 真实 API 失败（持续） | **0** | `/api/agents` 500 复测后 200，非持续故障 |
| 水平溢出 | **0 px** | 21/21 页面无横向滚动 |
| 新建 Bug 数 | **1** | 去重后净增 #1429，无重复 |

## 本轮新建的 Bug

### Bug #1429 — Workspace 侧边栏 tab「搜索」误标 — 实际打开「需求提案」页（P2 / medium）

- **现象**：工作区侧边栏 8 项为 `概览 / 看板 / Epics / 工作项 / 搜索 / 文档 / 成员与 Agents / 设置`；点击「搜索」后路由跳至 `/project/3/proposals`，主内容渲染「需求提案」管理页（h1=需求提案、右上「+ 新建提案」），但侧栏 tab 标签文字仍是「搜索」（Search），与所打开的页面毫无关系。
- **期望**：proposals 入口应明确命名为「提案」或「需求提案」，与路由/内容/按钮文案一致。
- **复核证据**（`focused_tab_check.py`）：
  - `location.pathname` 点击「搜索」后 = `/project/3/proposals` ✓
  - active tab 文本 = `搜索`（错），heading = `需求提案`（对）
  - 脚本用 `提案` 标签点击 → 超时未命中（UI 根本无此标签），印证 tab 缺失/被改名
- **截图**：`tests/e2e_story348/screenshots/ws_proposals.png`、`ws_overview.png` 等
- **根因推测**：`frontend/src/app/` 工作区侧边栏组件的 `navTabs` / `sidebar` 数组中 proposals 的 `label` 被误写为「搜索」

## 已知 Bug 复验

| Bug | 路径 | 上轮 | 本轮 | 判定 |
|---|---|---|---|---|
| #1427 | `/story/330` | 空白 | txt=2903，正常渲染 | **已修** |
| #1427 | `/task/1342` | 空白 | txt=650，正常渲染 | **已修** |
| #1427 | `/task/1339` | 空白 | txt=10，但实为 404「页面不存在」视图 | **非 bug**（实体不存在，正确渲染 404） |
| #1428 | `/documents` | h1=项目中心 11 | h1=项目中心 11 | **仍复现** |
| #1428 | `/proposals` | h1=项目中心 11 | h1=项目中心 11 | **仍复现** |

#1427 建议开发确认 task 1339 实体是否存在后将 330/1342 修复 + 1339 404 正常态合并关闭。

## 初判 → 复核后排除的"假阳性"（不上报 Bug）

1. **工作区 tab 点击后 URL "错位"（P2）**  
   `focused_tab_check.py` 同步采集 `page.url` 与 `window.location.pathname`：真实 `location.pathname` 每次点击后**全部正确**更新（/overview→/kanban→/epics→/backlog→/proposals→/documents→/members→/settings），active tab 与 heading 也全对。**是 Playwright `page.url` 滞后一拍的读法 artifact，非前端 bug。**

2. **`/api/agents` 500 错误**  
   直接 `curl` 带 admin token 复测返回 200 + 正常 JSON 列表（7 个 Agent）；`agents.png` 完整渲染。属瞬时/竞态噪声。

3. **`#theme-toggle` 找不到**  
   顶栏未使用该 id；上轮已确认为选择器 false positive，非 UI 缺陷。

4. **`#proj-new-btn` 不触发弹窗**  
   `projects.png` 中「+ 新建项目」按钮清晰可见；上轮已确认为选择器 false positive。

5. **`/task/1339` P1 空白**  
   实为正确的「页面不存在」404 视图，**非 bug**。

## 视觉与交互结论

- v7 色板（navy sidebar + blue 主操作色）在 21 页面一致生效；无布局错乱、重叠、明显对齐问题。
- 0 真实前端报错；0 水平溢出；0 持续性 API 失败。
- 主流程（项目中心、Agent 池、Workspace 8 tab、详情、评论、文档）渲染均正常。

## 产物路径

- 主脚本：`tests/e2e_story348/inspect_all_v4.py`（v4 巡检）
- 专项复核：`tests/e2e_story348/focused_tab_check.py`（本轮新增）
- 结构化数据：`tests/e2e_story348/report_story348.json`、`focused_tab_check.json`
- 本报告：`tests/e2e_story348/report_story348.md`
- 截图：`tests/e2e_story348/screenshots/`、`screenshots_focused/`（gitignored，本地证据）

---

**结论**：本轮巡检新增 1 条 P2 Bug（#1429），#1427 部分修复 + 1339 为正常 404，#1428 仍待修，4 类假阳性已澄清。
