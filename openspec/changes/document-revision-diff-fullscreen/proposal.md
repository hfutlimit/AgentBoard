# 变更提案：文档 Revision + Diff + Fullscreen Workspace

## 背景

AgentBoard 的项目文档（`Document`）当前是**直接覆盖**式保存：每次 PATCH 都把 `title` / `content`
改掉，**没有历史**。导致：

1. **没有 diff 能力**：用户提了修改后看不到自己改了什么、与上一版差在哪。
2. **没有回滚能力**：编辑错了无法回到历史版本。
3. **没有审计**：无法知道"这版是谁、什么时候、改了什么"。
4. **没有沉浸阅读**：详情页与主导航 / 侧边栏 / 其它 tab 共用同一布局，**没有全屏 Workspace**。
5. **没有冲突保护**：两个用户同时编辑，后保存的会静默覆盖前一个的改动。

参考：KnowledgeVault 的 `KnowledgeItemRevision`（不可变快照 + expected_revision_number 乐观锁）
+ `revision-diff.ts`（Hirschberg LCS，0 依赖）+ `fullscreen-document-workspace`（暗色沉浸 + Esc 退出）。

## 目标

1. 每次内容/标题保存形成**不可变 revision**（不可改、不可物理删除；Document 头加 current_revision_id 指针）。
2. **乐观锁**：保存带 `expected_revision_number`，不匹配 → `409 revision_conflict`。
3. **diff**：选两份 revision → 行级 + 词级 diff（红绿高亮 + 折叠未变化段）。
4. **回滚**：旧版内容**复制为新 revision**（不修改历史，标 `is_restore=True`）。
5. **Fullscreen Workspace**：沉浸阅读 + 暗/亮主题切换 + Esc 退出。

## 非目标

- 不引入 CRDT / 实时协同（保留单写者 + 乐观锁即可）。
- 不做"协作者光标 / 选区"实时显示。
- 不把"批注 / 建议"挂到 revision（留给后续 Epic）。
- 不重构 Document 编辑模态（仅在保存路径上分流：内容变更 → revision；元数据变更 → 原路径）。

## 范围

- **后端**：
  - `agentboard/domains/documents/models.py` 新增 `DocumentRevision` 表（id / document_id / revision_number / title / content / author_id / author / change_note / is_restore / restored_from_revision / created_at；联合唯一 `(document_id, revision_number)`）
  - `agentboard/models.py` re-export + `__all__`
  - `migrations/versions/g6h7i8j9k0l1_add_document_revisions.py`（新增表 + 4 索引）
  - `agentboard/service.py`：
    - `RevisionConflict` 异常（携带 expected / current）
    - `_next_revision_number` 私有
    - `create_document` 改造（创建时同步生成 r1，填 current_revision_id/number）
    - `create_revision` / `list_revisions` / `get_revision` / `restore_revision` / `save_document_with_revision`
  - `agentboard/api.py`：
    - `GET /api/documents/{id}/revisions`（分页）
    - `GET /api/documents/{id}/revisions/{n}`
    - `POST /api/documents/{id}/revisions`（带 expected_revision_number，409 → `{code: revision_conflict, expected, current}`）
    - `POST /api/documents/{id}/revisions/restore`
  - `agentboard/mcp_server.py`：`list_document_revisions` / `get_document_revision` / `save_document_with_revision` / `restore_document_revision` 4 个 MCP 工具
- **前端**：
  - `models.ts` `DocumentItem` 加 `current_revision_id` / `current_revision_number`；新增 `DocumentRevisionItem`
  - `api.service.ts` 新增 `listDocumentRevisions` / `getDocumentRevision` / `saveDocumentRevision` / `restoreDocumentRevision`；`listDocuments` 签名不变
  - `shared/utils/revision-diff.ts`（从 KV 移植的 Hirschberg LCS 实现，行级 + 词级 diff + 折叠未变化段，0 依赖）
  - `app.ts` 新增 8 signal + 8 方法：setDocDetailTab / loadDocRevisions / saveDocContentWithRevision / acceptCurrentAndReload / setDocDiffSide / openRevisionDiff / restoreRevision / openDocFullscreen / closeDocFullscreen / toggleDocFullscreenTheme + HostListener(window:keydown.escape)
  - `submitDocModal` 改造：内容/标题变更走 `saveDocumentRevision` 乐观锁路径（必填 change_note）；纯元数据变更走原 `updateDocument` 路径
  - `app.html`：
    - 详情 header 加 `r{current}` badge + `📄 内容` / `🕘 历史` / `⛶ 全屏` 三个按钮
    - 历史 tab 渲染 revision 列表（每行：rN + 标签 + change_note + 作者/时间 + 左/右选择 + 回滚）
    - 409 冲突卡片（接受最新 / 忽略）
    - Fullscreen overlay（顶栏带主题切换 / 退出；body 锁滚动；Esc 关闭）
    - Diff dialog（行级左右双列 + 词级红绿高亮 + 折叠段 + +/Δ 统计）
    - 编辑模态加 `change_note` 必填项（仅内容/标题变更时）
  - `app.css` 新增 ~190 行（revision list / diff table / fullscreen overlay 样式）

## 影响

- DB 新增 1 张表（4 索引），Alembic 自动 upgrade head；向下兼容（Document 头字段全保留，新功能走新端点）。
- 旧 `update_document` 路径仍兼容：UI 层在"内容未变"时仍走它，纯头元数据变更（type/status/epic/story）不计 revision。
- 前端 main bundle 906kB → 911kB（+5kB，diff 算法 + 新组件）；CSS 145kB → 162kB（+17kB）。
- MCP 工具集新增 4 个。
- 不影响任务 `spec`、Epic / Story / Task 的现有契约。

## 退出标准

1. `pytest tests/test_doc_revisions.py -q` → 12 passed。
2. `pytest tests/test_doc_revisions.py tests/test_doc_filters.py tests/test_document_links.py -q` → 31 passed。
3. `cd frontend && npm run build` 成功生成新 bundle（main-OEM74ABQ.js）。
4. `web/static/` 已部署新 bundle；`index.html` 指向新 hash。
5. `python tests/test_epic139_revision_diff_fullscreen_e2e.py`（需 Docker 栈 28080/18000）：
   - 详情页 `r{N}` badge 正确
   - 历史 tab 列出全部 revision 倒序
   - 选 2 份 revision → diff 弹窗含 +/Δ 统计 + 行/词高亮
   - 「回滚到此」→ 形成新 revision + 标 `is_restore=True`
   - 409 冲突：客户端基于 r1、服务端已 r2 → 弹版本冲突卡
   - Fullscreen 入口 → 暗/亮切换 → Esc 退出
   - 0 console error / pageerror
6. Git: feature commit + push `origin main`。
