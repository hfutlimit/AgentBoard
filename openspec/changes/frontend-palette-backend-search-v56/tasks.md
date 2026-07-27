# Tasks: 命令面板接入后端搜索 — Epic 69 v5.6

## 实现任务（Task 1124, high）
- [x] `PaletteCommand` 接口增加 `category` 字段
- [x] 新增 `paletteSearching` / `paletteTaskResults` / `paletteProjectResults` 信号 + 防抖定时器
- [x] `onPaletteInput()` 输入处理（防抖 200ms）
- [x] `paletteRunSearch()`：后端任务搜索（`/api/tasks?q=`）+ 客户端项目过滤
- [x] `paletteItems` computed 合并（命令优先、实体补充）
- [x] `openPalette`/`closePalette` 重置搜索状态
- [x] 模板：spinner、分类标签 `.palette-item-cat`、空态「搜索中…/无匹配命令」
- [x] 样式 `styles.css`：`.palette-item-cat` / `.cat-task` / `.cat-project` / `.command-palette-spinner`（含暗色）
- [x] `npm run build` → 部署 `agentboard/web/static/`

## 验证任务
- [x] Playwright E2E `tests/test_epic69_v56_palette_search_e2e.py` 全绿
- [x] 回归 v5.4 命令面板 E2E 全绿（命令优先，Enter 执行命令不变）
- [x] 后端 `pytest tests/test_epic30_cache.py` 8 passed

## 状态流转
- Task 1124：backlog → todo → in_progress → in_review
- Story 108 / Epic 59：in_review
