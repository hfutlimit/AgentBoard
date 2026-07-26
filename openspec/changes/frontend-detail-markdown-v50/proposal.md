# Change: Story/Epic 详情页描述 Markdown 渲染（v5.0）

## Why
任务列表「快速查看抽屉（v4.8）」与「任务详情页（v4.9）」的描述/Spec 已升级为 Markdown 渲染，与评论、文档体系一致。但 Story 与 Epic 详情页的描述仍使用 `text-pre` 纯文本渲染，Markdown 标记原样展示（如 `**加粗**`、`# 标题`），与已升级的页面体验割裂，也破坏了「详情页描述统一 Markdown 渲染」的一致性。

## What Changes
- `frontend/src/app/app.html`：
  - Epic 详情页（`@case ('epic')`）描述块由 `<div class="card md text-pre">{{ current.description }}</div>` 改为 `@if/else` 三态：有描述时 `<div class="card md task-md" [innerHTML]="renderMarkdown(current.description)"></div>`，无描述时 `<div class="card md task-md-empty">（空）</div>`。
  - Story 详情页（`@case ('story')`）描述块由 `<div class="card md story-description text-pre">` 改为同上三态，复用 `.story-description` 语义类 + `.task-md` Markdown 渲染类。
- `frontend/src/app/app.css`：复用既有 `.task-md` / `.task-md-empty`（v4.9 任务详情页 Markdown 样式，含 dark 主题），无需新增样式规则。
- 纯前端，零后端契约变更（复用既有 `renderMarkdown()` 渲染器 + `DomSanitizer` 防护）。

## Impact
- 仅前端模板/样式变更，不影响任何 API 契约或后端逻辑。
- 复用已验证的 `.task-md` 渲染体系（v4.8/v4.9），视觉与任务详情页一致。
- 新增 E2E `tests/test_epic63_v50_detail_md_e2e.py` 覆盖 Story/Epic 详情页 Markdown 渲染（标题/加粗/斜体/列表/引用/代码/链接）与「无原始标记」不变量 + 0 控制台/页面/404 错误。

## Status
Implemented（in_review）
