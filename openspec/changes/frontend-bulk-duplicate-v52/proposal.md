# Proposal: 任务列表批量复制选中任务（克隆）(v5.2 / Epic 65)

## 问题
任务列表每行已有「复制任务」按钮（`duplicateTask`），可将单个任务克隆到其所属 Story（标题追加 `(副本)`）。但批量操作栏仅有「状态 / 优先级 / 指派 / 截止日期 / 删除」五种操作，**缺少等价的「批量复制」**，多选任务后无法一次性克隆，需逐个点击，效率低。

## 目标
在批量操作栏「批量改截止日期」之后新增「批量复制」按钮：
- 点击即对 `selectedTasks()` 中每个任务调用 `api.createTask` 克隆到其各自 Story（标题追加 `(副本)`，保留 type/priority/description/labels）
- 显示批量进度（`bulkProgress`）
- 完成后清空选择、`refresh()` 刷新列表并 toast 提示复制数量
- 与单行 `duplicateTask` 行为对称，纯前端零后端契约变更

## 非目标
- 不新增后端端点（复用既有 `POST /api/stories/{sid}/tasks`）
- 不做跨 Story 指定目标（克隆到各自原 Story，与单行复制一致）
- 不改动既有 5 种批量操作

## 风险
低。完全复用现有 `api.createTask` 与 `bulkProgress`/`notify`/`refresh` 基础设施；无后端契约变更，不影响其他批量操作。唯一注意点：克隆后任务数增加，需 `refresh()` 让列表与汇总同步。
