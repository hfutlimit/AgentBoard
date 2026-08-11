# Tasks: 命令面板接入 Proposal 后端搜索（v6.17）

## T1: 后端 service.search_proposals

- 位置：`agentboard/service.py`（`search_agents` 之后）；
- 签名：`search_proposals(s, q, limit=20, user_id=None)`；
- `Proposal.title/content` ilike OR 匹配；`order_by updated_at desc, id desc`；`limit`；
- 可见性收敛：`user_id` 给定且非 admin → 仅 `ProjectMember` 项目；admin/None 全量（镜像 `list_proposals`）。

## T2: 后端 GET /api/search/proposals

- 位置：`agentboard/api.py`（`search_agents_api` 之后）；
- `q: str = Query(..., min_length=1)` + `limit: int = Query(20, ge=1, le=50)`；
- `_current_user(authorization, s, required_permission="api:read").id` 作为 `user_id` 传入（强制收敛）；
- 返回 `[service._ser(x) for x in rows]`。

## T3: 前端 api.service.searchProposals

- `frontend/src/app/api.service.ts`：`searchProposals({q, limit})` → `GET /api/search/proposals` → `ProposalItem[]`（镜像 `searchAgents`）。

## T4: 前端 app.ts 信号与搜索分支

- `paletteProposalResults = signal<PaletteCommand[]>([])`（`paletteAgentResults` 后）；
- `paletteRunSearch` proposal 分支：title=`Proposal #${p.id}：${p.title}`、hint=`${projectName(p.project_id)} · ${proposalStatusLabel(p.status)}`、category='proposal'、keywords 含 id/title/content、run → `router.navigateByUrl('/proposals/' + p.id)`；
- `paletteItems` 合并追加第 9 类；openPalette/closePalette/短查询三处清空 `paletteProposalResults.set([])`；
- `PaletteCommand.category` 联合类型追加 `'proposal'`。

## T5: 前端 app.html 分类标签 + styles.css

- `app.html` 分类标签三元链追加 `cmd.category === 'proposal' ? 'Proposal'`；
- `styles.css` 追加 `.palette-item-cat.cat-proposal { color:#0d9488; ... }`。

## T6: 后端单测 tests/test_proposal_search.py

- 种子：admin（首个注册自动 admin）+ 普通 member；两项目（A 含 member、B 不含）；各建 1 提案（title 专属关键词 + content 均含 "proposal"）；
- 用例（11）：service title 匹配 / content 匹配 / admin 全量收敛 / member 仅自己项目 / limit 与无匹配；API 200 结构 / member 可见性 / 401 / q 必填 + limit 上限 / 路由不冲突 / 端点并存。

## T7: 前端 vitest app.spec.ts

- 3 用例：paletteItems 合并 category=proposal + 短查询清空；`.cat-proposal → 'Proposal'` 渲染；open/close 清空。

## T8: E2E tests/test_palette_proposal_e2e.py

- Ctrl+K → 唯一 token → `.cat-proposal` 1 条 → 点击 → `/proposals/{id}` 详情渲染 → 0 pageerror/console/js·css 404。

## T9: OpenSpec 三件套 + 状态流转

- `openspec/changes/palette-proposal-search-v617/{proposal,design,tasks}.md`；
- MCP：Task 1034 backlog→todo→in_progress→in_review；Story 245/Epic 125 同步。

## 验收

- pytest 11 passed；vitest 全绿；E2E 0 错误；
- 部署 api(restart) + web(cp dist + restart)，未触碰 18001；
- Task → in_review；Story/Epic 状态同步。
