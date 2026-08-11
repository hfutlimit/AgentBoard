# Design: 命令面板接入 Ticket 后端搜索（v6.18）

## 现状

- `ProposalTicketRequest`（`agentboard/domains/proposals/models.py`）：`proposal_ticket_requests` 表，字段 `id / proposal_id / type(epic|story|task|bug) / parent_epic_id / parent_story_id / title / status(pending|processing|done|failed) / ticket_id / error / created_at / updated_at`。**表本身无 `project_id`**，项目归属需经 `proposal_id → proposals.project_id` 反查。
- `service.search_proposals`（v6.17）提供可见性收敛模板：非 admin 仅搜索 `ProjectMember` 项目，admin/None 全量。
- `api.py` 的 `/api/search/{epics,sprints,notifications,agents,proposals}` 提供端点模板：q 必填 + limit 1-50 + `_current_user` 鉴权。
- 前端 `paletteProposalResults`（v6.17）提供信号/分支/合并/分类标签完整模板；`models.ts` 已存在 `TicketRequestItem`（与 `_ser(ProposalTicketRequest)` 字段完全对齐）。

## 设计决策

### D1: 搜索字段 = title / type / 关联提案 title

- `ProposalTicketRequest.title`：工单标题（省略时为空串，此时靠提案标题命中）；
- `ProposalTicketRequest.type`：工单类型（epic/story/task/bug，用户可搜「task」）；
- `Proposal.title`：**关联提案标题**（join 已存在、零额外成本）——工单标题常为空（默认用提案标题），仅搜工单自身字段会漏掉绝大多数工单，这是与其余搜索端点最实质的差异点。

匹配 `ilike('%q%')` OR 组合，与既有搜索语义一致。

### D2: 可见性收敛 = 镜像 `search_proposals`（user_id 传入）

工单是提案的派生物，可见性必须与提案一致：

- `user_id=None`（内部调用）→ 全量；
- 非 admin → 仅自己 `ProjectMember` 项目下提案关联的工单；
- admin → 全量。

API 层固定传 `uid`（`_current_user(...).id`），端点对外永远是收敛视图。

### D3: 返回结构 = `_ser(ProposalTicketRequest)` + 附加 `project_id`

前端 hint 需要显示项目名（`projectName(project_id)`），而工单表无该列。`search_ticket_requests` 在 `join Proposal` 时同时取 `Proposal.project_id`，对每条结果做 `_ser(req)` 后附加 `project_id` 字段。该端点为新端点，返回结构可自由定义，不破坏任何既有契约。

### D4: 排序 = updated_at desc + id desc

工单有活跃的处理流（pending→processing→done/failed 刷新 updated_at），活跃工单优先；同时间戳按 id 倒序稳定。

### D5: 路由 = `/api/search/tickets`

与 `/api/tickets` 前缀无冲突；镜像 `search_proposals` 的实践。

### D6: 前端跳转 = `/proposals/{proposal_id}`

该路由已存在（问答工作台详情视图，含工单区）；`TicketRequestItem.proposal_id` 是原生字段，无需额外查询。

### D7: 分类标签 `.cat-ticket`

styles.css 追加 `.palette-item-cat.cat-ticket`（红系 `#ef4444`，命令面板色系中唯一红色，与其它九类区分）。

## 变更文件清单

| 文件 | 变更 |
|------|------|
| `agentboard/service.py` | 新增 `search_ticket_requests(s, q, limit=20, user_id=None)`（search_proposals 后） |
| `agentboard/api.py` | 新增 `GET /api/search/tickets`（search_proposals_api 后，带鉴权 + uid 收敛） |
| `frontend/src/app/api.service.ts` | 新增 `searchTicketRequests({q, limit})` |
| `frontend/src/app/app.ts` | `paletteTicketResults` 信号 + `paletteRunSearch` ticket 分支 + `paletteItems` 第 10 类合并 + open/close/短查询三处清空 + `PaletteCommand.category` 联合类型加 `'ticket'` |
| `frontend/src/app/app.html` | 分类标签三元链追加 `cmd.category === 'ticket' ? 'Ticket'` |
| `frontend/src/styles.css` | `.cat-ticket` 样式 |
| `tests/test_ticket_search.py` | 后端单测（service + API） |
| `tmp/e2e_v618_harness.py` | Playwright 自包含 E2E |

## 测试计划

1. **pytest**：service（title/type/提案标题匹配、可见性收敛 admin 全量/成员仅自己项目、project_id 附加字段、limit、无匹配）+ API（200 结构、401、q 必填 422、limit 上限 422、路由不冲突、端点并存）。
2. **E2E**：Ctrl+K → 唯一 token → `.cat-ticket` 结果 → 点击 → `/proposals/{id}` 详情渲染 → 0 pageerror/console/js·css 404。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 工单标题为空导致搜不到 | 搜索并入关联提案标题 `Proposal.title`（D1） |
| 可见性遗漏导致越权搜索 | 端点强制 `_current_user` + uid 收敛（镜像 search_proposals），单测覆盖成员/非成员双路径 |
| 返回结构缺 project_id 破坏前端 | D3 附加字段 + 单测断言 `project_id` 存在 |
| 与既有搜索端点混淆 | 独立路径 `/api/search/tickets` + 并存测试 |
