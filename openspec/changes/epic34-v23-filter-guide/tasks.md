# 任务：任务列表筛选结果引导（Epic 34 v2.3）

## Task 719（high）：任务列表筛选空结果空状态 + 清除全部筛选按钮
- [x] 新增 `showClearAll` computed（搜索非空 或 任一筛选活跃）
- [x] 新增 `clearAllFilters()` 方法（重置搜索 + 复用 `clearFilters()`）
- [x] 工具条新增「✕ 清除筛选」按钮（`.clear-all-btn`，`showClearAll()` 控制显隐）
- [x] 任务列表 `@empty` 分支二分：`.empty-inline`（无任务）/ `.filter-empty-state`（筛选无匹配，内置清除入口）
- [x] 新增 CSS：`.filter-empty-state` / `.filter-empty-icon` / `.filter-empty-title` / `.filter-empty-desc` / `.clear-all-btn`
- [x] 构建 `npm run build` → cp 至 `agentboard/web/static/`
- [x] Playwright e2e `test_epic34_v23_filter_guide_e2e.py` 全绿（0 错误）
- [x] 回归：更新 `test_epic35_search_e2e.py` 空状态断言；pytest + 4 个 E2E 全绿

## 验收
- 应用任一筛选后工具条出现「清除全部筛选」
- 点击后所有筛选复位、任务恢复显示、搜索清空、按钮消失
- 零结果筛选显示 `.filter-empty-state`（非「暂无任务」），内部清除按钮可复位
- 无 JS 报错 / 控制台错误 / .js+.css 404
- Playwright e2e 覆盖上述路径
