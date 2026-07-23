# Design: 批量状态变更状态机感知 (v3.5 / Epic 48)

## 现有可复用资产
- `statusTransitions: Record<string,string[]>`（`app.ts:2990`）—— v3.4 镜像后端 `TRANSITIONS`，点对点一致
- `selectedTasks = signal<Set<number>>` —— 批量选择状态
- `tasks = signal<Task[]>` —— 当前 story 任务，选中项均在其内
- `statusLabel()`（`app.ts:2956`）—— 状态→中文标签
- `bulkUpdateStatus(newStatus)`（`app.ts:2068`）—— 对选中项批量置状态（不变）
- 模板 `bulk-action-bar` / `.bulk-panel`（`app.html:1259` 起）

## 方案
1. **交集计算**：新增 `readonly bulkLegalStatuses = computed<string[]>`：
   - 选中为空或命中任务为空 → 返回 `[]`
   - 否则对所选每个任务取 `statusTransitions[t.status] || []`，做**逐任务交集**（首个初始化，后续 `filter` 保留共同项）
   - 返回最终交集（无共同项则为 `[]`）
2. **模板分支**（`app.html` bulk status 面板）：
   - `@if (bulkLegalStatuses().length > 0)` → 遍历 `bulkLegalStatuses()` 渲染状态按钮（复用既有 `.status-btn.badge.status--{s}` + `statusLabel`）
   - `@else` → 渲染 `.muted` 提示「所选任务状态无共同可流转目标（受状态机限制）」
   - 取消按钮始终保留
3. **调用不变**：点击合法状态按钮仍走 `bulkUpdateStatus(status)`（交集保证对所有选中任务均合法）。

## 关键决策
- **交集而非并集**：并集会把某些任务的非法目标暴露出来，点选后后端 400（部分失败）。交集保证「点哪个按钮，所有选中任务都能合法流转」，符合 premium 预期。
- **空态提示**：交集为空时明确告知原因（状态机限制），而非静默无按钮，避免用户疑惑。

## 验证
- Playwright `test_epic48_v35_bulk_status_fsm_e2e.py`：在隔离 story 186 建 4 个受控状态任务，断言
  - 选 `todo+todo+in_progress` → 仅 1 个按钮「完成」
  - 选 `backlog+todo` → 0 按钮 + 空态提示
  - 0 pageerror / console error / .js+.css 404
- 回归：`pytest test_epic30_cache.py`（7 passed/1 skipped）；E2E v3.4 行内状态切换全绿（无回归）
