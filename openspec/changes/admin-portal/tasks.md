# 任务清单：独立 Angular 管理后台（Admin Portal）

> 本任务清单已在 AgentBoard MCP（project 3）中创建 Epic 38 及下属 Story / Task，此处为索引与设计摘要。MCP 为唯一真相源。

## MCP 索引

- **Epic 38**：独立 Angular 管理后台（Admin Portal）
- **Story 1**：方案设计（到方法粒度）
- **Story 2**：完成独立 Angular 后台页面开发
- **Story 3**：Playwright 自动化测试

## Story 1 任务

1. 移除主 SPA 的「管理员后台」入口（`frontend/src/app/app.html`）。
2. 设计 `admin-portal` 项目结构与独立构建方案。
3. 设计 API 复用方案（复用 `/api/admin/*` 与 `/api/projects/{id}/stats`）。
4. 设计组件与路由（Login、Layout、Dashboard、UserList、ProjectList、Statistics、AdminGuard）。
5. 设计状态管理（Angular Signals）。
6. 设计构建与部署（同站 `/admin-portal` 子路径）。
7. 编写设计方案文档（`openspec/changes/admin-portal/{proposal,design,tasks}.md`）。

## Story 2 任务

1. 初始化 `admin-portal` Angular 项目（standalone、routing、CSS）。
2. 实现 `LoginComponent`（表单验证、Token 存储、错误提示）。
3. 实现 `AuthService` 与 `auth.interceptor`（自动注入 Bearer Token）。
4. 实现 `LayoutComponent`（侧边栏导航、退出）。
5. 实现 `AdminGuard`（非管理员重定向）。
6. 实现 `UserListComponent`（表格、分页、搜索、设为/撤销管理员）。
7. 实现 `ProjectListComponent`（表格、分页、搜索、删除确认）。
8. 实现 `StatisticsComponent`（日/周/月切换、SVG 图表、数据表格）。
9. 实现 `DashboardComponent`（关键指标卡片）。
10. 编写样式 `styles.css`（后台主题 Tokens）。
11. 编写构建脚本 `scripts/build-admin-portal.ps1`。
12. 修改 `agentboard/web_app.py` 挂载 `/admin-portal` 静态资源。

## Story 3 任务

1. 搭建 Playwright 测试骨架（`tests/test_admin_portal_e2e.py`）。
2. 登录页 E2E（成功/失败）。
3. 用户管理 E2E（搜索、设置管理员、撤销管理员）。
4. 项目管理 E2E（搜索、删除项目确认）。
5. 统计页 E2E（切换时间维度、断言图表/表格数据）。
6. 集成到 CI 运行。

## 依赖关系

```
Story 1 (方案设计)
  ├── 移除主 SPA 入口（可立即执行）
  ├── 项目/路由/组件设计
  ├── API 复用设计
  ├── 状态管理设计
  └── 构建部署设计
Story 2 (页面开发)
  ├── 依赖 Story 1 全部设计
  └── 输出可运行 Admin Portal
Story 3 (测试)
  ├── 依赖 Story 2 完成
  └── 输出 Playwright E2E
```
