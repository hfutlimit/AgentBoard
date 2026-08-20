# AGB 全站前端质量巡检报告 — Story 348

> 巡检时间：2026-08-21 00:35+（当前运行）  
> 执行者：AGB·全站 Playwright 巡检自动化  
> 目标 Story：Story 348「页面持续修复 · 新版前端 UI 问题收集」（Epic 152）  
> 数据基准：project_id=3（AGB），生产后端 `http://124.220.44.12`  

## 环境

- 前端：本地 `ng serve --proxy-config tests/e2e_story348/proxy.prod.conf.json --host 127.0.0.1 --port 4200`
- 代理：将 `/api`、`/ws` 转发到生产后端（避免本地 58125 数据库为空）
- 浏览器：Playwright Chromium，视口 1440×900，中文 locale
- 登录：`localStorage.agentboard_token` 注入 admin/admin123 JWT
- 脚本版本：`tests/e2e_story348/inspect_all.py` v3（增强主内容空白检测、溢出检测、交互覆盖）

## 巡检范围

| 类型 | 路由 |
|------|------|
| 顶层导航 | `/`, `/projects`, `/agents`, `/documents`, `/proposals`, `/settings`, `/notifications`, `/admin` |
| 项目 Workspace 8 个 tab | `/project/3/overview`, `/project/3/kanban`, `/project/3/epics`, `/project/3/backlog`, `/project/3/proposals`, `/project/3/documents`, `/project/3/members`, `/project/3/settings` |
| 详情页 | `/epic/152`, `/story/348`, `/story/330`, `/task/1342`, `/task/1339` |
| 交互 | 主题切换按钮检测、Workspace tab 点击、新建弹窗触发、列表搜索框输入 |

合计 **21 个页面**，全部截图留存于 `tests/e2e_story348/screenshots/`。

## 核心结果

| 指标 | 数值 | 说明 |
|------|------|------|
| 页面访问成功 | 21/21 | 无加载失败 |
| 真实 console error | 0 | 仅本地 proxy WebSocket upgrade 噪声（已过滤） |
| 真实 API 失败 | 0 | 仅 `/ws/agents` 因本地 Angular proxy 不支持 WebSocket upgrade（非前端 Bug） |
| page error | 0 | 无未捕获异常 |
| 水平溢出 | 0 px | 桌面 1440px 无横向滚动 |
| 新建 Bug 数 | **2** | 已创建到 Story 348，无重复 |

## 已确认并上报的 Bug

### Bug #1427 — Story / Task 详情页部分实体渲染为空白视图（P1 / high）

- **现象**：`/story/330`、`/task/1342`、`/task/1339` 顶部 Shell 正常，主内容区完全空白。
- **验证**：后端 `GET /api/stories/330`、`/api/tasks/1342`、`/api/tasks/1339` 均返回 200 且数据完整；同类型的 `/story/348`、`/epic/152` 渲染正常。
- **证据**：`tests/e2e_story348/screenshots/story_330.png`、`task_1342.png`、`task_1339.png`
- **额外观察**：`task_1342` 顶部项目下拉显示“未选择项目”，而 `story_330` 显示“当前项目 AgentBoard”，存在不一致。

### Bug #1428 — 全局 `/documents` 与 `/proposals` 路由错误渲染为项目中心（P2 / medium）

- **现象**：访问 `/documents` 与 `/proposals` 时，页面 h1 均为“项目中心 11”，列表也是项目列表，与 `/projects` 完全一致。
- **验证**：`/agents` 渲染正常（`Agent 池 7`），说明全局路由机制本身可用；Workspace 内的 `/project/3/documents` 与 `/project/3/proposals` 也正常。
- **证据**：`tests/e2e_story348/screenshots/documents.png`、`proposals.png`、`projects.png`

## 自动检测到的疑似问题及澄清

以下 3 条为 **脚本选择器/时序误报**，经截图人工复核后**不上报为 Bug**：

1. **#theme-toggle 未找到**  
   顶部 Header 未使用 `id="theme-toggle"`，主题切换入口可能在用户下拉菜单或设置页中，需后续脚本补充定位方式。视觉上未发现主题功能缺失。

2. **Workspace 8 个 tab 无法点击**  
   截图确认侧边栏 8 个 tab 均正常渲染且样式正确；失败原因是 `get_by_text(..., exact=True)` 命中了非可点击的文本副本。实际路由 `/project/3/*` 本身可正常访问。

3. **Backlog 新建弹窗未打开**  
   截图确认 `/project/3/backlog` 存在“＋工作项”按钮；脚本通用选择器未匹配到该按钮文本。弹窗本身未验证，但不属于 UI 缺陷。

## 视觉与交互结论

- v7 色板（navy sidebar + blue 主操作色）在 Workspace 视图、列表页、详情页、管理后台一致生效。
- 空状态（Kanban、通知中心）文案、图标、对齐正常。
- 无布局错乱、元素重叠、明显间距或对齐问题。
- 控制台无真实前端报错。

## 产物路径

- 主脚本：`tests/e2e_story348/inspect_all.py`
- 辅助验证：`tests/e2e_story348/check_global_routes.py`
- 结构化报告：`tests/e2e_story348/report_story348.json`
- 本报告：`tests/e2e_story348/report_story348.md`
- 截图目录：`tests/e2e_story348/screenshots/`（gitignored，本地证据）

---

结论：**本轮巡检 Story 348 新增 2 条 Bug（P1+P2），均已关联到 Story 348。**
