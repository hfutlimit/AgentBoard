# Tasks: 命令面板接入 Ticket 后端搜索（v6.18）

## 任务清单

### 1. 后端 service：`search_ticket_requests`
- [x] 在 `agentboard/service.py`（search_proposals 后）新增 `search_ticket_requests(s, q, limit=20, user_id=None)`
- [x] join `Proposal`（`Proposal.id == ProposalTicketRequest.proposal_id`），过滤 `title / type / Proposal.title` ilike OR
- [x] 可见性收敛镜像 `search_proposals`：非 admin 仅 `ProjectMember` 项目，admin/None 全量
- [x] 排序 `ProposalTicketRequest.updated_at desc, id desc`，limit 截断
- [x] 返回 `list[dict]`：`_ser(req)` + 附加 `project_id`（经提案反查）

### 2. 后端 API：`GET /api/search/tickets`
- [x] `agentboard/api.py`（search_proposals_api 后）新增端点
- [x] `q` 必填（min_length=1）、`limit` 1-50、`_current_user(...)` 鉴权 + uid 收敛
- [x] 路由 `/api/search/tickets` 不与既有端点冲突

### 3. 前端
- [x] `api.service.ts`：`searchTicketRequests({q, limit})` → `GET /api/search/tickets`
- [x] `app.ts`：`paletteTicketResults` 信号 + `paletteRunSearch` ticket 分支（hint 用 `projectName(project_id)` + 工单类型/状态标签）+ `paletteItems` 第 10 类合并 + open/close/短查询三处清空 + `PaletteCommand.category` 加 `'ticket'`
- [x] `app.html`：分类标签三元链追加 `cmd.category === 'ticket' ? 'Ticket'`
- [x] `styles.css`：`.palette-item-cat.cat-ticket`（红系 `#ef4444`）

### 4. 测试
- [x] `tests/test_ticket_search.py`：service（title/type/提案标题匹配、可见性 admin/成员、project_id、limit、无匹配）+ API（200、401、q 必填 422、limit 上限 422、路由不冲突、端点并存）
- [x] Playwright E2E：自包含栈（uvicorn + 静态注入 + 覆写 API URL），Ctrl+K → token → `.cat-ticket` → 跳转 `/proposals/{id}` → 0 报错

### 5. 验收
- [x] 单测全绿；回归无失败
- [x] MCP 状态流转：Task → in_review；Story → in_review；Epic → in_review
- [x] 提交 + push
