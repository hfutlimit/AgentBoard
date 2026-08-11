# Design: 命令面板接入 Agent 搜索（v6.16）

## 现状

| 实体 | 后端 | 前端信号 | 分类标签 | 鉴权 |
|------|------|---------|---------|------|
| 任务 | `/api/tasks?q=` | paletteTaskResults | cat-task | 视项目 |
| 项目 | 本地过滤 | paletteProjectResults | cat-project | — |
| Story | `/api/search/stories` | paletteStoryResults | cat-story | 无 |
| 文档 | `/api/documents?q=` | paletteDocumentResults | cat-document | 视项目 |
| Epic | `/api/search/epics` | paletteEpicResults | cat-epic | 无 |
| Sprint | `/api/search/sprints` | paletteSprintResults | cat-sprint | 无 |
| 通知 | `/api/search/notifications` | paletteNotificationResults | cat-notification | **需鉴权（按 user_id 隔离）** |
| **Agent（本次）** | **`/api/search/agents`** | **paletteAgentResults** | **cat-agent** | **需鉴权（镜像通知）** |

## 设计决策

### D1：鉴权策略 —— 镜像通知搜索（带鉴权）
- 通知搜索带鉴权是因为数据属用户私有；Agent 搜索无隐私数据，但 Agent 池视图本身（侧栏入口 + goAgents + loadAgents）依赖登录态。
- 为一致性采用 `_current_user(authorization, s, required_permission="api:read")`（镜像 search_notifications），未鉴权 401。
- 好处：与通知搜索同构，测试模式可复用；坏处：REQUIRE_AUTH=0 模式下调用方仍需携带 token，但前端总是已登录，无实际影响。

### D2：路由 —— `/api/search/agents` 避免与 `/api/agents/{agent_id}` 冲突
- FastAPI 路由匹配按注册顺序；`/api/agents/{agent_id}` 已存在（PUT/DELETE/heartbeat/deregister/probe），`/api/search/agents` 路径更长且字面量前缀不同，不会冲突。
- 放置位置：紧邻 `search_notifications_api`，与其余 `/api/search/*` 端点同区。

### D3：匹配字段与过滤
- 匹配 `agent_id` / `name` / `roles`（JSON 数组字符串，ilike 直接匹配开发者/reviewer 等角色 token）OR 组合。
- 过滤 `enabled=True`（已禁用 Agent 不参与搜索）。
- 排序 `id desc` + limit（默认 20，API 层 1-50 校验）。

### D4：前端数据流（镜像 v6.15 通知）
1. `api.service.ts` 新增 `searchAgents({q, limit})` → GET `/api/search/agents`（无本地缓存，镜像 searchNotifications）。
2. `app.ts`：
   - 新增 `paletteAgentResults = signal<PaletteCommand[]>([])`；
   - `openPalette()` / `closePalette()` / `paletteRunSearch` 短查询分支三处重置 `paletteAgentResults.set([])`；
   - `paletteRunSearch` 新增 agent 分支：`firstValueFrom(api.searchAgents({q, limit: 10}))` → map 为 `{id: 'agent-'+id, title: 'Agent '+agent_id+'：'+name, hint: 在线/离线 · probe_message, category: 'agent', keywords, run: () => goAgents()}`；
   - `paletteItems` computed 结果合并末尾追加 `...this.paletteAgentResults()`；
   - `PaletteCommand.category` 联合类型追加 `'agent'`。
3. `app.html`：分类标签三元链末尾追加 `cmd.category === 'agent' ? 'Agent'`。
4. `styles.css`：`.palette-item-cat.cat-agent` 紫色系（#9333ea，与 .cat-project 的 #7c3aed 区分层级）。

### D5：点击行为 —— 跳转 Agent 池视图
- Agent 池是内部视图（`view.set('agents')` + `goAgents()`），非深链路由。
- run 直接调 `goAgents()`（已含 connectAgentWs + loadAgents），与侧栏入口行为一致。

## 变更文件

| 文件 | 变更 |
|------|------|
| `agentboard/service.py` | +`search_agents()` |
| `agentboard/api.py` | +`GET /api/search/agents` |
| `frontend/src/app/api.service.ts` | +`searchAgents()` |
| `frontend/src/app/app.ts` | +信号/分支/合并/类型 |
| `frontend/src/app/app.html` | +分类标签 |
| `frontend/src/app/styles.css` | +`.cat-agent` |
| `tests/test_agent_search.py` | 新增（11 用例） |
| `frontend/src/app/app.spec.ts` | +3 用例 + 修复 pre-existing 类型错误 |

## 验收清单

- [ ] `pytest tests/test_agent_search.py` 全绿
- [ ] `ng test`（vitest）40 passed（含 Agent 3 用例）
- [ ] E2E：Ctrl+K → 关键词 → `.cat-agent` → 点击 → Agent 池视图 → 0 错误
- [ ] 零新增依赖；不触碰 18001
