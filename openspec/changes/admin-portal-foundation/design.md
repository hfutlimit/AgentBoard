# Design: Admin Portal 基础框架

## 架构
- 在 `frontend/` Angular 21 单一工作区中新增第二个 `application` 类型项目 `admin-portal`（命令：`ng generate application admin-portal --routing --style=css --ssr=false --skip-tests`）。
- 复用工作区已安装的 Angular 21 依赖，无需额外 `npm install`。
- 构建产物：`frontend/dist/admin-portal`；dev server：`ng serve admin-portal --port 4300`。

## 目录结构
```
frontend/projects/admin-portal/
├── src/
│   ├── index.html
│   ├── styles.css            # 全局主题 (Task 856)
│   ├── main.ts
│   └── app/
│       ├── app.ts/html/css    # 根组件 (router-outlet)
│       ├── app.config.ts      # provideRouter + provideHttpClient
│       ├── app.routes.ts      # login / dashboard / ** 路由
│       ├── auth.guard.ts      # 函数式路由守卫 (localStorage token)
│       ├── api.service.ts     # 登录 / me，注入 Authorization
│       ├── login/             # 登录页 (Task 851)
│       └── dashboard/         # 仪表盘占位 (受守卫保护)
└── proxy.conf.json            # dev: /api -> 58125
```

## 关键设计决策
- **鉴权字段**：后端 `/api/auth/login` 返回 `{id, username, is_admin, token}`，前端存储 `token` 到 `localStorage['admin_portal_token']`（首要修复点：初版误用 `access_token` 导致 `me()` 401）。
- **路由守卫**：`authGuard: CanActivateFn` 检查 token 存在，否则 `Router.createUrlTree(['/login'])` 重定向。
- **懒加载**：login / dashboard 均 `loadComponent` 懒加载，减小首包。
- **主题**：CSS 变量 + `prefers-color-scheme` 适配 light/dark，品牌渐变 `#6366f1→#8b5cf6`，与主 SPA 视觉一致。
- **API 代理**：dev 下 `/api` 经 Vite dev-server 代理到 `127.0.0.1:58125`（同源绕过 CORS）。

## 接口契约（零变更，仅消费）
- `POST /api/auth/login {username,password}` → `{token, ...}`
- `GET /api/auth/me`（Bearer token）→ `{id, username, is_admin}`
