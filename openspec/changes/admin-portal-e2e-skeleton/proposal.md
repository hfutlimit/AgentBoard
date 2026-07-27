# Proposal: Admin Portal E2E 测试骨架（Story 72 Playwright 自动化）

## 背景
admin-portal（Epic 32 / Story 71 前端实现）已完成登录 / 用户管理 / 项目管理 / 统计四页实现，
Story 72「Playwright 自动化测试」含 Task 857–861（搭建骨架 / 登录页 / 用户管理 / 项目管理 / 统计页 E2E）。
此前四页已有散落的根级 E2E 脚本（硬编码 `BASE=http://127.0.0.1:4300`、依赖手动 `ng serve`），
缺乏统一、可独立运行的基础设施，不符合「骨架」定位。

## 目标
将 admin-portal E2E 收拢为可独立运行、零手工依赖的测试骨架：
- `scripts/serve_admin_portal.py`：静态托管 `frontend/dist/admin-portal/browser` 并反向代理 `/api → 58125`（同源，无 CORS 干扰）
- `tests/admin_portal/`：共享 `_harness.py`（浏览器装配 / 登录 / 错误-401 采集）+ `run_all.py` 编排器 + 四个用例（login / users_projects / stats）
- 用例 `BASE` 改为读取 `ADMIN_PORTAL_URL` 环境变量（默认 `http://127.0.0.1:4321`），脱离 `ng serve` 硬编码

## 非目标
- 改动任何前端 / 后端业务代码（纯测试基础设施，零契约破坏）
- 新增业务断言维度（复用既有覆盖：登录鉴权 / 用户权限切换 / 项目列表 / 统计聚合）

## 为什么
Story 72 是 admin-portal 最高优先级真实 backlog 的收尾一环。以「骨架」形式固化 E2E 基础设施，
使后续每次自动开发都能一键 `serve + run_all` 验证 admin-portal，符合 OpenSpec 驱动与可持续验收要求。
