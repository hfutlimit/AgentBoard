# AgentBoard Angular Frontend

Angular 21 LTS 前端，使用 standalone components、signals、Angular Router 与类型化 `HttpClient` 服务。

```bash
npm ci
npm start       # 开发服务器
npm run build   # 输出到 dist/frontend/browser
npm test
```

本地 Node 需满足 Angular 21 的兼容范围（Node 20.19+、22.12+ 或 24）。仓库 Dockerfile 使用 Node 22 构建，因此 `docker compose up --build` 不依赖宿主机 Node。

运行时 API 地址由 `agentboard.web_app` 将 `AGENTBOARD_API_URL` 注入 `window.AGENTBOARD_API`。
