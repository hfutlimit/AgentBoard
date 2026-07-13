# AgentBoard Code Review - 第 11 次执行

**时间**: 2026-07-13 14:43  
**审查任务数**: 6 个 (in_review) - 全部来自 Epic 16（前端持续优化轨道）

---

## 审查结果

| Task | 标题 | 结果 | 说明 |
|------|------|------|------|
| #233 | Task 16.1.1: Filter signals | ⚠️ in_progress | 源码无 filterStatus/filterPriority/filterType signals |
| #234 | Task 16.1.2: Filter UI panel | ⚠️ in_progress | 源码无 filter-panel/filter-bar 元素 |
| #235 | Task 16.1.3: Sort by priority/status/created | ⚠️ in_progress | 源码无 sortField/sortDirection |
| #236 | Task 16.2.1: Extend keyboard shortcuts | ✅ done | 源码确认 `handleTaskKeydown` j/k/Enter/Space/Esc |
| #237 | Task 16.2.2: Update keyboard hints | ✅ done | `.kbd-hint` 提示 "j/k 上下导航 · Enter 打开 · Space 选择 · Esc 取消" |
| #238 | Task 16.3.1: ARIA labels | ⚠️ in_progress | app.html 仅 2 处 aria 属性（logo + modal-dialog） |

## 测试方法

- **源码 grep 验证**：在 `frontend/src/app/app.ts` 和 `app.html` 中搜索 `filter*`/`sort*`/`aria-*` 关键词
- **Playwright 真实浏览器**：登录 → 进入 project/80 → DOM 检查元素存在性
- **API 调用**：`PUT /api/tasks/{id}/status` 更新状态
- **部署**：git pull 1a70f97..e84fe67（登录页重构），`docker compose restart web`（volume 挂载无需重建）

## 关键发现

1. **commit 1c84a1a 实现范围有限**：标题写的是"API缓存 + 批量 + 导出 + 键盘导航增强"，但**未实现** filter/sort/aria
   - ✅ 已实现：键盘导航 (handleTaskKeydown)、批量操作、API 缓存、CSV/JSON 导出
   - ❌ 未实现：filter signals、filter UI、sort by 任意字段、ARIA labels
2. **状态机细节**：`in_review → done` 允许直接跳转（已通过 #236/#237 验证）；`in_review → verifying` 不合法
3. **comments API 需 `author` 字段**：缺字段返回 422 ValidationError

## 后续建议（按依赖关系）

1. **#233 Filter signals** 先做：添加 3 个 signal + computed derived list
2. **#234 Filter UI panel** 依赖 #233：filter-bar 容器 + 6 status + 5 priority 多选 chip
3. **#235 Sort** 独立：sortField/sortDirection signals + sort 下拉
4. **#238 ARIA** 独立：但建议与 #236 键盘导航同步完成（focused task + aria-activedescendant）

## 统计

- 通过：2/6 (33%)
- 退回 in_progress：4/6 (67%)
- 提交 commit：1c84a1a (1c84a1ac0d6255e08fd19aae3155ebc612962b42)

---

## 审查结果

| Task | 标题 | 结果 | 说明 |
|------|------|------|------|
| #204 | API 健康指示器（顶栏状态徽章） | ✅ done | 源码确认：health-dot + popover 完整实现 |
| #205 | Sprint 燃尽图（Canvas/SVG） | ✅ done | CSS 柱状图 + burndown API 正常 |
| #206 | GET /api/health 健康检查端点 | ⚠️ in_progress | 缺 mcp 字段（spec 要求） |
| #207 | Dashboard Hero 条 | ✅ done | 源码确认：hero + stats 卡片完整 |
| #208 | API 速率限制中间件 | ✅ done | 429 + Retry-After 正常触发 |
| #209 | 任务卡片丰富化 | ✅ done | priority/sprint/comment 徽章完整 |
| #210 | MCP Sprint/Stats/Attachment 工具集 | ⚠️ in_progress | 缺 search_attachments MCP 工具 |

## 测试方法

- **后端任务** (#206, #208, #210)：直接 curl API 端点验证
- **前端任务** (#204, #205, #207, #209)：源码审查（Playwright UI 测试受阻于 SPA 限流）

## 发现的问题

1. **SPA 限流交互**：SPA 页面加载触发大量并行 API 请求（meta + projects + epics + stories + tasks），超过默认 60req/min 限制 → 建议提高默认值至 200+ 或添加 SPA 请求合并

## 统计

- in_review → done: 5
- in_review → in_progress: 2
- 当前: todo 66 | done 65 | backlog 49 | in_progress 7 | in_review 0
