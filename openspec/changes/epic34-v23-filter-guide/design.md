# 设计：任务列表筛选结果引导（Epic 34 v2.3）

## 概览
延续既有「signal + computed + 模板条件渲染」模式，纯前端增量，不触碰 `visibleTasks` 的过滤逻辑（只新增一个显隐 computed 与一个复位方法）。

## 数据流
```
taskSearchQuery (signal, Epic 35)
  + filterPriorities / filterTypes / filterOnlyOverdue / labelFilter / filterMineOnly (signals)
        │
        ├─ activeFilterCount (computed)  → 既有「清除筛选」「筛选(N)」计数
        └─ showClearAll (computed, 新增) = taskSearchQuery.trim() !== '' || activeFilterCount() > 0
                │
                ├─ 工具条「✕ 清除筛选」按钮显隐条件
                └─ 筛选空状态内部「清除全部筛选」按钮显隐条件

clearAllFilters() (新增) ── 置 taskSearchQuery='' ──┐
                                                    ├─→ visibleTasks 重新计算 → 任务恢复
clearFilters() (既有) ───── 重置 chips/类型/逾期/标签/只看我 + 清持久化 ┘
```

## 关键决策
1. **`showClearAll` 仅看「搜索 + activeFilterCount」**：分组/排序视为视图偏好不计入，避免用户点「清除筛选」时意外打乱分组/排序布局。
2. **复用 `clearFilters()`**：搜索与高级面板复位逻辑统一，避免状态不一致；`clearAllFilters()` 在其基础上补一行搜索重置。
3. **空状态二分**：`@empty` 分支用 `tasks().length === 0` 判断——真无任务走 `.empty-inline`（保持原文案），被筛选隐藏走 `.filter-empty-state`（新样式 + 清除入口）。二者互斥，杜绝「筛选无匹配却显示暂无任务」的误导。

## 视图结构（app.html）
- 工具条（高级筛选按钮前）：
  ```html
  @if (showClearAll()) {
    <button class="ghost-sm clear-all-btn" (click)="clearAllFilters()" title="清除全部筛选条件">✕ 清除筛选</button>
  }
  ```
- 任务列表 `@empty`：
  ```html
  } @empty {
    @if (tasks().length === 0) {
      <div class="empty-inline">暂无任务</div>
    } @else {
      <div class="filter-empty-state">
        <div class="filter-empty-icon">🔍</div>
        <div class="filter-empty-title">没有符合当前筛选条件的任务</div>
        <div class="filter-empty-desc">尝试调整关键词、优先级或筛选条件</div>
        @if (showClearAll()) { <button class="btn-primary-sm" (click)="clearAllFilters()">清除全部筛选</button> }
      </div>
    }
  }
  ```

## 样式（app.css）
复用 `.search-empty-state` 视觉语言，`.filter-empty-state` 增加虚线边框 + 圆角卡片感；`.clear-all-btn` 采用危险色描边、hover 填充，与普通 ghost 按钮区分以提示「破坏性复位」语义。

## 验证
- 新建 `tests/test_epic34_v23_filter_guide_e2e.py`（Playwright）：登录 → 打开 story 69 → 验证初始无清除按钮 → 输入不匹配词 → 断言出现 `.filter-empty-state` 且工具条 `.clear-all-btn` 出现且行数=0 → 点工具条清除 → 行数恢复、按钮消失、搜索清空 → 再输入 → 点空状态内按钮 → 复位。全程 0 pageerror / console error / .js+.css 404。
- 回归：更新 `test_epic35_search_e2e.py` 的空状态断言（由 `.empty-inline` 改为 `.filter-empty-state`）；`pytest test_epic30_cache.py` 8 passed；E2E epic34_summary / epic35_search / epic33_mine_filter / v19_collapse_all 全绿。
