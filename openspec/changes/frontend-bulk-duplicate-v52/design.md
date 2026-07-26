# Design: 任务列表批量复制选中任务（克隆）(v5.2 / Epic 65)

## 现状
- 单行复制：`app.ts` `duplicateTask(id)` —— 查找任务，调 `api.createTask(story_id, {project_id, title+' (副本)', type, priority, description, labels})`。
- 批量操作栏：`app.html` `.bulk-action-bar` 内含 5 个 `showBulkActionPanel(...)` 按钮；选中数量由 `selectedTaskCount` 驱动渲染。
- 批量基础设施：`bulkProgress` 信号（进度提示）、`notify()`（toast）、`clearTaskSelection()`、`refresh()`（按当前路由重载数据）。

## 方案
### 前端（纯前端）
1. `app.ts` 新增 `bulkDuplicate(): Promise<void>`：
   - 取 `Array.from(this.selectedTasks())`；空则返回。
   - 置 `bulkProgress` 进度（total = 选中数）。
   - 遍历每个 id：`tasks().find` → 调 `firstValueFrom(this.api.createTask(story_id, {...}))` 克隆；`ok++` 并更新进度。
   - `finally`：清 `bulkProgress`、`clearTaskSelection()`、`await this.refresh()`。
   - 成功 toast `已批量复制 N 个任务（副本已创建到各自 Story）`；异常 toast `批量复制失败：<msg>`（error）。
2. `app.html` 在「批量改截止日期」按钮后追加 `<button class="btn" (click)="bulkDuplicate()">批量复制</button>`。

### 数据流
```
选中任务 → 点击「批量复制」 → bulkDuplicate()
  ├─ for each id: api.createTask(story_id, {title+'(副本)', type, priority, description, labels})
  ├─ bulkProgress 进度提示
  └─ finally: clearTaskSelection() + refresh()  → 列表/汇总更新 + toast
```

## 关键决策
- **克隆到各自 Story（非指定目标）**：与单行 `duplicateTask` 语义一致，避免引入目标选择 UI，保持简单。
- **直接按钮、无子面板**：克隆无需二级选项，`bulkDuplicate()` 立即执行（区别于需选值的 status/priority/assignee/due 面板）。
- **零后端变更**：`api.createTask` 已支持，无需新增端点或字段。

## 验证
- Playwright E2E：登录 → 受控 Story 种子 3 任务 → 勾选 3 → 点「批量复制」→ 断言列表出现 3 个 `(副本)` 且 toast 提示 → 经 API 复核副本数 → 清理种子。
- 回归：既有 v5.1 批量指派、v4.x 各 E2E + `pytest test_epic30_cache.py`。
