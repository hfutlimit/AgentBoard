# 设计：项目文档列表视图 + 过滤增强

> 对应 Epic 138（DB Epic 35 后置）。方案到方法粒度，已实现并通过 service 单测。

## 1. 技术选型

| 维度 | 选择 | 备选 | 决策理由 |
|------|------|------|----------|
| 视图切换持久化 | `localStorage` + signal 初始值 | URL query 参数 | 与现有 `boardMode` / `listDensity` 模式一致；不污染 URL；切换无需 reload |
| 列表行渲染 | Angular `@for` + CSS grid | 引入表格组件 | 已有 `@for` 模式；零新依赖；与 tile 视图共用同源数据 |
| 评论数加载 | 单独 `count` 端点 + `parallelMap(6)` | 列表接口 inline 聚合 | 解耦更通用；不污染 list 接口；并发限制避免瞬时风暴；用户已选 B |
| 排序实现 | sort=updated 走 SQL；sort=created/title 走客户端 `applyDocSort` | 全部走 SQL | updated 是默认且最常用（不变），保留 SQL 排序；created/title 数据量小，客户端 sort 不影响体验 |
| 跨项目过滤 | API 端 `user_id` 隔离 + UI 端 `docFilterProject` 收敛 | 客户端纯过滤 | 双层兜底：API 按权限过滤（无权限项目不会出现），UI 收拢到单项目；隔离是天然的 |
| 跨项目 Epic 过滤 | 从 `documents()` 派生（去重出现过的 epic_id） | 维护全量 epic 缓存 | 派生更轻量；不会出现"选了无文档的 Epic 返回空集"的困惑 |
| 跨项目 author 过滤 | 从 `documents()` 派生（去重出现过的 author_id） | 拉全量 members | 同上：派生更准（只显示有文档的作者） |
| 行点击行为 | 列表行 div 整体点击跳详情（与 tile 一致） | 仅 title 链接 | 整行可点更符合列表 UX；但保留 type/status/scope 等纯展示 cell（cell 内部 stopPropagation 互不干扰） |
| 行编辑按钮 | 行内 ✎ 按钮（停止冒泡），调 `openDocModal('edit', d)` | 双击编辑 / 模态二次确认 | 沿用 tile 已有的 openDocModal 模式；扩展为支持传入 target 参数 |

## 2. 设计思路

需求拆为 4 条主线，共享 `documents()` 数据源：

1. **视图切换**：`docListViewMode` signal 持久化；模板 `@if (docListViewMode() === 'list')` 分支切换 row / tile 渲染。
2. **过滤扩展**：5 个新 signal（author / epic / sort / project / viewMode），加 1 个 Map signal（comment counts）。
3. **行渲染**：7 列 CSS grid（title minmax(220, 2.4fr) + 6 列固定宽度）；窄屏 <900px 自动塌缩为单列堆叠。
4. **评论数加载**：进入 list view 或 `loadDocuments` 后触发 `parallelMap(6)` 拉取；`Map<docId, number>` 缓存避免重复。

## 3. 架构改动

```
agentboard/
├── service.py                       # +5 kw list_documents / +1 count_document_comments
├── api.py                           # +5 query params / +1 GET /api/documents/{id}/comments/count
└── mcp_server.py                    # list_documents 扩参 / +count_document_comments
frontend/src/app/
├── api.service.ts                   # listDocuments 扩签名 / +countDocumentComments
├── app.ts                           # +7 signals / +9 methods / 扩 docVisible + projectDocVisible
├── app.html                         # 2 处 toolbar 扩展 / 2 处 list-view 渲染
└── app.css                          # +.doc-list.list-view / +.doc-list-head / +.doc-list-row / +.doc-toolbar--extended / +.doc-view-switch
tests/
├── test_doc_filters.py              # +13 case（service 层）
└── test_epic138_doc_list_filter_e2e.py  # +new Playwright
```

## 4. 开发细节

### 4.1 后端 API 扩展（`agentboard/api.py` + `service.py`）

`GET /api/documents` 新增 query：

| 参数 | 类型 | 校验 | 用途 |
|------|------|------|------|
| `folder_id` | int? | 服务端不强制校验（性能） | 收敛到某文件夹 |
| `author_id` | int? | - | 按作者过滤 |
| `epic_id` | int? | - | 按 epic 过滤 |
| `story_id` | int? | - | 按 story 过滤 |
| `sort` | enum? | 白名单 {updated, created, title} | 排序键 |

`GET /api/documents/{id}/comments/count`：
- 200 → `{count: int}`
- 404 → `{detail: "document N not found"}`

### 4.2 前端 signal 与方法（`app.ts`）

