# Design: 筛选预设默认加载自动应用 (v4.6)

## 关键决策
1. **复用既有逻辑**：直接调用已存在的 `applyDefaultPreset()`（内部 `applyFilterPreset(default.id)`），不新增应用/收窄逻辑，避免重复实现。
2. **钩子位置**：`ngOnInit()` 末尾、`void this.loadRoute()` 之后。此时所有 filter 信号已完成字段初始化，`tasks()` 尚未（或正在）加载；应用预设仅写入信号，待任务到达时由 `visibleTasks()` 自动收窄，无竞态。
3. **幂等保护**：`private defaultPresetApplied = false`，首次调用后置 `true`。`ngOnInit` 仅执行一次，但保留标志以防未来被多处调用导致重复覆盖手动筛选。

## 数据流
```
localStorage.agentboard_filter_presets
  → loadFilterPresets()
  → filterPresets signal
  → defaultPreset computed
  → (ngOnInit) applyDefaultPresetOnLoad()
  → applyDefaultPreset()
  → applyFilterPreset(default.id)
  → 设置 filterStatus / filterAssignees / filterDueDate / groupBy / sortKey / ...
  → visibleTasks 收窄
```

## 风险与权衡
- 自动应用默认预设会在每次加载时收窄任务列表；用户若临时清除筛选后刷新，会重新套用默认预设。这是「默认预设」的预期语义，非缺陷。
- 预设为全局（非按 story 维度），自动应用作用于全局筛选信号，进入任意 story 视图均生效。
- 与登录流程无关：即使未登录（显示登录页），信号仍会被设置，登录进入后即时生效。
