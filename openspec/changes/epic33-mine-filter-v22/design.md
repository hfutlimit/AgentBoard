# 设计：任务列表「只看指派给我」快速筛选

## 数据流
```
currentUser()  ──┐
                  ├──► myUserId(): members().find(username==currentUser)?.user_id
members()       ──┘

visibleTasks computed (现有过滤链末尾追加):
  if (filterMineOnly()) {
    const myId = myUserId();
    if (myId != null && members().length > 0) {
      if (t.assignee_id !== myId) return false;   // 仅保留指派给我的
    }
  }
```

## 状态与持久化
- `filterMineOnly: signal<boolean>` 初始值来自 `localStorage.agentboard_filter_mine === '1'`。
- `toggleFilterMine()` 翻转并写回 `localStorage`。
- 与既有筛选（优先级 chips / 搜索 / 分组 / 高级面板）共存，过滤取交集。

## 成员映射的健壮性
- `myUserId()` 依赖 `members()` 已加载。Story 视图进入时已 `loadMembers(epic.project_id)`，成员可用。
- 守卫：仅当 `members().length > 0` 且 `myId != null` 才过滤；否则视为「无操作」，不隐藏任何任务，避免 Dashboard / 未加载成员场景误伤。

## UI 位置
- 工具条 `task-quickfilter-bar` 之后、`task-sort-bar` 之前新增「只看我」切换按钮。
- 样式 `.mine-toggle` 复用 `.qf-chip` 视觉语言（圆角胶囊、active 高亮、hover 上浮），保持一致的设计语言。

## 与既有清除逻辑联动
- `activeFilterCount` computed 追加 ` + (filterMineOnly() ? 1 : 0)`，使工具条「⚙ 筛选(N)」计数与「清除筛选」按钮随本筛选联动。
- `clearFilters()` 追加 `filterMineOnly.set(false)` 并清理 `localStorage`。

## 回归面
- 现有 e2e（Epic 31/32/34/35/36/v1.9）不应受影响：默认 `filterMineOnly=false`，不注入过滤。
- 排序 / 分组 / 搜索 / 批量选择仍基于 `visibleTasks`，叠加本过滤后行为一致。
