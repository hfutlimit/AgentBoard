# Design — Dashboard 首页加载性能优化（跨项目聚合统计端点）

epic: 117
story: 223
task: 995

## 现状（问题定位）

`frontend/src/app/app.ts` `loadDashboard()`（约 L1809）：

```ts
const allEpics   = (await Promise.all(projects.map(p => api.listEpics(p.id)))).flat();
this.epics.set(allEpics);
const allStories = (await Promise.all(allEpics.map(e => api.listStories(e.id)))).flat();
this.stories.set(allStories);
const allTasks   = (await Promise.all(allStories.map(st => api.listTasks(st.id)))).flat();
this.tasks.set(allTasks);
```

- 三级依赖串行：Epics 全量完成 → Stories 全量 → Tasks 全量；
- 请求数 = 项目数 + Epic 数 + Story 数（远程生产数百个）；
- 首页统计卡 / 状态环 / 活动图 / 项目进度全部依赖最终 `tasks()` 全量；
- 因此骨架屏 `loading` 一直挂到整树拉完。

## 目标设计

### 后端：`GET /api/overview`（新端点，零表变更）

`agentboard/service.py` 新增 `get_overview(s, user_id)`：

```jsonc
{
  "counts": { "projects": 2, "epics": 3, "stories": 4, "tasks": 6, "done_tasks": 3 },
  "projects": [ { "id": 1, "name": "...", "total": 5, "done": 2, "percent": 40 }, ... ],  // total 降序
  "status_distribution": [ { "status": "backlog", "count": 1 }, ... ],  // 含 0，按 ALL_STATUSES 顺序
  "activity_7d": [ { "day": "2026-08-06", "count": 2 }, ... ]          // 近 7 天含 0，按日升序
}
```

实现要点：

1. 可见性复用 `list_accessible_projects(s, user_id)`：admin 全量 / 普通用户成员项目 / 未登录空。
2. 聚合用条件 SQL（`func.count` + `group_by`），单次往返取状态分布与项目进度；
3. `activity_7d` 按 `updated_at` 日分组，`(now - 6d .. now)` 窗口补 0；
4. `api.py` 新增 `@app.get("/api/overview")`：`_optional_user_id` 解析身份后透传，
   鉴权由 `require_business_auth` + `project_access_middleware` 整体把关（本端点非项目级路由）。

### 前端：两阶段渲染

`app.ts`：

1. 新增信号 `overviewStats = signal<OverviewStats | null>(null)`（模型在 `models.ts`）；
2. `loadDashboard()` 改为：
   - 阶段一 `await firstValueFrom(api.getOverview())` → `overviewStats.set(overview)`；
   - 阶段二 `void this.loadDashboardFullTree(generation)` 后台填充全局信号；
3. 统计卡数值改读 `statProjects/statEpics/statStories/statTasks/doneTasks`
   （overview 优先、整树回退）；`dashboardStatusChart / dashboardProjectProgress / dashboardActivity`
   三个 computed 同样 overview 优先；
4. `api.service.ts` 新增 `getOverview()`（15s 短 TTL 缓存，写操作随 stats 失效）。

模板 `app.html`：hero 计数与 stats-row 五张统计卡改用 `stat*` computed；
图表区（status donut / project progress / activity）模板不变，仅 computed 数据源切换。

## 兼容性

- `overviewStats` 为 `null` 时全部 computed 回退旧逻辑 → 后端缺失 / 请求失败时行为与现状一致；
- 全局 `tasks/epics/stories` 信号仍由后台整树填充 → 搜索、看板、跳转、Story 视图零影响；
- Story 视图的 `tasks()` 写入保护（`view() !== 'story'`）保留。

## 测试

- 后端 `tests/test_overview.py`：结构契约 / 可见性（admin vs member vs 匿名）/ 口径一致性 / API 直调；
- 前端 `app.spec.ts`：overview 存在时统计卡与图表取 overview 数据；
- Playwright E2E：登录 → 首页秒出（骨架屏提前消失）→ 统计卡/图表渲染 → 0 控制台错误 / 0 404。
