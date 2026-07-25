# Design: 快速查看抽屉评论区（v4.4）

## 概览
在 Quick View Drawer（v4.2/v4.3）内新增一个「评论区」区块，位于描述区之后、抽屉底部操作栏之前。抽屉打开时自动加载该任务评论，用户可在抽屉内直接添加/删除评论，无需跳转详情页。

## 数据流
```
openQuickView(task)
  └─ qvLoadComments()  → GET /api/tasks/{id}/comments → qvComments.set(list)
qvAddComment()  → POST /api/tasks/{id}/comments {author, content} → qvLoadComments() 刷新
qvDeleteComment(id) → DELETE /api/comments/{id} → qvLoadComments() 刷新
closeQuickView() → qvComments.set([]), qvCommentDraft.set('')
```

## 关键设计决策
1. **纯前端、零契约变更**：评论 API（`GET /api/tasks/{id}/comments`、`POST /api/tasks/{id}/comments`、`DELETE /api/comments/{id}`）早已存在且被详情页复用，本特性仅在前端新增调用与渲染，不改动任何后端契约。
2. **评论加载时机**：`openQuickView` 内 `void this.qvLoadComments()` 发起加载（`qvLoadingComments` 信号控制加载态），`qvTask()` 依赖已在列表中的 `tasks()`，无需额外数据装载。
3. **Markdown 渲染复用**：评论正文沿用既有 `renderMarkdown()`（Angular DomSanitizer 防护的渲染器），与详情页/评论区一致，避免 XSS。
4. **作者来源**：新增评论 `author` 取 `commentAuthor()`（localStorage `agentboard_comment_author` 或当前用户或「我」），与详情页添加评论行为一致。
5. **即时刷新**：添加/删除成功后统一 `qvLoadComments()` 全量重载，简单可靠；`qvCommentDraft` 在添加成功后清空。
6. **体验细节**：textarea 支持 ⌘/Ctrl+Enter 发送；删除按钮默认 `opacity:0`、hover 行内显示（仿 v4.2/v4.3 风格）；`disabled` 在草稿为空时禁用发送按钮。

## 视觉
- 评论区以 `border-top` 与上文分隔，沿用 `--surface-2`/`--border`/`--color-primary` 设计变量，dark 主题下映射至 `rgba(255,255,255,.05)`/`#2b2f44` 等。
- 评论正文内的 Markdown 子元素（code/pre/blockquote/a/列表/标题）有专门样式，与详情页评论区观感统一。

## 测试策略
- E2E（`tests/test_epic57_v44_drawer_comments_e2e.py`）：登录 admin → 进 story 25 → 建种子任务并 API 预置 1 评论 → 开抽屉断言评论区渲染 + Markdown 渲染 + 计数 → 行内添加断言列表 + API 复核 → 行内删除断言列表 + API 复核 → Esc 关闭；断言 0 pageerror/console/.js+.css 404。
- 回归：后端 `pytest test_epic30_cache.py` + 抽屉 E2E v4.2/v4.3，确认零回归。
