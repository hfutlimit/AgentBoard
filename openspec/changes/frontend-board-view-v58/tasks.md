# Tasks: 任务列表看板视图渲染 (Epic 71 v5.8)

## 实现任务
- [x] `app.html`：任务列表区 `@if (!boardMode())` 改为 `@if/@else`，新增看板分支（列分桶 + 卡片 + 拖拽 + 折叠 + 点击打开抽屉）
- [x] `app.html`：工具栏 `filterbar__right` 在 `#densityToggle` 后新增 `#boardToggle`（列表/看板切换按钮）
- [x] `app.ts`：`handleTaskKeydown` 新增 `case 'v'` 切换 `boardMode`
- [x] `app.css`：新增看板基础布局样式（含暗色主题、列折叠态、拖拽反馈），复用既有优先级/进度/角标样式
- [x] 构建并部署静态产物（`npm run build` → `agentboard/web/static/`）
- [x] `tests/test_epic71_v58_board_view_e2e.py`：Playwright 端到端验证（7 列渲染 / 各状态列卡片 / 点击打开抽屉 / 拖拽改状态 API 复核 / 切回列表 / 0 错误）

## 验收标准
1. 列表视图下出现「看板」切换按钮；点击后渲染看板，含 7 个状态列。
2. 每列正确渲染对应状态任务卡片，列头显示任务计数。
3. 点击卡片打开快速查看抽屉；Esc 关闭。
4. 拖拽卡片到合法目标列，任务状态经 API 复核变更（非法迁移被状态机拒绝）。
5. 再次点击切换按钮回到列表视图。
6. 无 pageerror / console error / .js+.css 404。
