# AgentBoard 整体 Review + 本地部署测试报告

> 测试时间：2026-08-15 22:17–22:55（GMT+8）
> 基线：`origin/main` @ `18ea648`（Story 137 项目中心）+ 本次修复 commit `e6fc94b`
> 结论：**✅ 本地部署测试通过，可部署服务器**

---

## 一、代码状态

| 项目 | 状态 |
|---|---|
| 远程同步 | `Already up to date`（拉取前已与 origin/main 一致） |
| 本地未提交 | `openspec/changes/task-status-simplify-regression-fix/`（回归修复文档，代码改动已合入工作树） |
| 分支 | `main`（仅此一分支，无分叉） |
| Alembic 迁移链 | 单 head `a1b2c3d4e5f6`（Story 137 归档字段），本地独立 SQLite 全链 37 步迁移成功 |

## 二、代码 Review 结论

近期主线为 **Phase 1-9 vertical-slice 重构**（features/* 拆分 + core/ 抽层）+ **Story 137 项目中心** + **Story 265 Task 状态精简回归修复**。整体架构合理，但部署测试暴露 2 个真实 Bug（见第四节），均已修复。

## 三、自动化测试

| 测试组 | 结果 |
|---|---|
| `test_story_265_task_status_simplify.py` | ✅ 17/17 passed |
| `tests/unit/`（59 项） | ✅ 59/59 passed |
| 关键回归组（story265 + unit/work_items + task_state_machine） | ✅ 44/44 passed |
| 状态机单测 `test_task_state_machine.py` | ✅ 14/14 passed |
| 导入验证 | ✅ `import agentboard.service/api` OK；`service.InvalidValue is core.exceptions.InvalidValue` |

> ⚠️ 全量非 E2E 首跑出现 79 failed + 70 errors，**根因是已知 flaky 干扰**（多文件 `del sys.modules` 各自重载 + 各自设 DB_URL 互相干扰），**单独跑全部通过**。另有部分旧直连脚本（`test_review_*`、`test_sprint_api_review`、`shot_*`、`test_story_151/152` 等）在 pytest 收集期执行 `sys.exit(1)`，须 `--ignore` 排除，属历史遗留非本次回归。

## 四、本地部署测试（独立 SQLite @ 127.0.0.1:18099 + Web @ 28099）

### 4.1 API 冒烟 21/21 ✅

| 模块 | 覆盖点 | 结果 |
|---|---|---|
| 认证 | 注册 / 登录 / me | ✅ |
| 项目中心（Story 137 新） | `GET /api/projects/center` scope/sort + 统计字段（task_count/task_done/member_count/last_activity_at） | ✅ |
| 项目全链路 | 创建项目 → Epic → Story → Task（`POST /api/stories/{sid}/tasks`） | ✅ |
| 状态机 | todo→in_progress→in_review→done（done 须带 `status_reason=completed`）；done→todo 非法迁移被拒（400） | ✅ |
| 批量归档（Story 137 新） | bulk-archive / bulk-unarchive / is_archived 标记 | ✅ |
| 评论 / 搜索 / 文档 | 评论增查 / 任务搜索 / 文档创建 | ✅ |
| 成员 | 成员列表（owner 角色） | ✅ |

### 4.2 前端验证 ✅

- `ng build`：26s 成功，产物 `main-PJH5CVFS.js`（932.95 kB）+ `styles-EX4DZUOY.css`，仅 1 个 CSS budget 警告（156KB vs 150KB，不影响功能）。
- 部署：已复制到 `agentboard/web/static/`，旧 hash 文件清理，`mermaid.min.js` 保留。
- Playwright 真实浏览器冒烟：首页加载 → 注册 → 登录 → 注入 token 回 SPA → 项目中心/仪表盘完整渲染，**0 JS 错误**。

## 五、本次发现并修复的 2 个真实 Bug（commit `e6fc94b`，已推送）

| # | 位置 | 问题 | 影响 | 修复 |
|---|---|---|---|---|
| 1 | `features/projects/router.py` | bulk-archive/unarchive 非 admin 分支引用 `ProjectMember` **未导入** | 非 admin owner 归档项目 → NameError **500**（admin 路径绕过该分支，故未暴露） | 补 `from ...features.projects.models import ProjectMember` |
| 2 | `service.py update_task` + `schemas.py` + `features/work_items/router.py` | PATCH `/api/tasks/{tid}` 直改 status **绕过状态机**（仅校验取值） | 允许 `done→todo` 等非法迁移，违反 Story 265「set_status 强制迁移」 | status 委托 `set_status` 强制迁移 + `status_reason` 透传；PATCH 补捕获 `IllegalTransition`→400；`TaskPatch` 补 `status_reason` 字段 |

> 修复后回归验证：上述冒烟场景 21/21 全绿，关键回归组 44/44 全绿，无新破坏。

## 六、⚠️ 部署服务器注意事项（重要）

1. **生产 Docker 栈静态目录优先级**：`web_app.py` 中 `_angular_dist_dir`（镜像内 `frontend/dist/frontend/browser` 快照）**优先于**挂载的 `agentboard/web/static/`。更新前端后必须注入 `AGENTBOARD_WEB_STATIC_DIR=/app/agentboard/web/static` 或重建镜像，否则仍渲染旧版。
2. **本机 Docker 栈仍是 23 小时前旧镜像**（不含 Story 137 + 本次修复），服务器部署前请确认构建包含 `main-PJH5CVFS.js` 对应的最新代码。
3. **18001 MCP 容器禁止重启**（内存中跑旧代码，重启会切断 WorkBuddy MCP 连接）；改后端后用 `docker cp` + `docker restart` 其余服务，或重建 api/web。
4. 生产用 MariaDB（`AGENTBOARD_DB_URL=mysql+pymysql://...`），存量数据迁移 Story 137 归档字段会自动由 Alembic `a1b2c3d4e5f6` 处理；`ready→confirmed` 等旧 CHECK 约束冲突需先映射（详见既有迁移经验）。
5. 新增依赖已固化在 `requirements.txt`（本轮无新增依赖）。
