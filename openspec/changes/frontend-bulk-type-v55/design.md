# Design: 任务列表批量修改类型（v5.5）

## 方案选择
- **方案 A（推荐，采用）**：纯前端循环 `api.updateTask(id, {type})`，与既有 `bulkDuplicate`（逐任务 `createTask`）模式对称，零后端变更。
- **方案 B**：后端 `BulkTaskUpdate` 新增 `type` 字段 + handler 分支（`/api/tasks/bulk-update` 转发）。需改动后端并重启服务，风险更高；且 `updateTask` 单任务端点已稳定支持 `type`，逐任务循环在 3~10 条批量下性能完全可接受。

## 数据流
1. 用户在任务列表勾选 N 个任务 → `selectedTasks()` 集合填充。
2. 点击「批量修改类型」→ `showBulkActionPanel('type')` 展开类型面板。
3. 点击某类型按钮（如「Bug」）→ `bulkUpdateType('bug')`：
   - 遍历 `selectedTasks()`，逐任务 `await api.updateTask(id, {type:'bug'})`；
   - 每次成功后用 `tasks.update` 局部刷新该行 `type`（即时 UI 反馈）；
   - `bulkProgress` 实时进度；失败收集后统一 toast 提示；
   - `finally` 中 `clearTaskSelection()` + `refresh()` 全量同步。
4. 类型按钮配色复用 `.status-btn` 基础 + `.type--{t}` 三档语义色，含 dark 主题。

## 关键约束
- 复用既有 `typeLabel(t)` 渲染按钮文案（Task / Bug / Test Execution），与类型图标、分组标签一致。
- 不引入新 CSS 变量、不改动任何后端文件；`angular.json` 仅上调样式 budget 上限。
- 状态流转：Task 1123 经 `backlog→todo→in_progress→in_review` 合法链置 in_review；Story 107 / Epic 58 同步 in_review。
