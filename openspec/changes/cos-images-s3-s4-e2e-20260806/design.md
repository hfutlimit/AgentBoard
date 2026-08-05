# Design: Epic 64 S3/S4 评论与描述图片渲染验证

## 现状核查（S2 统一管线已覆盖的调用点）

`frontend/src/app/app.html` 中所有 markdown 渲染均经 `renderMarkdown()`：

| 场景 | 模板调用（行号） | DOM 容器 | 图片样式 |
|---|---|---|---|
| 文档正文 | `renderMarkdown(d.content)` (1063/2283) | `.doc-content` | `.doc-content img` |
| 任务描述 | `renderMarkdown(current.description)` (1113/2075) | `.card.md.task-md` / `.two-col .task-md` | `.task-md img` |
| Story 描述 | `renderMarkdown(current.description)` (1245) | `.story-description.task-md` | `.task-md img` |
| Epic 描述 | `renderMarkdown(current.description)` (1117) | `.detail-panel .card.md.task-md` | `.task-md img` |
| quick-view 描述 | `renderMarkdown(qt.description)` (3010) | `.qv-desc.md` | `.qv-desc img` |
| 任务评论 | `renderMarkdown(comment.content)` (1157/2137) | `.comments-card .md.text-pre` | `.md img` |
| Story 评论 | `renderMarkdown(comment.content)` (1283) | `.comments-card .md.text-pre` | `.md img` |
| Epic 评论 | `renderMarkdown(comment.content)` (1070) | `.comments-card .md.text-pre` | `.md img` |
| 文档评论 | `renderMarkdown(c.content)` (3030) | `.qv-comment-body` / `.md.text-pre` | `.qv-comment-body img` / `.md img` |

图片渲染逻辑（`app.ts` L5753+ `renderMarkdown` 内 `inline()`）：
- 正则 `!\[([^\]]*)\]\(([^)\s]+)\)` 匹配图片语法；
- **协议白名单**：仅 `^https?://`（含 COS 预签名 URL）；
- **属性逃逸拦截**：URL 含 `" ' 空白 < >` 直接拒绝；
- 输出 `<img src=... alt=... loading="lazy" referrerpolicy="no-referrer">`（Angular sanitizer 会剥离非白名单属性，属于纵深防御）。

## 验证设计

### 1. E2E（tests/test_epic64_s3_s4_e2e.py，Playwright + 本地 Docker 栈）

**数据准备（API 自建自清）**：
```
POST /api/projects/3/epics            → [E2E-ts] S4 Epic 描述图片（description 含 合法+危险 图片）
POST /api/epics/{eid}/stories         → [E2E-ts] S4 Story 描述图片（同上）
POST /api/stories/{sid}/tasks         → [E2E-ts] S3/S4 图片渲染任务（同上）
POST /api/tasks/{tid}/comments        → 2 条：合法图片 / 危险协议
POST /api/stories/{sid}/comments      → 1 条：合法图片
```

**危险输入样本**：
- `![x](javascript:alert(1))` — JS 协议
- `![x](data:image/svg+xml;base64,...)` — data 协议
- `![x](https://ok.com/a.png" onerror="alert(1))` — 属性逃逸

**UI 断言矩阵**（深链路由 + 选择器）：

| 步骤 | 路由 | 断言 |
|---|---|---|
| S4-A 任务描述 | `/task/{tid}` | `.two-col .task-md img` 渲染 1 个；src 含 COS 域名；js/data 纯文本 |
| S3-A 任务评论 | 同上 | `.comments-card .md.text-pre img` ≥1；js/data 纯文本 |
| S4-B Story 描述 | `/story/{sid}` | `.story-description img` =1；js 纯文本 |
| S3-B Story 评论 | 同上 | `.comments-card .md.text-pre img` ≥1 |
| S4-C Epic 描述 | `/epic/{eid}` | `.detail-panel .card.md.task-md img` =1；js 纯文本 |
| S3-C 抽屉 | `/story/{sid}` → 「Task 列表」tab → `.task-quick-view-btn` | `.qv-desc img` =1；`.qv-comment-body img` ≥1；行内 UI 填 `![新截图](IMG_OK)` 提交后评论 +1 且 img +1 |

**红线**：console error / pageerror / js-css 请求失败 全 0；截图存 `tmp/`。

**清理**：评论 → 任务 → Story → Epic（DELETE 级联）。

### 2. 前端单测（app.spec.ts，vitest）

在 S2 的 `renderMarkdown 图片渲染` describe 内新增：
- 评论场景：图片与加粗/代码/多行共存 → 2 个 `<img>`；
- 描述场景：标题+图片+列表共存，危险图片拒绝 → 仅 1 个 `<img>`；
- 空 alt 边界：`![](url)` → `alt=""`。

### 3. 回归与遗留修复

- `tests/test_smoke.py::test_service_layer` L87 `service.list_comments(s, t.id)` → `service.list_comments(s, task_id=t.id)`（service 已 keyword-only，位置参数 TypeError 是 9e70415 遗留）。

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 页面路由/选择器与实现漂移 | E2E 全部基于深链真实 DOM；选择器以 app.html 实际 class 为准（任务描述 `.two-col .task-md` 而非 `.card.md.task-md`） |
| Angular sanitizer 剥离 loading/referrerpolicy | 单测（方法层）验证属性输出，E2E 只断言 src/alt 与计数，不断言非白名单属性 |
| Story 详情默认 tab 为 detail | E2E 显式点击「Task 列表」tab 后再找任务行 |
