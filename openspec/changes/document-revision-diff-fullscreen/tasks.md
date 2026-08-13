# Tasks：文档 Revision + Diff + Fullscreen Workspace

## 后端

- [x] `agentboard/domains/documents/models.py` 新增 `DocumentRevision`（含联合唯一 + 4 索引）
- [x] `agentboard/models.py` re-export + `__all__` 加 `DocumentRevision`
- [x] `migrations/versions/g6h7i8j9k0l1_add_document_revisions.py` 新表 + 4 索引
- [x] `agentboard/service.py` `RevisionConflict` 异常（带 expected / current）
- [x] `agentboard/service.py` `_next_revision_number(s, document_id)` 私有助手
- [x] `agentboard/service.py` `create_document` 改造：同步生成 r1 + 填 current_revision 指针
- [x] `agentboard/service.py` `create_revision` / `list_revisions` / `get_revision` / `restore_revision` / `save_document_with_revision`
- [x] `agentboard/api.py` `GET /api/documents/{id}/revisions`（分页）
- [x] `agentboard/api.py` `GET /api/documents/{id}/revisions/{n}`
- [x] `agentboard/api.py` `POST /api/documents/{id}/revisions`（带 expected_revision_number + 409 映射）
- [x] `agentboard/api.py` `POST /api/documents/{id}/revisions/restore`
- [x] `agentboard/mcp_server.py` `list_document_revisions` / `get_document_revision` / `save_document_with_revision` / `restore_document_revision` 4 个 MCP 工具

## 测试（后端）

- [x] `tests/test_doc_revisions.py` 12 case：
  - 创建文档 → r1 + current_revision 指针
  - 多次 save → revision_number 单调递增
  - 乐观锁：expected 不匹配 → RevisionConflict
  - 空保存不消耗 revision_number
  - list_revisions 倒序分页 / get_revision / 404
  - restore：复制为新 revision、is_restore=True、restored_from_revision=N、历史保留
  - restore 强制 change_note（空 → InvalidValue）
  - restore 源不存在 → 404
  - 头元数据走原 update_document，不影响 revision
  - RevisionConflict 携带 expected/current 字段

## 前端

- [x] `frontend/src/app/models.ts` `DocumentItem` 加 `current_revision_id` / `current_revision_number`；新增 `DocumentRevisionItem`
- [x] `frontend/src/app/api.service.ts` 新增 4 方法：`listDocumentRevisions` / `getDocumentRevision` / `saveDocumentRevision` / `restoreDocumentRevision`
- [x] `frontend/src/app/shared/utils/revision-diff.ts` 移植自 KV 的 Hirschberg LCS（行 + 词级，0 依赖）
- [x] `frontend/src/app/app.ts` 新增 8 signal：`docRevisions` / `docRevisionsLoading` / `docDetailTab` / `docChangeNote` / `docRevisionConflict` / `docDiffLeft` / `docDiffRight` / `docFullscreenOpen` / `docFullscreenTheme` / `docDiffData`
- [x] `frontend/src/app/app.ts` 新增 8 方法 + HostListener(escape)：
  - `setDocDetailTab` / `loadDocRevisions` / `saveDocContentWithRevision` / `acceptCurrentAndReload`
  - `setDocDiffSide` / `openRevisionDiff` / `restoreRevision`
  - `openDocFullscreen` / `closeDocFullscreen` / `toggleDocFullscreenTheme`
- [x] `frontend/src/app/app.ts` `submitDocModal` 改造：内容/标题变更走乐观锁 revision 路径（必填 change_note）+ 409 处理；元数据走原路径
- [x] `frontend/src/app/app.html` 详情 header 加 `r{N}` badge + `📄 内容` / `🕘 历史` / `⛶ 全屏` 三个按钮
- [x] `frontend/src/app/app.html` 历史 tab 渲染（每行：rN + 标签 + change_note + 作者/时间 + 左/右选择 + 回滚）
- [x] `frontend/src/app/app.html` 409 冲突卡（接受最新 / 忽略）
- [x] `frontend/src/app/app.html` Fullscreen overlay（顶栏 + body 锁滚动 + Esc 关闭 + 暗/亮主题）
- [x] `frontend/src/app/app.html` Diff dialog（行级左右双列 + 词级红绿高亮 + 折叠段 + +/Δ 统计）
- [x] `frontend/src/app/app.html` 编辑模态加 `change_note` 必填项（仅内容/标题变更时显示）
- [x] `frontend/src/app/app.css` 新增 ~190 行：revision list / diff table / fullscreen overlay 样式

## 测试（前端）

- [x] `tests/test_epic139_revision_diff_fullscreen_e2e.py` Playwright：
  - 详情页 r3 badge 正确
  - 历史 tab 列出全部 revision 倒序
  - 选 2 份 revision → diff 弹窗 +/Δ 统计 + 行/词高亮
  - 「回滚到此」→ 形成新 r4 + 标 is_restore
  - 409 冲突：客户端基于 r1、服务端已 r2 → 弹冲突卡
  - Fullscreen 入口 → 暗/亮切换 → Esc 退出
  - 0 console error / pageerror

## OpenSpec 文档

- [x] `openspec/changes/document-revision-diff-fullscreen/proposal.md`
- [x] `openspec/changes/document-revision-diff-fullscreen/design.md`
- [x] `openspec/changes/document-revision-diff-fullscreen/tasks.md`（本文件）

## 验证

- [x] `pytest tests/test_doc_revisions.py tests/test_doc_filters.py tests/test_document_links.py -q` → 31 passed
- [x] `cd frontend && npm run build` → bundle 生成成功（main-OEM74ABQ.js）
- [x] `web/static/` 已部署新 main-*.js + styles-*.css
- [ ] `python tests/test_epic139_revision_diff_fullscreen_e2e.py`（需 Docker 栈 28080/18000 在跑）→ 计划后续在 staging 跑

## 已知遗留（独立 PR）

- [ ] 列表视图（Phase A）加 rN 列
- [ ] 409 冲突手动合并视图
- [ ] `update_document` 旧路径弃用（强制走 `save_document_with_revision`）
