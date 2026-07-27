# Design: 命令面板接入 Story/文档后端搜索（v5.7）

## 架构概览
沿用 v5.6「命令面板后端搜索」的信号 + computed 合并模式，将搜索域从「任务/项目」扩展到「任务/项目/Story/文档」四类实体：

```
paletteRunSearch(q)
  ├─ 项目：客户端过滤 projects()/recentProjects() 池（无后端请求）
  ├─ 任务：GET /api/tasks?q=             → paletteTaskResults
  ├─ Story：GET /api/search/stories?q=  → paletteStoryResults   ← 新增
  └─ 文档：GET /api/documents?q=        → paletteDocumentResults ← 新增

paletteItems (computed)
  results = [...task, ...project, ...story, ...document]
  if (staticMatches.length) return [...static, ...results]   // 命令优先
  return results
```

## 关键设计决策
1. **Story 搜索端点路径**：FastAPI 路由 `/api/stories/{sid}` 会吞掉 `/api/stories/search`，故新端点命名为 `/api/search/stories`（与 `/api/tasks/search` 风格一致但避开冲突）。
2. **SearchStories 缓存**：复用 `apiCache.getWithTTL`（30s），与 `searchTasks` 保持一致，降低高频输入下的后端压力。
3. **文档搜索复用现有能力**：`/api/documents?q=` 已支持关键词搜索与权限过滤（admin 可见全量），前端直接调用既有 `listDocuments({ q })`，无需新增后端代码。
4. **分类色体系**：`.cat-story` 用青色（#0891b2）、`.cat-document` 用橙色（#d97706），与既有 `.cat-task`（蓝）/`.cat-project`（紫）形成可区分的视觉层次，全部复用 `--surface-2` 等主题变量，自动适配 dark 主题。
5. **命令优先排序**：保留 v5.6 行为——当查询命中静态命令关键词时，命令项排在后端实体结果之前，确保 Enter 默认执行命令的既有交互不变。

## 状态机 / 数据流
- 纯前端 + 只读 GET，不修改任何写操作契约。
- 信号在 `openPalette`/`closePalette` 中重置，避免跨次搜索串味。
- 后端 `search_stories` 仅做 ilike 模糊匹配 + 按 id 倒序 + limit 截断，无事务写。

## 部署路径
- 后端：`agentboard/api.py` + `service.py` 编辑后，本地 uvicorn 58125 重启；docker `agentboard-api-1` 因 `./agentboard` 只读 bind-mount 已可见改动，`docker restart` 即可加载（不触碰 18001 MCP 端口）。
- 前端：`npm run build` → `cp frontend/dist/frontend/browser/* agentboard/web/static/`，web 8090（验证）/ 28080（docker）即时生效。
