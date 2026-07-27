# Tasks: Admin Portal E2E 测试骨架

## 实现
- [x] `scripts/serve_admin_portal.py`：`ThreadingHTTPServer`，静态托管 `frontend/dist/admin-portal/browser`，`/api` 反向代理到 `http://127.0.0.1:58125`；SPA 路由回退；静默访问日志
- [x] `tests/admin_portal/__init__.py`：包说明（运行方式）
- [x] `tests/admin_portal/_harness.py`：共享 `start_browser()` / `login_ui()` / `check_errors()` / `report()`
- [x] `tests/admin_portal/test_login_e2e.py`（Task 858）：从根级脚本迁入，改用 harness + 环境变量 `BASE`
- [x] `tests/admin_portal/test_users_projects_e2e.py`（Task 859+860）：迁入并复用 harness，保留 `is_admin` API 复核与还原
- [x] `tests/admin_portal/test_stats_e2e.py`（Task 861）：迁入并改用 harness；项目 3 任务总数断言放宽为正数（抗数据漂移）
- [x] `tests/admin_portal/run_all.py`：编排三用例，非零退出供流水线使用
- [x] 根级旧脚本 `test_admin_portal_{login,stats,users_projects}_e2e.py` 经 `git mv` 收拢至 `tests/admin_portal/`

## 验证
- [x] `ng build admin-portal --configuration development` 构建通过（login/stats/users/projects/dashboard 懒加载 chunk 正常产出）
- [x] `python tests/admin_portal/run_all.py` 全绿：
  - login PASS（卡片渲染 / 错误凭据告警 / 正确登录存 token 跳转 / 守卫拦截未登录 / 重新登录）
  - users_projects PASS（users=51 行 / projects=50 行；非管理员→管理员 UI+API 双复核；还原不污染）
  - stats PASS（5 汇总卡片 / 创建·完成双系列柱状图 / 日周月聚合切换 / 项目 3 下拉切换刷新）
  - 全程 0 pageerror / console error / .js+.css 404，无预期外 401
- [x] 回归：后端 `pytest test_epic30_cache.py` 8 passed（零后端 / 前端源码改动）

## 状态流转（REST 合法链 backlog→todo→in_progress→in_review）
- [x] Task 857（搭建 Playwright 测试骨架）→ in_review
- [x] Task 858（登录页 E2E）→ in_review
- [x] Task 859（用户管理 E2E）→ in_review
- [x] Task 860（项目管理 E2E）→ in_review
- [x] Task 861（统计页 E2E）→ in_review
- [x] Story 72（Playwright 自动化测试）→ in_review
- [x] Epic 32（独立 Angular 管理后台 Admin Portal）→ in_review
