# Proposal: Admin Portal 基础框架（Angular 21 子应用）

## 背景
项目 3 最高优先级 backlog 为 **admin-portal**（Epic 含 Story 71 前端实现 / Story 72 E2E，Task 850–861，整体为整站级）。其中 Task 850「初始化 admin-portal Angular 项目」、Task 851「实现登录页」、Task 856「样式与主题」可独立交付，作为 admin-portal 的第一个可验证增量。

## 目标
在现有 `frontend/` Angular 21 工作区内生成第二个应用 `admin-portal`，复用已安装的 node_modules（Angular 21），实现：
- 可独立构建/运行的 Angular 应用骨架（Task 850）
- 登录页：调用 `/api/auth/login`、存储 token、登录后跳转 dashboard（Task 851）
- 全局 premium 主题（light/dark 自适应）+ 登录/仪表盘布局（Task 856）
- 路由守卫保护 dashboard 路由

## 非目标
- 用户管理 / 项目管理 / 统计等具体业务模块（后续 Story）
- 后端契约变更（纯前端，零契约破坏）
- 生产部署拓扑（本次仅交付源码与本地验证）

## 为什么
admin-portal 是项目最高优先级真实 backlog。其整体不可在 1 小时内收尾，故按规则选取最小可独立交付增量，先落地「可运行骨架 + 登录鉴权」这一最关键基础。

## 风险
- 与现有 `frontend` 主应用共用工作区：`ng generate application` 已自动更新 `angular.json`/`tsconfig.json`，主应用构建不受影响（验证见回归）。
- 登录响应字段为 `token`（非 `access_token`），已按真实契约实现。
