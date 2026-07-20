# 变更提案：任务列表键盘快捷键增强（Epic 32 v2.1）

## 背景
AgentBoard 任务列表已具备搜索、排序、分组、折叠、优先级/类型筛选、批量操作，以及列表内的键盘导航（j/k/方向键移动焦点、Enter 打开、空格多选、Esc 取消选择）。
但用户要从键盘**快速开始搜索**仍需手动点击搜索框。参照 GitHub / Jira 的惯例，按 `/` 应一键聚焦搜索框；在搜索框内按 `Esc` 应清空查询并失焦。这是「模仿 Jira、小步迭代」长期轨道中一个独立、低风险、纯前端的体验增强。

## 目标
在 Story 任务列表视图新增键盘快捷键：
- 非输入框聚焦状态下按 `/` → 任务搜索框获得焦点（并 `preventDefault`，避免把 `/` 当作输入）。
- 搜索输入框聚焦状态下按 `Esc` → 清空当前查询并失焦。
- 搜索框旁显示 `/` 快捷键可视提示（`<kbd>`）。

## 非目标
- 不改动后端契约 / `models.py` / `api.py` / `mcp_server.py`。
- 不改动既有 j/k/方向键/Enter/空格/Esc 列表导航逻辑。
- 不做命令面板（command palette）等大型功能。

## 范围
- 纯前端（Angular SPA）：`frontend/src/app/app.ts` + `app.html` + `app.css`。
- `app.ts`：`handleTaskKeydown` 的 `switch` 新增 `case '/'` 聚焦 `.task-search-input`。
- `app.html`：搜索 `<input>` 新增 `(keydown.escape)` 清空+失焦；搜索条新增 `<kbd class="search-kbd">/</kbd>` 提示；更新 placeholder / title。
- `app.css`：补充 `.search-kbd` 样式（主题适配）。

## 约束
- 新增前端代码 < 80 行（符合前端持续优化长期轨道纪律）。
- 不引入新框架 / 依赖。
- 与既有快捷键无冲突：`handleTaskKeydown` 在 target 为 INPUT/TEXTAREA/SELECT 时提前返回，`/` 仅在列表区（非输入框）触发聚焦；`Esc` 在输入框内清空搜索、在列表区内维持原有「取消选择」语义。

## 影响
- 仅前端静态产物；无后端、无迁移、无 API 变更。
- 无新增 `localStorage` key。

## 退出标准
- 任务列表视图按 `/`，搜索框获得焦点。
- 搜索框聚焦状态下按 `Esc`，查询被清空且输入框失焦。
- 搜索框旁显示 `/` 快捷键提示。
- 与既有列表键盘导航无冲突。
- 构建无错误；Playwright E2E 通过且控制台 / 网络零错误（仅计 .js/.css 失败；`/api` 的 ERR_ABORTED 良性忽略）。
