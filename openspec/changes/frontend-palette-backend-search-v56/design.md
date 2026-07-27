# Design: 命令面板接入后端搜索 — Epic 69 v5.6

## 信号与数据流
- 新增信号：`paletteSearching`、`paletteTaskResults: PaletteCommand[]`、`paletteProjectResults: PaletteCommand[]`，及 `paletteDebounceTimer` 防抖定时器。
- `PaletteCommand` 接口扩展 `category?: 'command' | 'task' | 'project'`，用于模板渲染分类标签。

## 交互流程
1. 输入 `(input)="onPaletteInput(value)"` → 写入 `paletteQuery()` 并启动 200ms 防抖。
2. 防抖触发 `paletteRunSearch(q)`：
   - `q.length < 2`：清空结果、停止搜索指示。
   - 否则置 `paletteSearching(true)`：
     - **项目**（同步）：从 `projects()`（回退 `recentProjects()`）按 `name + key` 包含过滤，取前 8。
     - **任务**（异步）：`firstValueFrom(api.searchTasks({ q, limit: 10 }))` → 映射为 `任务 #{id}：标题` + hint（`projectName` + status），`.finally` 置 `paletteSearching(false)`。
3. `paletteItems` computed 合并：有静态命令命中时命令优先、实体结果补充其后；无命令命中时直接展示实体结果。

## 模板与样式
- 输入框旁新增 `.command-palette-spinner`（搜索中转圈）。
- 列表项按 `cmd.category` 渲染左侧 `.palette-item-cat`（`.cat-task` 蓝 / `.cat-project` 紫），active 态反白。
- 空态：`paletteSearching()` 时显示「搜索中…」，否则「无匹配命令」。
- 样式写入 `frontend/src/styles.css`（含暗色自适应，复用 `--accent`/`--surface-2` 等变量）。

## 验证
- Playwright E2E：`tests/test_epic69_v56_palette_search_e2e.py` 覆盖任务搜索跳转、项目搜索跳转、无匹配空态、0 错误。
- 回归：`tests/test_epic67_v54_command_palette_e2e.py`（命令优先，Enter 仍执行命令）。
