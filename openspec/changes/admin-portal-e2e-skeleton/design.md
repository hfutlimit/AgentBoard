# Design: Admin Portal E2E 测试骨架

## 架构
```
scripts/serve_admin_portal.py        # 静态 + /api 反向代理 (ThreadingHTTPServer)
        │  同源代理 -> 浏览器 /api 请求直达 58125，无 CORS
        ▼
frontend/dist/admin-portal/browser   # ng build 产物（相对路径 /api）
        ▼
tests/admin_portal/
  ├── __init__.py     # 包说明
  ├── _harness.py      # 共享：start_browser() / login_ui() / check_errors() / report()
  ├── test_login_e2e.py        # Task 858：登录渲染 / 错误凭据告警 / 守卫拦截
  ├── test_users_projects_e2e.py # Task 859+860：用户权限切换(API复核) / 项目列表
  ├── test_stats_e2e.py        # Task 861：汇总卡片 / 双系列柱状图 / 日周月聚合 / 项目切换
  └── run_all.py       # 依次运行三用例，任一失败非零退出
```

## 关键决策
1. **同源代理而非 CORS**：serve 脚本把 `/api` 代理到 `http://127.0.0.1:58125`，
   浏览器视其为同源请求，规避前端独立 origin 访问 API 的 CORS 预检失败。
2. **SPA 回退**：非 `/api` 且无扩展名的路径（如 `/users`）回退到 `index.html`，保证路由可刷新生效。
3. **错误采集口径统一**：`_harness.check_errors` 过滤 `ERR_ABORTED`（导航中断）与 `favicon` 资源失败等良性噪声；
   登录页预期的 1 次 `/api/auth/login` 401 单独放行。
4. **API 复核用直连**：`test_users_projects_e2e.py` 用 `requests` 直连 58125 复核 `is_admin` 翻转，
   与浏览器 UI 断言形成双校验，且测试末还原权限不污染数据。

## 运行
```bash
python scripts/serve_admin_portal.py --port 4321 &          # 后台启动静态+代理
ADMIN_PORTAL_URL=http://127.0.0.1:4321 \
  python tests/admin_portal/run_all.py                    # 运行全部 E2E
```
