# Proposal: 提案已生成 Story 的可访问链接

## Why

提案转换完成后，详情页目前把已生成工单展示为 `id=<数字>` 的不可点击文本。用户不能从提案直接进入已生成的 Story；同时，项目工作台详情和根路由详情是两套渲染入口，若各自实现判断或跳转，很容易出现链接、优先级或新标签行为不一致。

## What Changes

- 在两个提案详情入口，将有效的已生成 Story 引用展示为“查看 Story #<id>”链接，而不是裸编号。
- 建立唯一的 Story 引用解析规则：优先 `proposal.story_id`；仅在它缺失时，且 `proposal.ticket_type === 'story'`，才使用 `proposal.ticket_id`。
- 工作台内普通左键复用既有实体 Tab 打开 Story；链接仍保留真实 `/story/<id>` href，使 Ctrl/Cmd/Shift/中键保持浏览器原生行为。
- 根模板入口使用同一引用解析规则和同一真实 href，但由 Angular 路由按既有非工作台路径处理普通点击。

## Non-goals

- 不修改 Proposal、Story、TicketRequest 的 API、模型、状态机、数据库或自动建单流程。
- 不从 Epic、Task、Bug、TicketRequest 或状态文本推断 Story；非 Story 工单及生成中、失败、无效引用的现有展示不变。
- 不改动 Story 页面、工作台 Tab 路由契约，或浏览器原生新标签行为。

## Impact

- `src/frontend/src/app/app.ts`、`app.html`：提供并使用共享的 Story 引用解析入口；根模板渲染真实 Story 链接。
- `src/frontend/src/app/proposal-detail-view/`：工作台详情渲染同一链接，并在普通左键委托既有 `openWorkspaceEntity('story', ...)`。
- `src/frontend/src/app/app.spec.ts`（以及按实现需要的组件测试）：覆盖优先级、兼容回退、非 Story 负例和点击边界。

后端、MCP、数据库、部署产物均不在本变更范围内。
