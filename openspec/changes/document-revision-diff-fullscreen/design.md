# 设计：文档 Revision + Diff + Fullscreen Workspace

> 对应 Epic 139。方案到方法粒度，已实现并通过 service 单测。

## 1. 技术选型

| 维度 | 选择 | 备选 | 决策理由 |
|------|------|------|----------|
| 不可变快照存储 | `document_revisions` 表 + `documents.current_revision_id` 头指针 | 把全文塞 JSON 字段 | 关系建模最自然；头指针避免每次 list 都 join；索引友好 |
| 乐观锁实现 | 客户端提交 `expected_revision_number` + 服务端事务内比对 | 整行版本号 / etag | 与 KV 一致；用户视角最直白；服务端不需发 etag |
| 冲突响应 | HTTP 409 + `{code: revision_conflict, expected, current}` | 200 + 携带 current_revision | 409 语义最清晰；前端可弹"接受最新"按钮 |
| 头部元数据（type/status/folder/epic/story）是否进 revision | **不进**（走原 `update_document`） | 全进 | 与 KV 决策一致；元数据变更不算"内容变更"；不污染 history |
| Diff 算法 | 前端 Hirschberg LCS（行级 + 词级），0 依赖（`shared/utils/revision-diff.ts`） | 后端 diff-match-patch / jsdiff | 零新依赖（FR-12 红线）；长 Markdown 友好；客户端实时 |
| Diff 折叠段 | 保留每段上下文 3 行，中间连续 unchanged 段折叠 | 全部展开 | 减少视觉噪声；与 KV 一致 |
| 回滚实现 | 旧版 content 复制为新 revision（`is_restore=True`） | 物理修改指针 | 不破坏历史（不可变原则）；可看到"我是从 r1 恢复的" |
| Fullscreen 实现 | 固定定位 overlay + body `overflow: hidden` | CSS `:fullscreen` API | Angular SPA 下 `:fullscreen` 触发条件多；overlay 简单可控 |
| 暗色主题 | 全文应用 `.dark` 类 + 自带 CSS 变量切换 | CSS 变量 | 不依赖全局主题；同一文档可独立切换沉浸与正常视图 |
| Esc 退出 | `HostListener('window:keydown.escape')` | 局部监听 | 焦点在 input 时也能响应 |
| 内容变更触发条件 | title 或 content 任意一个变了 → 走 revision 路径 | 只看 content | 标题也是历史的一部分；用户能说出"我改了标题" |

## 2. 设计思路

需求拆为 3 条主线：

1. **数据模型**：`DocumentRevision`（不可变快照）+ `documents.current_revision_id/number`（头指针）。创建文档同步生成 r1；编辑时如果 title/content 变化则走 `save_document_with_revision` 乐观锁；元数据变更走原 `update_document`。
2. **乐观锁**：服务端在事务内校验 expected == current_revision_number；不等则抛 `RevisionConflict(expected, current)`，API 层映射为 HTTP 409 + JSON body `{code, expected, current}`；前端捕获后弹"接受最新"按钮。
3. **Diff + 回滚 + Fullscreen**：diff 走客户端 LCS（前端独立组件 / 内联渲染）；回滚 = 旧版 content 复制为新 revision（不变指针改 history）；fullscreen 走 overlay 模式。

## 3. 架构改动

```
agentboard/
├── domains/documents/
│   └── models.py                            # +DocumentRevision（id/document_id/revision_number/title/content/author_id/author/change_note/is_restore/restored_from_revision/created_at）
├── models.py                                # re-export + __all__
├── service.py                               # +RevisionConflict / +_next_revision_number / +create_revision / +list_revisions / +get_revision / +save_document_with_revision / +restore_revision / create_document 改造
├── api.py                                   # +4 REST 端点
└── mcp_server.py                            # +4 MCP 工具
migrations/versions/
└── g6h7i8j9k0l1_add_document_revisions.py   # 新表 + 4 索引
frontend/src/app/
├── shared/utils/
│   └── revision-diff.ts                     # Hirschberg LCS（行+词级，0 依赖，移植自 KV）
├── models.ts                                # +DocumentRevisionItem / DocumentItem 加 2 字段
├── api.service.ts                           # +4 新方法
├── app.ts                                   # +8 signal / +8 方法 / submitDocModal 改造 / HostListener(escape)
├── app.html                                 # tab 切换 / 历史 list / 409 冲突卡 / fullscreen overlay / diff dialog / 编辑模态加 change_note
└── app.css                                  # +~190 行
tests/
├── test_doc_revisions.py                    # +12 case（service 层）
└── test_epic139_revision_diff_fullscreen_e2e.py  # +new Playwright
```

## 4. 开发细节

### 4.1 数据模型（`agentboard/domains/documents/models.py`）

```python
class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "revision_number", name="uq_document_revisions_doc_revnum"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    author: Mapped[str | None] = mapped_column(String(100), nullable=True)
    change_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    is_restore: Mapped[bool] = mapped_column(default=False, nullable=False)
    restored_from_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
```

