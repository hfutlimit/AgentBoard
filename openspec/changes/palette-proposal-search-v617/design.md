# Design: 命令面板接入 Proposal 后端搜索（v6.17）

## 现状

- `service.list_proposals` 已具备可见性收敛语义：`user_id` 给定时，非 admin 仅搜索 `ProjectMember` 项目下的提案（镜像文档模块）。
- `service.search_agents`（v6.16）提供全局搜索模板：关键词 ilike OR 匹配 + `order_by id desc` + `limit`。
- `api.py` 的 `/api/search/{epics,sprints,notifications,agents}` 提供端点模板：q 必填 + limit 1-50 + `_current_user` 鉴权。
- 前端 `paletteAgentResults`（v6.16）提供信号/分支/合并/分类标签完整模板。

## 设计决策

### D1: 搜索字段 = title/content（与 `list_proposals` 的 q 分支一致）

`Proposal.title.ilike(like) OR Proposal.content.ilike(like)`，关键词语义与既有提案列表搜索一致；content 是提案正文（Markdown），用户常凭正文关键词回忆提案。

### D2: 可见性收敛 = 镜像 `list_proposals`（user_id 传入）

与其它实体搜索端点不同，Proposal 是项目级隐私数据（提案正文可能含业务细节），**必须**按调用者可见项目收敛：

- `user_id=None`（未登录/内部调用）→ 全量（与 `search_agents` 一致，供内部工具使用）；
- 非 admin → 仅 `ProjectMember` 项目下的提案；
- admin → 全量。

API 层固定传 `uid`（`_current_user(...).id`），因此端点对外永远是收敛视图。

### D3: 排序 = updated_at desc + id desc

提案有活跃的澄清流（用户作答/Worker 回写会刷新 updated_at），活跃提案优先展示；同时间戳按 id 倒序稳定。

### D4: 路由 = `/api/search/proposals`

与 `/api/proposals/{pid}` 无冲突（不同前缀），且路径字面量明确，镜像 `search_epics` 避开 `/api/epics/{eid}` 的既有实践。

### D5: 前端跳转 = `/proposals/{id}`

该路由已存在（`app.routes.ts`），问答工作台详情视图渲染提案 + 轮次问题；无需新增路由。

### D6: 分类标签 `.cat-proposal`

styles.css 追加 `.palette-item-cat.cat-proposal`（teal 系 `#0d9488`），与 `.cat-agent`（紫 `#9333ea`）、`.cat-document`（橙）区分。

## 变更文件清单

| 文件 | 变更 |
|------|------|
| `agentboard/service.py` | 新增 `search_proposals(s, q, limit=20, user_id=None)`（search_agents 后） |
| `agentboard/api.py` | 新增 `GET /api/search/proposals`（search_agents_api 后，带鉴权 + uid 收敛） |
| `frontend/src/app/api.service.ts` | 新增 `searchProposals({q, limit})` |
| `frontend/src/app/app.ts` | `paletteProposalResults` 信号 + `paletteRunSearch` proposal 分支 + `paletteItems` 第 9 类合并 + open/close/短查询三处清空 + `PaletteCommand.category` 联合类型加 `'proposal'` |
| `frontend/src/app/app.html` | 分类标签三元链追加 `cmd.category === 'proposal' ? 'Proposal'` |
| `frontend/src/styles.css` | `.cat-proposal` 样式 |
| `tests/test_proposal_search.py` | 后端单测（11 用例） |
| `frontend/src/app/app.spec.ts` | vitest 3 用例 |
| `tests/test_palette_proposal_e2e.py` | Playwright E2E |

## 测试计划

1. **pytest**：service（title/content 匹配、可见性收敛 admin 全量/成员仅自己项目、limit、无匹配）+ API（200 结构、401、q 必填 422、limit 上限 422、路由不冲突、端点并存）。
2. **vitest**：`paletteItems` 合并 category=proposal、`.cat-proposal → 'Proposal'` 渲染、open/close 清空。
3. **E2E**：Ctrl+K → 唯一 token → `.cat-proposal` 1 条 → 点击 → `/proposals/{id}` 详情渲染 → 0 pageerror/console/js·css 404。

## 风险与对策

| 风险 | 对策 |
|------|------|
| 提案正文含 HTML/敏感文本 | 搜索仅 ilike 匹配，不渲染；序列化 `_ser` 仅输出 DB 列 |
| 可见性遗漏导致越权搜索 | 端点强制 `_current_user` + uid 收敛（镜像 list_proposals），单测覆盖成员/非成员双路径 |
| 与既有搜索端点混淆 | 独立路径 `/api/search/proposals` + 并存测试 |
