# Design: 快速查看抽屉内联编辑标题与描述（v4.3）

## 现状（v4.2 基线）
- 抽屉 TS 逻辑：`qvTaskId/qvTask()` 从 `tasks()` 信号按 id 取任务；`openQuickView/closeQuickView` 开关。
- v4.3 半成品：标题编辑区（`.qv-title-row` 编辑按钮 + `.qv-title-edit` 输入态）已在模板落地，但**描述编辑仅展示、无编辑入口**；且编辑相关 CSS 类（`.qv-edit-btn/.qv-title-input/...`）缺失样式。

## 设计决策
1. **复用既有信号与方法**：不新增 TS 状态，直接使用 v4.2 已有的 `qvEditingDesc/qvEditDesc` 与 `startQvEditDesc/saveQvDesc/cancelQvEditDesc`，与标题编辑保持完全对称。
2. **模板切换**：描述区用 `@if (qvEditingDesc()) { 编辑态 } @else if (qt.description) { 展示 } @else { 空态 }`，与标题区 `@if/@else` 模式一致。
3. **保存路径**：`saveQvDesc()` → `firstValueFrom(api.updateTask(id,{description}))` → `tasks.update` 局部刷新 → 抽屉（读 `qvTask()`）与列表同步。`description` 与现有值相同则早退不改。
4. **样式补齐**：组件作用域 CSS（`app.css`）编译进 `main-*.js` 运行时注入，故新增 `.qv-edit-*` 类即可生效，无需改全局 `styles.css`。
5. **取消语义**：`cancelQvEdit*` 仅复位编辑信号，不改 `tasks()`，API 不变（验收标准 #4）。

## 交互细节
- 编辑按钮：标题行右侧 `✎`、描述标题右侧 `✎`，均 `stopPropagation + preventDefault` 防误触关闭抽屉。
- 键盘：`keydown.enter` 保存标题；`keydown.escape` 取消编辑（抽屉级 `document:keydown.escape` 仍负责整体关闭，行为一致）。
- 校验：标题为空或与原值相同则直接退出编辑（不调 API）。

## 风险与规避
- **PATCH 不返回（历史坑）**：v1.6 曾遇 Angular HttpClient PATCH Observable 不 emit；v4.1 已验证 `api.updateTask` PATCH 在当前环境可用，故沿用；E2E 以 API 复核确认。
- **作用域 CSS 未进产物**：构建后仍需确认 `qv-desc-input`/`qv-edit-btn` 出现在 `main-*.js`（已 grep 验证）。
