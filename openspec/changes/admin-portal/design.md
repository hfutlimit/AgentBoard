# 设计：独立 Angular 管理后台（Admin Portal）

> 对应 Epic 38 / Story 1：方案设计（到方法粒度）。

## 1. 技术选型

| 维度 | 选择 | 备选 | 决策理由 |
|------|------|------|----------|
| 框架 | Angular 21（与主 SPA 一致） | React/Vue/Svelte | 统一技术栈，降低心智负担；复用现有 `models.ts`、认证逻辑与构建经验。 |
| 构建工具 | Angular CLI (`@angular/build:application`) | Vite/Webpack 自定义 | Angular CLI 与仓库内 `frontend` 一致，无需额外调研；独立 `angular.json` 可控。 |
| 状态管理 | Angular Signals（内置） | NgRx / Akita | 后台页面数据流简单，Signals 足够；减少依赖与样板代码。 |
| HTTP 客户端 | Angular `HttpClient` | `fetch` 直接调用 | 与主 SPA 一致，Interceptors 可统一注入 Token。 |
| 样式 | 纯 CSS + CSS 变量 | Tailwind / Bootstrap | 避免引入新依赖；便于与主 SPA 的设计 Token 对齐或独立演进。 |
| 图表 | 纯 CSS/SVG 自绘 | Chart.js / ECharts | 新增 Story/Task 统计只需柱状图/折线，自绘足够轻量，避免引入图表库。 |
| 测试 | Playwright + pytest | Cypress / Vitest 单元测试 | 与现有 `tests/test_playwright_e2e.py` 保持一致；E2E 覆盖真实登录与管理操作。 |

## 2. 设计思路

需求可拆为三条主线：

1. **入口清理**：从主 SPA 移除「管理员后台」菜单，避免用户两套入口的混淆。
2. **独立应用**：新建 `admin-portal`，拥有自己的路由、组件、服务、构建产物，与主 SPA 通过 REST API 通信。
3. **功能复用**：后台所需的「用户列表/修改」「项目列表/删除」「统计」均能在现有 API 中找到或组合得到，优先复用，必要时再新增最小端点。

## 3. 架构设计

```
AgentBoard
├── frontend/              # 主 SPA（移除 Admin 入口）
│   └── src/app/app.html   # 删除「管理员后台」链接
├── admin-portal/          # 新独立 Angular 应用
│   ├── angular.json
│   ├── package.json
│   ├── src/
│   │   ├── main.ts
│   │   ├── index.html
│   │   ├── styles.css
│   │   ├── app/
│   │   │   ├── app.config.ts
│   │   │   ├── app.routes.ts
│   │   │   ├── admin.guard.ts
│   │   │   ├── models.ts        # 复刻/导入共享模型
│   │   │   ├── services/
│   │   │   │   ├── auth.service.ts
│   │   │   │   ├── admin.service.ts
│   │   │   │   └── stats.service.ts
│   │   │   └── components/
│   │   │       ├── login/
│   │   │       ├── layout/
│   │   │       ├── dashboard/
│   │   │       ├── user-list/
│   │   │       ├── project-list/
│   │   │       └── statistics/
│   └── dist/              # 构建产物
├── agentboard/
│   ├── api.py             # 复用 /api/admin/* 与 /api/projects/*/stats
│   └── web_app.py         # 托管主 SPA（不变）
├── scripts/
│   └── build-admin-portal.ps1  # 构建脚本
└── tests/
    └── test_admin_portal_e2e.py
```

## 4. 开发细节（到方法粒度）

### 4.1 主 SPA 入口移除

修改文件：`frontend/src/app/app.html`。

删除内容：
```html
@if (isAdmin()) {
  <a routerLink="/admin" class="user-dropdown-item" (click)="showUserMenu.set(false)">🛡 管理员后台</a>
}
```

保留 `isAdmin` signal 与 `adminMe`/`loadAdminData`/`setAdmin`/`adminDeleteProject` 方法（虽然模板不再使用，但 Story 2 完成前保留，避免破坏 `/admin` 路由；Story 2 完成后可彻底删除）。