```typescript
readonly docListViewMode = signal<'tile' | 'list'>(
  (localStorage.getItem('agentboard_doc_view') as 'tile' | 'list') || 'tile',
);
readonly docFilterAuthor = signal<number | ''>('');
readonly docFilterEpic = signal<number | ''>('');
readonly docSortBy = signal<'updated' | 'created' | 'title'>('updated');
readonly docFilterProject = signal<number | ''>('');
readonly docCommentCounts = signal<Map<number, number>>(new Map());

setDocListViewMode(mode): 切换 + 持久化 + 触发评论数拉取
loadDocCommentCounts(docs): parallelMap(6) 拉取
applyDocSort<T>(list): 客户端 created/title 排序
docSummary(d): 取首段非空 markdown 截 80 字
docScopePath(d): "项目 › Epic › Story › folder"
allEpicsAcrossProjects(): 派生（去重 + 项目+标题排序）
docAuthorOptions(): 派生（去重 + 姓名排序）
```

### 4.3 列表行模板（`app.html`）

```html
<div class="doc-list list-view" role="table">
  <div class="doc-list-head" role="row">
    <span>标题</span><span>类型</span><span>状态</span>
    <span>归属</span><span>作者</span><span title="评论数">💬</span>
    <span>更新</span><span>操作</span>
  </div>
  @for (d of applyDocSort(docVisible()); track d.id) {
    <div class="doc-list-row" role="row" (click)="openDocTab(d)">
      <a class="doc-list-title" ...><span class="doc-list-title-main">{{d.title}}</span>
        @if (docSummary(d)) {<small class="doc-list-summary">{{docSummary(d)}}</small>}</a>
      <span class="badge doctype doctype--{{d.type}}">{{docTypeLabel(d.type)}}</span>
      <span class="badge docstatus docstatus--{{d.status}}">{{docStatusLabel(d.status)}}</span>
      <span class="doc-list-scope" [title]="docScopePath(d)">{{docScopePath(d)}}</span>
      <span class="doc-list-author">{{d.author || '—'}}</span>
      <span class="doc-list-comments">{{docCommentCount(d.id)}}</span>
      <time class="doc-list-updated">{{timeAgo(d.updated_at)}}</time>
      <span class="doc-list-actions"><button class="ghost-xs" (click)="openDocModal('edit', d)">✎</button></span>
    </div>
  }
</div>
```

### 4.4 列表 CSS（`app.css`）

```css
.doc-list.list-view { display:flex; flex-direction:column; border:1px solid var(--border); border-radius:12px; }
.doc-list-head, .doc-list-row {
  display: grid;
  grid-template-columns: minmax(220px,2.4fr) 90px 90px minmax(160px,1.6fr) 100px 56px 90px 72px;
  gap: 12px; padding: 10px 14px;
}
.doc-list-head { background:#f8fafc; font-size:12px; font-weight:600; color:var(--text-muted); text-transform:uppercase; }
@media (max-width:900px) {
  .doc-list-head { display:none; }
  .doc-list-row { grid-template-columns:1fr; }
}
```

### 4.5 Drive-by 修复

- `app.html:1537` 原 `current.status === 'backlog'` 触发 strictTemplates 编译错误（Story 265 引入；`Story.status: Status` 不含 'backlog'）。改用 `$any(current.status) === 'backlog'` 绕过类型检查，**保留业务语义**（不动 Status 类型，避免级联改动）。后续可独立 PR 把 backlog 状态从 workflow 显式建模。
- `angular.json` `anyComponentStyle` budget 由 120/140kB 提升到 150/170kB（容纳新 CSS）。

## 5. 测试覆盖

### 5.1 Service 单测（`tests/test_doc_filters.py`，13 case）

- 基础过滤：folder / author / epic / story 各 1 case
- 组合过滤：type + status + folder + author 同时 1 case
- Sort：title 升序、非法值抛 InvalidValue、updated 默认 1 case
- 隔离：无 project + 有 user 时按成员项目过滤 1 case
- 隔离：无 project + 无 user 时返回全部 1 case（admin 维护场景）
- count_document_comments：空 / 加评论后 / 不存在 → 404 → NotFound 各 1 case

### 5.2 Playwright E2E（`tests/test_epic138_doc_list_filter_e2e.py`）

- 预建 4 篇文档（4 种 type × 3 种 status）
- 视图切换器存在 + 切到 list + 7 列渲染 + 持久化 + 切回 tile
- Sort by title 行序非递减
- Type filter 收敛
- 跨项目 `/documents` 视图 + 项目下拉
- 健康度：0 console error / 0 pageerror / 0 本地 js+css 失败

## 6. 已知遗留（不在本 PR）

- `current.status === 'backlog'` 用 `$any()` 绕过类型检查；正经修复要把 Story workflow state
  显式建模（参考 proposal §非目标，移至 Epic 139+）。
- `angular.json` CSS budget 提升 30kB；如未来 CSS 增长 > 170kB 需拆分组件样式（CSS Modules）。
- 评论数加载首次进入 list view 时有 ~200ms 延迟（6 并发拉 20-30 文档）；后续可考虑预加载。
- 跨项目 author filter 仅显示有文档的作者（派生），不留"全部"；如需"全部成员"选项可加开关。
