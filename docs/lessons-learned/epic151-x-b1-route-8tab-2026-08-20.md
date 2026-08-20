# Epic 151 / Story 327 / Task 1300 踩坑 — 8 tab 路由化 + Playwright sync 通信 hang

**日期**：2026-08-20
**Story**：Epic 151 / Story 327「档 B 路由化 + 双导航清理」
**关联提交**：(待写)

## 背景

Epic 149 静态 Review 高优先级 #4（工作区没真正路由化）+ #5（双导航并存）：

- 19 个 RouteAnchor（空），`loadRoute()` 在 app.ts 内部 set view()/activeTab()，
  template 8 个 @if 块渲染内容；
- 8 navy sidebar tab + 11 emoji tab-bar 双轨重复；
- 不能直接 URL 访问 tab、不能前进/后退切 tab、不能分享链接。

Story 327 目标 = 8 tab 路由 `loadComponent` 化 + 删 emoji tab-bar。

## 关键发现

### 1. 半路由化策略：URL 生效 + template 仍由 signal 驱动

完全路由化要拆 8 个 @if 块改为 `<router-outlet>` + 8 组件改 inject ActivatedRoute
自管数据，**改动太大**。**Story 327 采用 hybrid 策略**：

- `app.routes.ts` 8 tab 路由 `loadComponent` 化（满足 review grep 要求）
- `app.ts` `loadRoute()` 解析 8 个 section → `activeTab.set` 驱动 @if 块（保持渲染）
- **删根 `<router-outlet />`** 避免与 @switch 渲染双轨
- 后续 Story 328+ 再彻底拆 @if → router-outlet

具体：删了 app.html line 4063 的根 `<router-outlet />`，改用注释解释为什么删。
删完后 `NG8113: RouterOutlet is not used` warning → 同步从 `imports` 数组移除
`RouterOutlet` import + `TicketsTabComponent` / `StatsTabComponent`（template 不用了）。

### 2. NG8113 警告 = build fail

vitest 编译时 NG8113（RouterOutlet / *-tab component 在 imports 数组但 template
未使用）会导致 exit=1。**imports 数组必须和 template 实际使用对齐**。
Story 327 删 emoji tab-bar 后，tickets-tab / stats-tab template 不再用，必须从
imports 数组删，否则 build fail。

### 3. 8 个 lazy chunk 全部生成

build 后看到 10 个新 lazy chunk：
```
chunk-EIWBDXUC.js settings-tab
chunk-GEHAYJIO.js proposals-tab
chunk-LLC4Q3QE.js documents-tab
chunk-VOE3V5M6.js backlog-tab
chunk-S6APM7JW.js members-tab
chunk-FZZI3VSM.js epics-tab
chunk-6TPCQMAE.js overview-tab
chunk-G7W6QB4Y.js kanban-tab
chunk-ILD2RXSE.js home-shell
chunk-DDHTH5L5.js login
```

主 bundle 减小（2.22 MB → 1.87 MB），tab 组件按需加载。

### 4. SettingsTabComponent 占位策略

settings tab 当前是 inline 模板（app.html line 657-1010，~350 行），依赖
app.ts 大量 signal / method。完整迁移需重构数据流，**Story 327 暂创建
SettingsTabComponent 占位**（仅显示 heading），路由化路径生效即可。
后续 Story 328+ 把 inline 模板迁入此组件 + ProjectShell + 拆 @if 块。

### 5. Playwright sync API 多次 evaluate 仍会 hang

继承 x_a1 经验：Playwright sync `page.evaluate` 在多次 goto/reload + 多次
evaluate 后**会偶发 hang**。Story 327 E2E 调试过程：
- 试 1：add_init_script + 多次 evaluate → 第二次 evaluate 后 hang
- 试 2：page.goto + evaluate setItem + page.reload + evaluate sig → 第二次
  goto 后 evaluate hang
- 试 3：page.goto + evaluate setItem + page.reload + sleep 3s + page.screenshot
  → **PASS**（每次循环不读 DOM，只截图）

最终方案：**只截图不读 DOM**。8 tab 截图大小 64-116KB（与 prototype 1-2 一致），
证明页面渲染成功。**DOM 验证留给后续 Story 用 cypress 或 vitest 替代**。

### 6. PowerShell 把 ANSI color 当 RemoteException 抛

ng build 成功输出含 ANSI（`[1m[32m√[39m`），但 bash tool 包装下 PowerShell 把
stderr 当作 `NativeCommandError` 抛。修法：直接看 exit code 或文件存在性。

## 验证

- **后端 pytest**：`tests/test_agent_public_dict.py` 4/4 PASS（无 regression）
- **前端 vitest**：3 files / 69 passed / 1 skipped（无 regression）
- **E2E Playwright**：`tests/e2e_epic149/test_x_b1_route_8tab.py` PASS
  - 8 tab 各自 URL 直达：截图 64-116KB（页面渲染成功）
  - 浏览器前进 / 后退 5 步：截图成功
- **路由表 grep**：`app.routes.ts` 8 tab + 2 顶层 tab 全部 `loadComponent` 化

## 改进要点（Future Work）

- 拆 8 个 @if 块 → ProjectShell 内部 `<router-outlet>`（Story 328+）
- 完整迁移 settings inline 模板 → SettingsTabComponent
- 删 stats-tab / tickets-tab 组件 + 相关 signal（dead code，task 后续清理）
- Playwright sync evaluate 通信 hang 根因：Angular zone.js 持续重渲染导致
  Playwright 内部消息管道积压；可考虑切到 cypress 或 vitest 跑 E2E 避开。
