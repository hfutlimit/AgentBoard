# Change: Epic 64 S3/S4 评论与描述图片渲染验证（Task 993/994 → in_review，2026-08-06）

## Why

Epic 64（腾讯云 COS 图片上传与图片展示）当前 S1（上传后端，Task 991）与 S2（文档 markdown 图片渲染，Task 992）已完成并 in_review。剩余 S3（评论支持展示图片）与 S4（Story/Epic 描述支持展示图片）为 backlog。

S2 的实现选择了**统一渲染管线**：前端 `renderMarkdown()` 是所有 markdown 渲染的唯一入口（文档 `.doc-content`、任务描述 `.task-md`、Story 描述 `.story-description`、Epic 描述 `.card.md.task-md`、quick-view 抽屉描述 `.qv-desc`、所有评论区 `.md.text-pre` / `.qv-comment-body`），图片语法 `![](https://...)` 与 XSS 白名单（http(s) 协议 + 危险字符拦截）在方法层一次实现后**自动覆盖评论与描述场景**；CSS 亦已为 `.qv-desc img` / `.qv-comment-body img` / `.task-md img` / `.md img` 统一提供样式（含暗色主题）。

因此 S3/S4 无需新增前端代码，核心交付物为**独立验证**：以真实页面 DOM 断言证明评论与描述两条链路确实走统一管线、图片正常渲染、危险协议保持纯文本，并补齐方法层单测用例与状态流转。

## 方案对比

| 方案 | 说明 | 取舍 |
|---|---|---|
| A. 为评论/描述单独新增图片字段（多图缩略图） | 评论表加独立图片列、前端缩略图网格 | 增加 DB 契约与前后端复杂度；评论 markdown 图片已覆盖绝大多数贴图场景；超出 Epic 64 验收原文（"评论支持 markdown 图片语法"） |
| **B. 复用统一 renderMarkdown 管线 + E2E 验证**（选定） | 依赖 S2 已建立的统一入口，S3/S4 以端到端 DOM 断言验证真实链路 | 零代码改动、零契约变更、交付快且风险低；验收原文要求（"评论内容支持 markdown 图片语法"）即 B 方案的能力 |
| C. 仅方法层单测 | 只测 renderMarkdown 本身 | 无法证明评论/描述页面真实调用了管线；验收价值不足 |

选定 **方案 B**：方法层补充 3 个用例（评论场景、描述场景、空 alt 边界）+ 端到端 DOM 断言（任务/Story/Epic 描述与评论区、quick-view 抽屉）全覆盖。

## 设计要点

1. **统一管线事实核查**：`app.html` 中 14 处 `renderMarkdown(...)` 调用覆盖文档/任务/Story/Epic 描述与全部评论区；`app.css` 3616-3627 行为全部容器类提供 img 样式（含 `[data-theme="dark"]`）。
2. **E2E 数据自建自清**：测试用 API 创建临时 Epic → Story → Task（描述含合法 COS URL + javascript:/data:/onerror 三类危险输入），预置任务/Story 评论，验证后删除，不污染生产数据。
3. **验证矩阵**：
   - S3：任务详情评论 `.comments-card .md.text-pre`、Story 详情评论、quick-view 抽屉 `.qv-comment-body` 渲染 `<img>`；危险协议保持纯文本；抽屉行内 UI 添加图片评论后即时渲染。
   - S4：任务详情描述 `.two-col .task-md`、Story 描述 `.story-description`、Epic 描述 `.detail-panel .card.md.task-md`、抽屉描述 `.qv-desc` 渲染 `<img>`；危险协议保持纯文本；合法图片仅 1 个 `<img>`。
4. **0 报错红线**：全程收集 console error / pageerror / js-css 加载失败，必须全 0。
5. **零契约变更**：本次仅新增/修改测试文件，不触碰前端源码、后端源码与 DB 契约。

## 验收

- [x] 新增 `tests/test_epic64_s3_s4_e2e.py` 31/31 PASS（0 console error / pageerror / js-css 失败）
- [x] 前端单测补充 3 用例（评论场景/描述场景/空 alt）后 21 passed
- [x] pytest 聚焦回归 50 passed + 核心回归 9 passed
- [x] 修复遗留 `tests/test_smoke.py::test_service_layer`（`list_comments` 位置参数 → keyword-only 兼容）
- [x] MCP 状态：Task 993/994 → in_review；Story 63/64 → in_review
