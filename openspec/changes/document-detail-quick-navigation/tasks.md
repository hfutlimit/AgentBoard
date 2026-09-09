# Tasks：文档详情快速定位导航

> 本清单供后续实现使用；本次 Design 交付不勾选实现或验证项。

## 模板与客户端逻辑

- [x] `src/frontend/src/app/app.ts`：新增 `DocumentQuickNavigationTarget`、私有 id 映射和唯一 `isDocumentQuickNavigationMode()`，其条件必须包含 document view、preview、content 及 `!docFullscreenOpen()`。
- [x] `src/frontend/src/app/app.html`：只在该守卫为真时、`.doc-title` 后输出 `nav.doc-quick-nav`，其中有可访问名称为「正文开头」和「评论开头」的原生按钮及稳定 button/nav testid。
- [x] `src/frontend/src/app/app.html`：在同一守卫下为预览 Markdown article 与评论卡片静态 section header 输出唯一的 `document-content-start` 和 `document-comments-start` id/testid；fullscreen 副本、history 与 split 分支不得复用它们。
- [x] `src/frontend/src/app/app.ts`：实现同步 `scrollDocumentDetailTo`：先检查守卫和目标，再检查 `scrollIntoView`；不移动焦点、不改 URL/localStorage、不显示 toast、不请求 API。
- [x] `src/frontend/src/app/app.ts`：`matchMedia` 必须以 `typeof view?.matchMedia === 'function'` 保护并安全处理异常；reduce 用 `auto`，其它或不可用情形用 `smooth`。

## 样式与可访问性

- [x] `src/frontend/src/app/app-features.css`：添加 `.doc-quick-nav*` 的 sticky、主题、边框、z-index 与 `scroll-margin-block-start` 规则；确认实际详情滚动根支持该 sticky 行为。
- [x] `src/frontend/src/app/app-features.css`：保证浅色/深色可见性和 `:focus-visible` ring；减少动态效果下不保留非必要 transition。
- [x] `src/frontend/src/app/app-features.css`：在 `max-width: 640px` 下导航满宽，按钮等宽，至少 44px，且不裁切文字或遮挡标题、正文、空态和评论表单。

## 自动化与验收

- [x] 扩展 `src/frontend/src/app/app.spec.ts`：验证守卫的四个条件、正常状态的 nav/目标唯一性、history/split/fullscreen 状态的 nav 与目标缺席，并直接设置既有 signals，不新增产品模式入口。
- [x] 扩展组件测试：spy `scrollIntoView` 的 smooth/auto 参数、`matchMedia` 缺失/抛错回退、目标缺失和 `scrollIntoView` 缺失安全返回、无额外 ApiService 调用。
- [x] 新增 `tests/test_document_detail_quick_navigation_e2e.py`：覆盖长正文、有评论、无评论、空正文、稳定定位契约、正文/评论落点、无 console/page error 和无额外 API 请求。
- [x] E2E：覆盖 Tab + Enter/Space、焦点可见、390px 窄屏、浅色/深色主题、reduced motion 可用性及 sticky 不遮挡目标。
- [x] 执行前端 Vitest 单次运行、`cd src/frontend && npm run build` 和可达的文档模块回归，记录真实运行环境与结果。
- [x] 不把现有无 UI 入口的 history/split/fullscreen 进入/退出 E2E 列为本变更验收；入口恢复由独立任务处理后再恢复该端到端回归。