### 4.2 初始化 admin-portal Angular 项目

使用仓库内已安装的 managed Node.js 22.22.2：

```bash
cd E:\Projects\WorkBuddy\AgentBoard
C:\Users\jason\.workbuddy\binaries\node\versions\22.22.2\node.exe \
  C:\Users\jason\.workbuddy\binaries\node\versions\22.22.2\node_modules\@angular\cli\bin\ng.js \
  new admin-portal --routing --style=css --standalone --skip-git --package-manager=npm
```

> 注意：不可通过 `node_modules/.bin/ng` 直接运行，因为该 wrapper 是 shell 脚本，在 Windows 上会导致解析错误（见仓库记忆）。应使用 `npm run build` 或调用 `.bin/ng.js`。

生成后调整：
- `package.json` 中 Angular 版本与 `frontend` 对齐：`^21.2.0`。
- `angular.json` 中 output path 改为 `dist/admin-portal`。
- `index.html` 中 `__API_URL__` 占位符，由 `web_app.py` 或独立静态服务注入。

### 4.3 共享模型策略

由于两个 Angular 项目独立，直接跨项目导入 TypeScript 路径配置复杂。采用**复制+同步**策略：

- 在 `admin-portal/src/app/models.ts` 中复制主 SPA 所需的类型（`UserProfile`、`Project`、`PagedResult`、`ApiErrorBody` 等）。
- 当后端契约变化时，两个文件同步更新；本次不新增字段，因此一次复制即可。

### 4.4 认证服务（AuthService）

文件：`admin-portal/src/app/services/auth.service.ts`

```typescript
export class AuthService {
  private readonly http = inject(HttpClient);
  readonly baseUrl = window.AGENTBOARD_API || 'http://127.0.0.1:8000';
  readonly token = signal<string | null>(localStorage.getItem('admin_portal_token'));
  readonly currentUser = signal<UserProfile | null>(null);

  login(username: string, password: string): Observable<AuthResult> { ... }
  logout(): void { ... }
  me(): Observable<UserProfile> { ... }
  isAdmin(): boolean { ... }
  isAuthenticated(): boolean { ... }
}
```

- `login` 调用 `POST /api/auth/login`，成功后写入 `localStorage.admin_portal_token`。
- `logout` 清除 token 并跳转 `/login`。
- `me` 调用 `GET /api/auth/me` 校验管理员身份。
- 使用 `HttpClient` Interceptor（`auth.interceptor.ts`）自动注入 `Authorization: Bearer <token>`。

### 4.5 管理员服务（AdminService）

文件：`admin-portal/src/app/services/admin.service.ts`

```typescript
export class AdminService {
  private readonly api = inject(ApiService);

  listUsers(params?: { limit?: number; offset?: number; search?: string }): Observable<PagedResult<UserAdminRow>> { ... }
  setUserAdmin(userId: number, isAdmin: boolean): Observable<UserAdminRow> { ... }
  listProjects(params?: { limit?: number; offset?: number; search?: string }): Observable<PagedResult<ProjectAdminRow>> { ... }
  deleteProject(projectId: number): Observable<{ ok: boolean }> { ... }
}
```

- `listUsers` 调用 `GET /api/admin/users`。
- `setUserAdmin` 调用 `PATCH /api/admin/users/{id}`，body `{ is_admin: boolean }`。
- `listProjects` 调用 `GET /api/admin/projects`。
- `deleteProject` 调用 `DELETE /api/admin/projects/{id}`。

### 4.6 统计服务（StatsService）

文件：`admin-portal/src/app/services/stats.service.ts`

```typescript
export class StatsService {
  private readonly api = inject(ApiService);

  // 获取全部项目的基础统计（ProjectStats）
  getProjectStats(projectId: number): Observable<ProjectStats> { ... }

  // 聚合所有项目的新增 Story / Task
  aggregateNewItems(mode: 'day' | 'week' | 'month'): Observable<StatPoint[]> { ... }
}
```

