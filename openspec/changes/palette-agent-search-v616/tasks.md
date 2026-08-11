# Tasks: 命令面板接入 Agent 搜索（v6.16）

## 状态

- [x] service.search_agents：agent_id/name/roles OR ilike + enabled 过滤 + id desc + limit
- [x] api.py `GET /api/search/agents`：q 必填（min_length=1）+ limit（1-50）+ 鉴权 `_current_user(api:read)`
- [x] api.service.ts `searchAgents({q, limit})`
- [x] app.ts：paletteAgentResults 信号 + paletteRunSearch agent 分支 + paletteItems 合并 + 三处重置 + category 联合类型
- [x] app.html 分类标签 `agent → 'Agent'`
- [x] styles.css `.palette-item-cat.cat-agent`（#9333ea 紫色系）
- [x] tests/test_agent_search.py（11 用例）
- [x] app.spec.ts 新增 3 用例 + 修复 pre-existing 类型错误（Story.needs_design ×4、ProposalItem.ticket_type/ticket_id）
- [x] E2E tests/test_palette_agent_e2e.py

## 验收记录

- pytest tests/test_agent_search.py：11 passed
- vitest（ng test）：40 passed / 0 failed（含 Agent 3 用例）
- E2E：见测试文件
- 零新增依赖；未触碰 18001

## 部署

- api：docker cp + restart（或自包含栈验证）
- web：npm run build → cp dist → restart