### 4.2 Service 关键流程

**创建文档**：
```python
doc = Document(...); s.add(doc); s.flush()
rev = DocumentRevision(document_id=doc.id, revision_number=1, title=..., content=..., change_note="初始版本")
s.add(rev); s.flush()
doc.current_revision_id = rev.id
doc.current_revision_number = 1
_commit(s)
```

**乐观锁保存**：
```python
def save_document_with_revision(s, *, id, expected_revision_number, title=None, content=None, change_note, ...):
    d = s.get(Document, id)
    if not d: raise NotFound
    if expected_revision_number != (d.current_revision_number or 0):
        raise RevisionConflict(expected=expected, current=current)
    if new_title == d.title and new_content == d.content:
        return d  # 空保存不消耗 revision_number
    rev = DocumentRevision(document_id=id, revision_number=_next_revision_number(s, id), ...)
    s.add(rev); s.flush()
    d.title = rev.title; d.content = rev.content
    d.current_revision_id = rev.id; d.current_revision_number = rev.revision_number
    _commit(s)
    return d
```

**回滚**：
```python
def restore_revision(s, *, id, revision_number, change_note, ...):
    src = get_revision(s, id, revision_number)
    note = (change_note or "").strip()[:500]
    if not note: raise InvalidValue("change_note is required for restore")
    new_rev = DocumentRevision(
        document_id=id, revision_number=_next_revision_number(s, id),
        title=src.title, content=src.content,
        change_note=f"回滚自 r{revision_number}：{note}",
        is_restore=True, restored_from_revision=revision_number,
    )
    s.add(new_rev); s.flush()
    d.title = src.title; d.content = src.content
    d.current_revision_id = new_rev.id; d.current_revision_number = new_rev.revision_number
    _commit(s); return d
```

### 4.3 Diff 渲染（前端 LCS）

复用 KV 的 `revision-diff.ts`：`buildRevisionDiff(old, new) → RevisionDiffBlock[]`，
每块 `{kind: 'rows' | 'collapsed', rows: RevisionDiffRow[]}`，行内 `oldFragments` / `newFragments` 带
`{text, kind: 'unchanged' | 'added' | 'removed'}` 用于词级高亮。

UI 渲染：双列 grid，红/绿/黄底色分别标 added / removed / changed；折叠段显示「⋯ 折叠 N 行 ⋯」。

### 4.4 Fullscreen Workspace

```html
<div class="doc-fullscreen" [class.dark]="..." tabindex="0">
  <header>... title + badges + theme toggle + exit </header>
  <main>... doc-content (rendered markdown) ...</main>
  <footer>... last updated + Esc hint ...</footer>
</div>
```

```typescript
@HostListener('window:keydown.escape')
onEscapeKey() { if (this.docFullscreenOpen()) this.closeDocFullscreen(); }
```

### 4.5 编辑模态的 change_note 必填

`submitDocModal` 改造：内容/标题变了 → 必填 `docChangeNote()`，否则 notify 报错；走
`saveDocumentRevision`；捕获 409 → 设置 `docRevisionConflict` 信号 → 模板渲染冲突卡（提供
"接受最新" 按钮调 `acceptCurrentAndReload`）。

## 5. 测试覆盖

### 5.1 Service 单测（`tests/test_doc_revisions.py`，12 case）

- 创建文档 → r1 + current_revision 指针
- 多次 save → revision_number 单调递增
- 乐观锁：expected 不匹配 → RevisionConflict(expected, current)
- 空保存不消耗 revision_number
- list_revisions 倒序分页 / get_revision / 404
- restore：复制为新 revision、is_restore=True、restored_from_revision=N、历史保留
- restore 强制 change_note（空 → InvalidValue）
- restore 源不存在 → 404
- 头元数据（type/status）走原 update_document，不影响 revision
- RevisionConflict 携带 expected/current 字段（供 API 序列化）

### 5.2 Playwright E2E（`tests/test_epic139_revision_diff_fullscreen_e2e.py`）

- 详情页 r3 badge 正确
- 切到历史 tab 列出全部 revision 倒序
- 选 2 份 revision → diff 弹窗 +/Δ 统计 + 行/词高亮
- 「回滚到此」→ 形成新 r4 + 标 is_restore
- 409 冲突：客户端基于 r1、服务端已 r2 → 弹冲突卡
- Fullscreen 入口 → 暗/亮切换 → Esc 退出
- 0 console error / pageerror

## 6. 已知遗留

- 详情页 tab 切到 history 不会自动滚动到顶部（用户行为习惯可接受）。
- 列表视图（Phase A）暂未显示当前 revision number；可在「列表行」加一列 rN（独立 PR）。
- 409 冲突卡的"接受最新"按钮会调 `get_document` 重新拉取最新（最简实现）；后续可加
  `diff` 视图让用户手动合并两版。
- `update_document` 旧路径仍接受 title/content（兼容）；不创建 revision。可在 v0.4 弃用并
  强制走 `save_document_with_revision`。