实现策略：
- 先 `GET /api/projects` 拿到所有项目 ID。
- 对每个项目 `GET /api/projects/{id}/stats` 获取 `daily_created` / `daily_done`。
- 在内存按 `mode` 聚合到时间桶，返回 `{ bucket: string; stories: number; tasks: number }[]`。

> 性能考量：项目数较多时 N+1 请求不可接受。若后续性能不足，再新增 `GET /api/admin/stats?mode=day|week|month` 后端聚合接口，本次保持最小后端改动。

### 4.7 路由与权限守卫

文件：`admin-portal/src/app/app.routes.ts`

```typescript
export const routes: Routes = [
  { path: 'login', component: LoginComponent },
  { path: '', component: LayoutComponent, canActivate: [adminGuard], children: [
      { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
      { path: 'dashboard', component: DashboardComponent },
      { path: 'users', component: UserListComponent },
      { path: 'projects', component: ProjectListComponent },
      { path: 'statistics', component: StatisticsComponent },
  ]},
  { path: '**', redirectTo: 'login' },
];
```

文件：`admin-portal/src/app/admin.guard.ts`

```typescript
export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (!auth.isAuthenticated()) {
    router.navigate(['/login']);
    return false;
  }
  // 异步校验管理员身份
  return auth.me().pipe(
    map(u => {
      if (u.is_admin) return true;
      router.navigate(['/login']);
      return false;
    }),
    catchError(() => {
      router.navigate(['/login']);
      return of(false);
    })
  );
};
```

### 4.8 组件设计

#### LoginComponent
- 模板：`login/login.html`
- 表单：`username`（input）、`password`（input type="password"）、提交按钮。
- 方法：`submit()`：调用 `authService.login()`，成功后 `router.navigate(['/'])`；失败显示错误。

#### LayoutComponent
- 侧边栏导航：Dashboard、Users、Projects、Statistics、Logout。
- 顶部显示当前管理员用户名。
- 主内容区 `<router-outlet />`。

#### DashboardComponent
- 卡片：总用户数、总项目数、今日新增 Story 数、今日新增 Task 数。
- 方法：`loadDashboard()`：并发调用 `adminService.listUsers({ limit: 1 })`、`adminService.listProjects({ limit: 1 })`、`statsService.aggregateNewItems('day')`。

#### UserListComponent
- 表格列：ID、用户名、角色、注册时间、操作。
- 操作按钮：设为管理员 / 撤销管理员。
- 分页：Limit 20，Offset 递增。
- 搜索：前端按用户名过滤（或后端参数化）。
- 方法：`loadUsers()`、`toggleAdmin(user)`、`onPageChange(page)`、`onSearch(query)`。

#### ProjectListComponent
- 表格列：ID、名称、短码、私有、成员数、创建时间、操作。
- 操作按钮：查看、删除（确认对话框）。
- 方法：`loadProjects()`、`deleteProject(project)`。

#### StatisticsComponent
- 时间维度切换：日 / 周 / 月。
- 图表：两条 SVG 折线（Stories / Tasks），下方表格展示数据。
- 方法：`loadStatistics(mode)`、`formatBucket(date, mode)`、`renderChart(points)`。

### 4.9 样式与主题

- 新建 `admin-portal/src/styles.css`。
- 使用 CSS 变量定义 design tokens：
  - `--admin-bg`、`--admin-surface`、`--admin-border`、`--admin-text`、`--admin-primary`、`--admin-danger`。
- 初始版本与主 SPA light theme 保持色调一致，但布局更紧凑（后台风格）。
- 暗色模式作为可选项（v2），首轮不强制实现。

### 4.10 构建与部署

#### 独立构建

```bash
cd admin-portal
npm run build
```

产物：`admin-portal/dist/admin-portal/browser/index.html`、CSS、JS。

#### 部署方案（推荐方案 A：同站子路径）

修改 `agentboard/web_app.py`：

