# Tasks: 提案已生成 Story 链接交互

## 实现

- [ ] 在 `App` 定义并暴露单一 `proposalStoryId(proposal)` 解析入口，落实 `story_id` 优先、仅缺失时 Story ticket 回退以及正整数校验。
- [ ] 更新根模板提案详情的终态工单区域：有效 Story 引用渲染“查看 Story #<id>”和真实 `/story/<id>` 链接；其他工单保留当前文本。
- [ ] 更新 `ProposalDetailViewComponent`：转发同一解析入口，渲染相同链接，并按工作台惯例只拦截普通主键以打开/复用 Story 实体 Tab。
- [ ] 不修改 Proposal API、模型、TicketRequest、后端状态机、数据库迁移或自动建单代码。

## 自动化验证

- [ ] 增加解析和模板覆盖：`story_id` 优先、Story ticket 回退、非 Story ticket、无效非空 `story_id`，以及两个入口的相同文案/href。
- [ ] 增加工作台点击覆盖：普通左键委托 `openWorkspaceEntity('story', id)`；Ctrl/Cmd/Shift/中键不拦截。
- [ ] 在 `src/frontend` 运行聚焦 Vitest 测试与 `npm run build`。
- [ ] 执行 `openspec validate proposal-story-link-interaction --strict` 与 `git diff --check`。

## 验收标准

- [ ] `story_created` 或 `ticket_created` 提案含有效 Story 引用时，两个详情入口均显示业务可读的 Story 链接，而非裸 `id=数字`。
- [ ] 链接 href 严格为 `/story/<storyId>`；工作台普通左键进入或复用对应 Story Tab，目标路由为 `/project/<projectId>/stories/<storyId>`。
- [ ] Ctrl/Cmd/Shift/中键不被工作台代码拦截，浏览器可基于真实 href 原生打开。
- [ ] `story_id` 缺失时仅 `ticket_type='story'` 的 `ticket_id` 可回退；Epic、Task、Bug 与未知 ticket 不得生成 Story 链接。
