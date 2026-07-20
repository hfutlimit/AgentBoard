# 任务清单：任务列表键盘快捷键增强

## 实现任务
- [x] T1 `app.ts`：`handleTaskKeydown` 的 `switch` 新增 `case '/'` 聚焦 `.task-search-input` 并 `preventDefault()`
- [x] T2 `app.html`：搜索 `<input>` 新增 `(keydown.escape)` 清空查询并失焦
- [x] T3 `app.html`：`.task-search-bar` 新增 `<kbd class="search-kbd">/</kbd>` 提示；更新 placeholder / title
- [x] T4 `app.css`：补充 `.search-kbd` 样式（主题适配）

## 验证任务
- [x] V1 `npm run build` 通过（无 budget / 编译错误）
- [x] V2 启动 web 服务，Playwright 访问 Story 任务列表，按 `/` 确认搜索框聚焦
- [x] V3 Playwright 在搜索框输入后按 `Esc`，确认查询清空且失焦
- [x] V4 控制台 / 网络零错误（仅计 .js/.css 失败；`/api` ERR_ABORTED 良性忽略）
- [x] V5 新增 `tests/test_epic32_tasklist_hotkeys_e2e.py` 并通过
- [x] V6 运行既有回归（Epic 34 汇总栏 / Epic 35 搜索 / Epic 36 内联编辑 / v1.9 折叠 / Epic 31 chips / `test_epic30_cache.py`）确保无回归

## 关联 MCP
- Epic 32 (id=67) → in_progress
- Story 68 (id=68) → in_progress
- Task 717 (id=717) → in_review（验收通过后）
