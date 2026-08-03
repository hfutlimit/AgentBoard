# Tasks: Epic 15 文档模块整体验收与状态同步

## Task 1: MCP 文档工具全链路验收 ✅

- [x] create_document（项目 3，type=plan，含 mermaid）→ 201
- [x] set_document_status draft→in_review
- [x] add_document_comment（testadmin）
- [x] list_document_comments（按时间正序）
- [x] search_documents（q 匹配）
- [x] delete_document（级联清理）

## Task 2: 前端文档 UI Playwright E2E（新增回归资产）✅

- [x] `tests/test_epic15_doc_module_e2e.py` 编写（15 项断言，自包含 + 可配端点）
- [x] 项目文档 Tab 渲染 / 新建按钮 / 筛选 / 搜索
- [x] 预建文档列表 + markdown（h1/加粗）+ mermaid（SVG）
- [x] 新建弹窗 → UI 创建 → 列表即时出现
- [x] 评论区发帖（form.requestSubmit）
- [x] 0 console error / 0 pageerror / 0 failed js-css → **15/15 PASS**

## Task 3: 后端回归 ✅

- [x] test_domain_boundaries + test_admin_api_key_scope + test_epic96_p0_proposals + test_epic30_cache + test_story_151_notifications + test_story_152_favorites + test_scheduler = **39 passed / 1 skipped / 0 failed**

## Task 4: MCP 状态同步（生产权威源）✅

- [x] Story 45-53（S1-S9）：backlog → todo → in_progress → done（逐级）
- [x] Task 707（web 资源契约测试）：in_review → done
- [x] **Task 964（新建 highest）：验收与状态同步 → in_review**
- [x] Epic 15：backlog → todo → in_progress → done

## Task 5: 收尾 ✅

- [x] OpenSpec change 三件套（proposal/design/tasks）落盘
- [x] git add 本次文件 → commit → push origin main
- [x] 删除 .workbuddy/autodev.lock
- [x] 工作日志 .workbuddy/memory/2026-08-04.md 追加
