# 变更提案：文档详情快速定位导航

## 背景

独立文档详情路由（`/documents/:id`）把标题、Markdown 正文和评论卡片置于同一纵向内容流。阅读长文档后，用户需要手动滚回正文起点，或继续滚到评论区才能阅读、讨论和发表评论。当前标题附近没有这两个稳定入口；Markdown 内部标题由渲染结果决定，不能作为可靠锚点。

本提案修订了 Task 1723 第一轮评审发现的四个缺口：fullscreen 覆盖层出现时普通详情树仍保留在 DOM；当前模板没有历史、分屏或全屏的进入入口；运行环境未必提供 `matchMedia`；验收没有覆盖空正文。

## 目标

- 仅在普通预览内容状态提供紧凑、持续可见的「正文开头」和「评论开头」按钮。
- 为 Markdown 正文容器起点与评论卡片标题起点提供稳定、唯一的 DOM 定位目标；空正文、空评论和评论读取失败时仍安全可用。
- 默认平滑滚动；声明减少动态效果时即时滚动；不支持 `matchMedia` 时仍可用。
- 让鼠标、键盘和辅助技术获得等价行为，并在窄屏、浅色和深色主题下保持可见、可点击。

## 范围

- 仅改动 Angular 前端的独立文档详情普通预览内容流：`src/frontend/src/app/app.html`、`src/frontend/src/app/app.ts`、`src/frontend/src/app/app-features.css`、`src/frontend/src/app/app.spec.ts`，以及新的 Playwright 测试。
- 普通预览内容状态严格为 `view() === 'document' && docViewMode() === 'preview' && docDetailTab() === 'content' && !docFullscreenOpen()`。导航和两个定位目标只在该状态输出。
- 复用注入的 `DOCUMENT`、现有主题变量和按钮样式；定位不写入 URL、localStorage 或任何持久化状态。

## 非范围与已知验证边界

- 不增加目录、全文搜索、URL hash、分享定位链接、后端 REST/MCP 接口或数据模型。
- 不改变 Markdown 渲染、文档读取/编辑、revision、评论加载、排序、发布、权限或数据请求。
- 不恢复或新增历史、分屏、fullscreen 的 UI 进入入口。虽然对应 signal 和方法仍存在，但 `app.html` 当前没有这些入口；恢复它们是独立范围。
- 因入口不可达，本变更不得把 `tests/test_epic139_revision_diff_fullscreen_e2e.py` 当作可执行的进入/退出验收门槛，也不得为了测试而扩展产品 UI。普通预览外的隐藏契约由 Angular 组件测试直接设置既有 signals 覆盖；这些模式的端到端进入/退出回归应在入口恢复任务中重新建立。

## 影响

- 纯前端、无迁移、无 API、权限或持久化契约变化。
- `document-quick-navigation`、两个按钮和两个目标的 id/`data-testid` 成为稳定的测试契约，但不承担 URL 路由职责。
- 增加组件测试与专用 Playwright 测试；浏览器测试使用临时数据并在系统临时位置保存其运行产物。
