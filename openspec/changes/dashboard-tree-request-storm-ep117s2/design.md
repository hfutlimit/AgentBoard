# Design — 首页整树加载请求风暴治理（Epic 117 S2）

## 现状（改造前）

```ts
private async loadDashboardFullTree(generation: number): Promise<void> {
  try {
    const allEpics = (await Promise.all(
      this.projects().map((p) => firstValueFrom(this.api.listEpics(p.id))),
    )).flat();
    // ...generation 检查
    this.epics.set(allEpics);
    const allStories = (await Promise.all(
      allEpics.map((e) => firstValueFrom(this.api.listStories(e.id))),
    )).flat();
    this.stories.set(allStories);
    const allTasks = (await Promise.all(
      allStories.map((s) => firstValueFrom(this.api.listTasks(s.id))),
    )).flat();
    if (this.view() !== 'story') this.tasks.set(allTasks);
  } catch { /* 整体失败全部丢弃 */ }
}
```

问题：
1. Task 级：`allStories.map(listTasks)` 并发发起 S 个请求（S = Story 数，生产数百）→ 请求风暴主源；
2. 各级 `Promise.all` 全量并发，瞬时峰值 = max(P, E, S)；
3. 任一项失败 → 整体 reject → 该级全部结果丢弃（脆弱）。

## 改造后

```ts
/** 并发受限 map：同时最多 limit 个任务在跑；单项失败跳过，结果按输入顺序保留成功项 */
private async parallelMap<T, R>(items: T[], limit: number, fn: (item: T) => Promise<R>): Promise<R[]> {
  const results: (R | undefined)[] = new Array(items.length);
  let idx = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (idx < items.length) {
      const i = idx++;
      try { results[i] = await fn(items[i]); } catch { /* 单项失败跳过 */ }
    }
  });
  await Promise.all(workers);
  return results.filter((r): r is R => r !== undefined);
}

private async loadDashboardFullTree(generation: number): Promise<void> {
  try {
    const overviewOk = this.overviewStats() !== null;   // 阶段一成功标志
    const allEpics = (await this.parallelMap(this.projects(), 6, (p) =>
      firstValueFrom(this.api.listEpics(p.id)))).flat();
    if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
    this.epics.set(allEpics);
    const allStories = (await this.parallelMap(allEpics, 6, (e) =>
      firstValueFrom(this.api.listStories(e.id)))).flat();
    if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
    this.stories.set(allStories);
    // overview 已成功 → 统计卡/图表由 overview 驱动，Task 级全量仅作回退/预热 → 跳过（请求量最大一级）
    if (overviewOk) return;
    const allTasks = (await this.parallelMap(allStories, 6, (s) =>
      firstValueFrom(this.api.listTasks(s.id)))).flat();
    if (generation !== this.routeLoadGeneration || this.view() !== 'home') return;
    if (this.view() !== 'story') this.tasks.set(allTasks);
  } catch { /* 整树失败不影响已渲染首屏 */ }
}
```

## 关键决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| Task 级是否保留 | overview 成功时跳过，失败时保留 | overview 成功时首页无 Task 展示需求；各视图（项目/Story/任务详情）均有独立加载路径 |
| 并发上限 | 6 | 默认浏览器同域 HTTP/1.1 连接池 ≈ 6，避免排队与服务器瞬时压力；足够快（百级请求秒级完成） |
| 失败处理 | 单项跳过、保留成功项 | 比整体 reject 更健壮；Epic 列表页本就允许局部缺失 |
| 回退保底 | overview 失败 → 全量加载不变 | 图表/统计 computed 在 overview null 时回退 tasks()，契约不变 |

## 数据流

```
首页路由 → loadDashboard
  ├─ 阶段一: GET /api/overview → overviewStats ✓（秒出，驱动统计卡/图表）
  └─ 阶段二(后台): loadDashboardFullTree
       ├─ overviewOk = true  → epics(分片≤6) → stories(分片≤6) → [跳过 tasks]
       └─ overviewOk = false → epics → stories → tasks（全量回退，分片≤6）
项目页 /story/{id} /task/{id} → 各自独立加载路径（不受本次改动影响）
```

## 测试策略

- 前端单测（app.spec.ts）：mock ApiService（getOverview/listEpics/listStories/listTasks spy）：
  - overview 成功 → listTasks 零调用；epics/stories 信号被填充；
  - overview 失败 → listTasks 被调用（回退）；
  - parallelMap 并发峰值 ≤ limit（延迟 spy 计数）。
- Playwright E2E：首页秒出 + 统计卡正确 + 网络层 `/api/stories/{id}/tasks` 请求数为 0 + 项目页/Story 页回归 + 0 console/pageerror/js·css 失败。
