# 变更提案：独立 Angular 管理后台（Admin Portal）

## 背景

当前 AgentBoard 主 SPA（`frontend/src/app/app.html`）的顶栏用户下拉菜单中提供了一个「管理员后台」入口，点击后路由到 `/admin`，由主应用 `App` 组件内嵌渲染用户管理、项目管理两个数据表格。该实现存在以下问题：

1. **耦合**：管理后台与主应用共享同一个 `app.ts` 巨型组件，Admin 相关逻辑、信号、模板全部堆在 `App` 中，随功能增加会进一步膨胀。
2. **权限边界模糊**：主应用普通用户也能通过直接输入 `/admin` 访问，虽然后端做了 `admin only` 拦截，但前端路由与体验不统一。
3. **视觉与交互定位冲突**：主应用模仿 Jira 的项目管理交互，管理后台需要更密集的数据表格、统计图表，混在一起难以维护两套视觉语言。

## 目标

1. 从主 SPA 中移除「管理员后台」入口。
2. 新建独立的 Angular 应用 `admin-portal`，与 `frontend` 同仓库但独立构建、独立部署。
3. 在独立后台中提供：
   - **用户管理**：查看所有用户、设置/撤销管理员权限。
   - **项目管理**：查看所有项目、删除项目（带确认）。
   - **统计**：新增 Story / Task 按日/周/月聚合展示。
4. 复用现有 REST API，不修改后端业务契约（`models.py`/`api.py`）。
5. 提供 Playwright E2E 测试覆盖登录、用户管理、项目管理、统计页面。

## 非目标

- 不替换或重构主 SPA 的现有功能（除移除入口）。
- 不引入新的后端框架或数据库表。
- 不做复杂的实时数据推送（WebSocket/SSE）。
- 不改动用户认证与鉴权机制（仍使用现有 JWT Bearer Token）。

## 范围

- **前端**：新建 `admin-portal/` 目录；修改 `frontend/src/app/app.html` 移除入口。
- **后端**：仅复用现有接口；如统计需求现有 API 无法满足，再评估是否新增 `/api/admin/stats`。
- **测试**：新增 `tests/test_admin_portal_e2e.py`。
- **文档**：新增 `openspec/changes/admin-portal/{proposal,design,tasks}.md`。

## 影响

- 主 SPA 包体积略微减小（移除 Admin 模板与相关方法未来可逐步清理）。
- 新增独立构建产物，CI 需增加 `admin-portal` 的构建步骤。
- 部署方式从「一个 Web 服务」变为「主 SPA + Admin Portal」两个静态站点（或同站不同路径）。

## 退出标准

1. 主 SPA 用户下拉菜单不再出现「管理员后台」。
2. `admin-portal` 可独立 `npm run build` 并通过 Playwright 测试。
3. 用户管理、项目管理、统计页面在真实浏览器中可运行。
4. 后端 API 契约未变。
5. 相关 Epic/Story/Task 在 AgentBoard MCP 中状态更新为 done。
