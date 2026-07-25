# Tasks: 快速查看抽屉内联编辑标题与描述（v4.3）

## 验收标准
1. 抽屉标题旁出现编辑按钮（✎），点击进入输入框
2. 描述区出现编辑按钮（✎），点击进入 textarea
3. 保存后 title/description 经 API 复核更新，且列表同步
4. 取消不生效（API 不变）

## 实现任务
- [x] TS：复用 `qvEditingDesc/qvEditDesc` 与 `startQvEditDesc/saveQvDesc/cancelQvEditDesc`（v4.2 已落地）
- [x] 模板：描述区新增编辑按钮 + textarea 编辑态（`@if/@else if/@else`）
- [x] CSS：补齐 `.qv-edit-btn/.qv-title-input/.qv-title-edit/.qv-edit-actions/.qv-desc-head/.qv-desc-edit/.qv-desc-input`（light + dark）
- [x] 构建：`npm run build` → cp `dist/frontend/browser/.` → `agentboard/web/static/`
- [x] E2E：`tests/test_epic56_v43_inline_edit_title_desc_e2e.py` 全绿（标题编辑 / 描述编辑 / 取消无副作用；0 pageerror/console/.js+.css 404）
- [x] 回归：v4.2 抽屉 E2E 通过；`pytest test_epic30_cache.py` 8 passed
- [x] 状态：Task 1055 → in_review；Story 202 / Epic 129 同步 in_review

## 追踪实体（MCP/REST 兜底，Docker API 18000 / web 28080）
- project 123 (AUTODEV56) → epic 129 (Epic 56 v4.3) → story 202 (抽屉内联编辑标题与描述) → task 1055 (high)
