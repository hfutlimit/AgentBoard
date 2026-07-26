# Design: Story/Epic 详情页描述 Markdown 渲染（v5.0）

## 复用决策
任务详情页（v4.9）与抽屉（v4.8）已实现完整的 Markdown 渲染体系（`renderMarkdown()` 渲染器 + `.task-md`/`.qv-desc.md` CSS，含 dark 主题）。Story/Epic 详情页描述与任务描述语义一致，因此**直接复用 `.task-md` 渲染类**，不新增重复 CSS，保持单一 Markdown 视觉规范。

## 数据流
- 模板读取 `current.description`（`current` = `story()` 或 `epic()` 信号），经 `[innerHTML]="renderMarkdown(current.description)"` 注入。
- `renderMarkdown()` 内部调用 Angular `DomSanitizer` 做 XSS 防护（与评论/文档一致），已在前序版本验证安全。

## 模板结构（@if/@else 三态）
```
@if (current.description) {
  <div class="card md task-md" [innerHTML]="renderMarkdown(current.description)"></div>
} @else {
  <div class="card md task-md-empty">（空）</div>
}
```
- Epic 详情页：`<div class="card md task-md">`（原 `text-pre` 替换为 `task-md`）。
- Story 详情页：`<div class="card md story-description task-md">`（保留 `story-description` 语义类，附加 `task-md` 渲染类）。

## 空态
无描述时显示 `.task-md-empty`「（空）」，与 v4.9 任务详情页空态一致。

## 风险
- 极低：仅模板 class 变更 + 复用既有方法/样式，无新逻辑、无后端依赖。
- 已通过 Playwright E2E 验证（Story/Epic 双向 Markdown 渲染 + 0 错误）与回归（v4.9 任务详情、v4.5 抽屉导航、后端 pytest 8 passed）。
