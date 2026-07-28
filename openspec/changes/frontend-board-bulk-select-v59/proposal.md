# Proposal: 看板视图批量操作（卡片多选 + 复用批量工具栏）(Epic 72 v5.9)

## 背景
v5.8 补齐了任务列表的看板视图渲染（列按状态分桶、卡片可拖拽改状态、视图切换按钮），看板功能首次真正可用。
但看板视图下仍缺少**批量操作能力**：列表视图拥有完整的批量工具栏（状态/优先级/类型/指派/截止日期/复制/删除，且状态变更状态机感知），看板视图的卡片却只能单选拖拽或点开快速查看，无法多选后批量处理。

## 目标
在看板视图的每张卡片上增加**选择复选框**，复用既有的 `toggleTaskSelection` 选择体系；勾选后自动复用列表视图的**共享批量工具栏**（`bulk-action-bar`，位于看板/列表分支之外，随 `selectedTasks().size > 0` 出现）。实现看板视图下的批量状态/优先级/类型/指派/截止/复制/删除，且批量状态变更保持状态机感知（交集合法目标）。纯前端，零后端契约变更。

## 非目标
- 不改变后端数据模型或 API 契约。
- 不新增看板特有的批量 UI（直接复用列表批量工具栏与全部既有方法）。
- 不改变看板列分组/拖拽逻辑。

## 影响范围
- `frontend/src/app/app.html`：看板卡片 `<article>` 增加 `[class.selected]` 绑定；卡片头新增 `.kanban-card-check` 复选框（stopPropagation 防止触发快速查看）。
- `frontend/src/app/app.css`：新增 `.kanban-card-check` 与 `.kanban-card.selected`（含暗色），复用品牌色变量。
- 无 `app.ts` 逻辑改动（`toggleTaskSelection` / `bulkUpdate*` / `bulkLegalStatuses` 均已具备）。
- 构建产物 `agentboard/web/static/` 同步更新（web 8090 即时生效）。
