# AGB 全站前端巡检 · 第 29 次运行报告（Story 348）

- **时间**：2026-08-21 09:36（hourly 自动触发）
- **使命**：对 AgentBoard 新版前端全页面/功能做 Playwright 端到端巡检，问题以 Bug 上报至 Story 348（AGB 项目，project_id=3）
- **结论**：**本轮新建 Bug 0 条**；3 个已知 Bug（#1428 / #1430 / #1431）稳定复现，1 个（#1427）已修复待关闭，1 个（#1429）已修复关闭

## 环境
- 本地 `ng serve`（127.0.0.1:4200，编译 18.0s） + 代理生产后端 `http://124.220.44.12`
- Playwright Chromium 1440×900，admin/admin123 登录
- 已清理：巡检结束后强杀 ng serve，端口 4200 释放

## 巡检范围
| 类别 | 覆盖 |
|---|---|
| 全局路由 | /、/projects、/epics、/stories、/tasks、/bugs、/documents、/dashboard、/settings、/agents、/proposals、/notifications、/admin（13） |
| 工作区 tab | /project/3/{overview,kanban,epics,backlog,proposals,documents,members,settings}（8） |
| 详情页 | /story/348、/story/330、/task/1342、/task/1339、/epic/152（5） |
| 交互 | 顶层导航点击穿透（6 锚点）、主题切换枚举、新建弹窗、文档搜索、评论框 |
| 扩展（本轮新增） | 响应式 1440/1280/1024 水平溢出、工作项列表、暗色主题 DOM 探测 |

## v6 初判 9 条 finding 复核（全部非新问题）
- **7 × P1「主内容 0 字符」**（projects/epics/tasks/bugs/documents/dashboard/settings）→ 暖机复核：projects/documents/settings 正常渲染（txt=1304/1304/650）；epics/tasks/bugs/dashboard = 稳定 404（即已知 Bug #1430）。**冷启 lazy chunk 时序误报，非新回归。**
- **1 × P2 主题缺失** → 已知 **#1431**（header 全 button 枚举候选=0、`dataset.theme` 不翻转、`has_dark_class=false`）
- **1 × P3 新建弹窗未打开** → 选择器时机 artifact（v10 已确认 `modal modal-create` 正常打开含表单字段）

## 已知 Bug 复验（真实测量）
| Bug | 状态 | 本轮证据 |
|---|---|---|
| #1427 详情页空白（high） | ✅ 已修复 | v11：story_330/task_1342/task_1339 `still_blank=False`，正常渲染；系统仍 `todo`，建议开发复核关闭 |
| #1428 全局 /documents、/proposals 误渲染为项目中心（medium） | 🔴 STILL | 两个路由 h1="项目中心 11" |
| #1429 侧栏「搜索」误标（medium） | ✅ 已修复 | status=`done`；侧栏含「提案」无「搜索」 |
| #1430 全局路由 /epics /stories /tasks /bugs /dashboard 404（medium） | 🔴 STILL | 5 路由稳定 404 |
| #1431 暗色/亮色主题切换缺失（medium） | 🔴 STILL | 全 button 枚举无主题控件、无 dark class |

## 扩展检查（本轮新增，无新 Bug）
- **响应式**：1440/1280/1024 三档下 home/projects/overview 水平溢出均 = 0px，无溢出/重叠
- **工作项列表** `/project/3/backlog`：渲染 **200 条**工作项，含「搜索当前项目」输入 + 8 个分页器元素，列表无回归
- **暗色主题 DOM 探测**：`documentElement`/`body` 无 dark class，`--navy:#10243e` 令牌存在但无 light/dark 切换机制（佐证 #1431）

## 真实质量指标
- 路由可达：**26/26**（其中 5 个为 #1430 设计的 404）
- 真实 console error：**0**（3 条全为 401 / WS 代理 artifact）
- page error：**0**
- 瞬态 API 失败：1 条 `/api/agents` 401（artifact，重试即 200）
- 水平溢出：**0px**

## 产物（仓库 `tests/e2e_story348/`，截图 gitignored）
- `inspect_all_v6.py` + `report_story348.json` — 26 路由 + 交互 + 已知复验
- `inspect_v11_recheck.py` + `report_v11_recheck.json` — 暖机复核 + 扩展检查
- `inspect_v12_worklist.py` + `report_v12_worklist.json` — 工作项列表专项
- 截图 `screenshots_v6/`、`screenshots_v11/`、`screenshots_v12/`

## MCP 闭环
- Story 348 新增巡检摘要评论 **#790**（author=AGB-AutoInspector，使用 `add_story_comment`）
- 未新建 Bug、未关闭任何任务/Bug

## 下一轮
- 继续每小时巡检；监控 #1428 / #1430 / #1431 修复；建议开发复核关闭 #1427
- 提示：`add_story_comment` 才是 Story 评论入口（`add_comment` 对 story 报 "task not found"）；冷启 0 字符必走暖机复核；工作项列表正确路由 `/project/3/backlog`