```python
ADMIN_STATIC_DIR = Path(__file__).parent / "web" / "admin"
app.mount("/admin-portal", StaticFiles(directory=str(ADMIN_STATIC_DIR)), name="admin-portal")

@app.get("/admin-portal/{path:path}")
def admin_portal_route(path: str):
    ...
```

构建脚本 `scripts/build-admin-portal.ps1`：

```powershell
$node = "C:\Users\jason\.workbuddy\binaries\node\versions\22.22.2\node.exe"
Push-Location admin-portal
& $node (Resolve-Path ..\frontend\node_modules\@angular\cli\bin\ng.js) build --configuration production
Pop-Location
Copy-Item -Recurse -Force admin-portal\dist\admin-portal\browser\* agentboard\web\admin\
```

> 备选方案 B：独立端口服务。创建 `admin_web_app.py` 单独监听，适合未来拆分到独立域名。当前推荐 A，减少运维端口。

### 4.11 API 复用清单

| 功能 | 已存在 API | 是否需要新增 |
|------|------------|--------------|
| 登录 | `POST /api/auth/login` | 否 |
| 获取当前用户 | `GET /api/auth/me` | 否 |
| 用户列表 | `GET /api/admin/users` | 否 |
| 设置管理员 | `PATCH /api/admin/users/{id}` | 否 |
| 项目列表 | `GET /api/admin/projects` | 否 |
| 删除项目 | `DELETE /api/admin/projects/{id}` | 否 |
| 项目统计 | `GET /api/projects/{id}/stats` | 否（首轮） |
| 全局聚合统计 | — | 可选（`GET /api/admin/stats`） |

## 5. 问题与解决

| 问题 | 解决方案 |
|------|----------|
| 独立 Angular 项目与 `frontend` 共享 Node 模块 | 使用 managed Node 全局 workspace 或单独 `npm install`。推荐在 `admin-portal` 内独立安装，避免版本冲突。 |
| 构建命令在 Windows 出错 | 不使用 `node_modules/.bin/ng`（shell wrapper），使用 `npm run build` 或 `.bin/ng.js`。 |
| 统计 N+1 请求性能问题 | 首轮使用内存聚合；项目/数据量增大后新增 `/api/admin/stats` 后端聚合。 |
| 主 SPA `/admin` 路由仍残留 | Story 1 仅移除入口；Story 2 完成后删除 `admin` ViewKind、`loadAdminData` 等代码，并移除 `app.routes.ts` 中 `{ path: 'admin', component: RouteAnchor }`。 |
| 两个应用 Token 作用域 | 使用不同 `localStorage` key（`admin_portal_token` vs `agentboard_token`），避免互相覆盖。 |

## 6. 方案对比

| 方案 | 优点 | 缺点 | 结论 |
|------|------|------|------|
| A. 在主 SPA 内继续扩展 Admin | 无需新项目，改动小 | 巨型组件继续膨胀，权限与视觉边界模糊 | 否 |
| B. 独立 Angular 应用，同站子路径 | 架构清晰，复用后端，部署简单 | 需要额外构建步骤 | **推荐** |
| C. 独立 Angular 应用，独立端口 | 完全隔离，可独立部署 | 多一个服务端口，运维成本增加 | 可选未来升级 |

## 7. 验证清单

- [ ] `frontend/src/app/app.html` 中无「管理员后台」链接。
- [ ] `admin-portal` 可 `npm run build` 成功。
- [ ] 登录页成功/失败分支正常。
- [ ] 用户管理页：设置/撤销管理员后列表刷新。
- [ ] 项目管理页：删除项目后列表刷新。
- [ ] 统计页：切换日/周/月，图表与表格更新。
- [ ] Playwright E2E 用例全部通过。
- [ ] 后端 API 契约未变。

## 8. 后续任务索引

- Story 1 任务已录入 AgentBoard MCP（Epic 38）。
- Story 2（页面开发）与 Story 3（Playwright）待本方案 review 后启动。
