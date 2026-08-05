## COS V5 签名四步算法（2026-08-06 Epic 64 S1，commit `ce48d59`）
- StringToSign = `sha1\n{KeyTime}\n{SHA1(HttpString)}\n`（**不是**直接对 HttpString 做 HMAC，初版易错）。
- SignKey = HMAC-SHA1(SecretKey, KeyTime)；Signature = HMAC-SHA1(SignKey, StringToSign)。
- HttpParameters / HttpHeaders 拼接：参数按 key **字典序**（`q-ak < q-header-list < q-key-time < q-sign-algorithm < q-sign-time < q-url-param-list`），值 URL 编码保留 `;/:=+,-_.~`。
- Authorization/URL 参数顺序不影响验证（服务端解析后重建），但与 cos-python-sdk-v5 SDK 行为一致用 sorted。
- `urllib.parse.parse_qs` 默认 `keep_blank_values=False` → 解析 `q-url-param-list=` 这种空值需加 `keep_blank_values=True`。
- `urllib.request.Request.headers.items()` 返回的 header 名是 `capitalize()` 结果（`content-type`→`Content-type`）。
- 纯标准库实现 7980 字节，**零第三方依赖**，SQLite/MariaDB/Windows IIS/dist 全环境免安装。

## Proposal 闭环（2026-08-04 P3 完成 Epic 96 收尾）
- 状态机：converged→story_created 终态；转化端点 `POST /api/proposals/{pid}/convert {epic_id, title?}` 走 service.convert_proposal_to_story。
- 幂等：story_id 已回填+Story 仍在 → 直接复用，呼应 P1 全量重放/P2 at-least-once。
- 子任务解析复用 `generate_tasks_from_spec` 同款正则 `r"\s*[-*]\s*\[\s*[ xX]\s*\]\s*(.*)"`；`- [x]` 已勾选项也生成任务。
- proposals 现在是**项目级 Tab**（`/project/{pid}/proposals`），非侧栏全局入口（旧 `#nav-proposals` 已移除）。
- Story 子任务列表端点分页：`GET /api/stories/{sid}/tasks` 返回 `{items,total}` 而非纯数组。

## 评论三实体（2026-08-05，commit `9e70415`）
- `comments` 表 task_id/story_id/epic_id 三者**恰好其一非空**；`task_id` 已由 NOT NULL 改可空（迁移 `n1o2p3q4r5s6`；SQLite 改列约束须 `batch_alter_table`）。
- 端点：`GET/POST /api/{tasks|stories|epics}/{id}/comments`；删除统一 `DELETE /api/comments/{cid}`。@提及通知抽 `_mention_notify`（api.py），link=`/task|/story|/epic/{id}`。
- service `create_comment`/`list_comments` 泛化为关键字参数（task_id=/story_id=/epic_id=，旧位置调用已全部迁移）；`get_comment_project_id` 按实体上溯；删 Story/Epic 级联清其评论。
- 前端 Story/Epic 详情面板评论区复用 `.comments-card`（styles.css 全局），模板变量用 `epAuthor/epContent/stAuthor/stContent`；发布后**只重载评论列表**，勿用 `run()`（会全量 refresh 重置 storyTab/epicTab）。

## E2E 模式
- 中文标题 sorted() 不稳定，用 set 比较。
- MCP 工具注册验证：`asyncio.run(mcp.list_tools())`（FastMCP 无 `_tool_manager._tools`）。

# AgentBoard 项目长期记忆（2026-08-03 精简版）

## 定位与架构
轻量项目管理 + OpenSpec。层级 Project→Epic→Story→Task/Bug。双后端 SQLite 调试 / MariaDB 生产（`AGENTBOARD_DB_URL`）。前端单体 `App` 组件（`frontend/src/app/app.{ts,html,css}`），standalone 无 NgModule，假路由（`view()` signal + `@switch`）。UI 弹窗统一走 `modal()`/`docModal`/`sprintModalOpen`，样式 `.modal-overlay`（styles.css 全局）。

## API / 状态机
- 任务状态 `PUT /api/tasks/{tid}/status` body `{"status":"..."}`。真实迁移表（service.py TRANSITIONS）：`BACKLOG→TODO`；`TODO→IN_PROGRESS/BACKLOG/DONE`；`IN_PROGRESS→IN_REVIEW/VERIFYING/TODO/DONE`；`IN_REVIEW→DONE/IN_PROGRESS`；`VERIFYING→DONE/IN_PROGRESS`；`DONE→IN_PROGRESS/TODO`。**无 in_review→verifying**（旧线性链是错的）。状态同步在 TRANSITIONS 上 BFS 求最短路径，勿硬编码。例外 A-22 快速完成：允许 TODO/IN_PROGRESS→DONE、DONE→TODO，IN_PROGRESS→BACKLOG 仍禁。
- Story/Epic 状态 `PATCH /api/{stories|epics}/{id}`；Story 创建 `POST /api/epics/{epicId}/stories`；`TaskIn` 必含 `project_id`；`TaskPatch` 含 `sprint_id`（可 null）。
- CORS：`require_business_auth` 对 401 手动注入 `Access-Control-Allow-Origin`（api.py L49-55）。middleware 早返回的 JSONResponse 须 `_apply_cors` 手动补头，否则前端报 `0 Unknown Error`。速率限制中间件已移除（commit `8036b1e`）。

## 访问控制（2026-07-20 加固）
- 仅 `REQUIRE_AUTH=1`（Docker 默认）生效；本地开放模式不强制。
- `project_access_middleware`（api.py）拦截所有 `/api` 项目级路由：私有项目仅成员/系统管理员可见，公开项目可读但写入需成员；项目根 PATCH/DELETE 需 owner/admin；**系统管理员 `is_admin` 全局绕过**。子资源→project 解析器在 service.py。`create_project` 支持 `is_private`（新建弹窗默认勾选）。
- 新增/修改项目级接口勿绕过中间件（已覆盖 epics/stories/tasks/sprints/schedules/webhooks/评论/附件/统计/导出）。`/api/admin/projects` 用系统管理员 token；`list_all_projects_admin` 曾因 `func` 未导入 500，改 `.count()`。
- API Key：`abk_` 经 `_current_user()` 解析为关联用户完整身份（权限=用户）。`/api/api-keys` CRUD 自助管理；`/api/admin/*` 走 `_require_admin()`（支持 Bearer + abk_）。MCP 用非管理员 key（`make-mcp-token.py` 建 `mcp-service` 用户）。

## 构建 / 部署（关键）
- 前端流程：`export PATH=<managed-node-22.22.2>:$PATH && npm run build` → cp `frontend/dist/frontend/browser/*` → `agentboard/web/static/`（勿用 `node.exe .bin/ng build`）。docker-compose.yml **web 绑定挂载 `./agentboard/web/static`、api 绑定挂载 `./agentboard`** → 改代码/静态只需 `docker compose restart api web`，无需重建镜像/docker cp。
- ⚠️ **部署致命坑（2026-08-01 实测）**：`agentboard/web/static` 是 git 跟踪构建产物，会滞后于 `frontend/` 源码。正确顺序：**git pull → npm run build → cp 到 static → docker compose restart web**。部署后 `curl :28080/` 确认 index.html 引用的 `main-*.js` hash 已变。static 含手动资源 `mermaid.min.js`（构建产物不含），覆盖拷贝勿删。
- ⚠️ Angular DevTools 红条：`app.config.ts` 的 `provideBrowserGlobalErrorListeners()` 应改为 `...(isDevMode() ? [...] : [])`，否则生产 + DevTools 扩展顶红条。
- 新增 Python 依赖：`docker compose exec api pip install <pkg>`（重启保留）；固化进镜像才 `docker compose build api`。
- Docker 运维：`com.docker.service` 停止时 `Start-Service` 失败，须启动 `Docker Desktop.exe`。`agentboard-rabbitmq`(28672/28673) 无 restart 策略，Docker 重启后被 kill 需 `docker start`。
- compose 必填 env（放本地 .env，未跟踪）：`AGENTBOARD_CORS_ORIGINS`、`MARIADB_PASSWORD`（须与既有库一致）、`MARIADB_ROOT_PASSWORD`、`AGENTBOARD_WEB_API_URL`。`angular.json` production 已设 `"optimization":{"fonts":false}`。

## Git / 协作
- **文档驱动**：需求 `docs/requirements.md`、任务 `docs/tasks.md`、变更 `openspec/changes/<id>/{proposal,design,tasks}.md`。
- **Git 硬规则**：`git add <本次文件>`（勿 add .）→ commit → **立即 `git push origin main`**；push 失败须提示用户本地重试，不得静默跳过。remote `ssh://git@ssh.github.com:443/hfutlimit/AgentBoard.git`（SSH over 443，认证 `Jzhong2026`）。

## MCP（关键坑）
- `MCP_REQUIRE_AUTH=0`（默认）→ 调用方 key 不生效，MCP 固定以服务端 `.env` `AGENTBOARD_MCP_TOKEN` 身份运行。身份错位修法=换目标用户 abk_ key。方案 B 需 `MCP_REQUIRE_AUTH=1` 且改 `AgentBoardTokenVerifier`（auth.py:82 只认 v1 登录 token）。
- `_proj_list`：先 `/api/auth/me` 判身份 → admin 全量，普通用户走 `/api/users/me/projects`。回归测试 `tests/test_admin_api_key_scope.py`。
- 连接器 `agentboard` 指向 `http://124.220.44.12/mcp`（远程生产 Windows `C:\AgentBoard`，NSSM）；本机 docker：api=18000 / web=28080 / MCP=18001 仅本地验证；改后端须生产重部署才生效。
- `mcp_server.py` 新增工具用 `_http(method,path)`（路径带 `/api`）；`set_status`/`update_task`/`update_story`/`update_epic` 走 `_task_status`/`_epic_update`/`_story_update` helper。并发锁 `.workbuddy/autodev.lock`（90min）。
- ⚠️ **18001 MCP 容器跑内存中旧代码**（`python -m agentboard.mcp_server` 启动后进程内存不变；`_api`→`_http` 15 处修复不重启不生效）。**自动化约束禁止重启 18001**（切断 WorkBuddy MCP 连接）→ 修复靠自包含测试验证（epic97：测试自己拉 uvicorn 子进程并指向 `mcp_server.API_URL`），容器重部署留独立运维窗口。`dist/agentboard-*/agentboard/mcp_server.py` 仍是带 bug 旧副本，重建 dist 须同步修复。

## 自动化任务经验
- **MCP 优先**：AgentBoard MCP 是进度唯一权威来源。`mcp__agentboard__set_status` 沙箱有序列化 bug → 改用 curl REST API 更新状态。
- MCP auth 不可用：`auth_login` 后 `list_projects` 仍 unauthorized。备选 `POST /api/auth/register` 创建 admin/admin123（id=54）用于 Playwright E2E。
- 多 DB：本地 uvicorn (58125) 用 `agentboard.db`（数据完整），Docker API (18000) 不同 DB。Playwright 测试用 8080 端口。
- **禁止触碰端口 18001**：WorkBuddy MCP 通信端口，任何 docker 操作不得影响。

## Playwright 验证经验
- 登录：`add_init_script` 注入 `localStorage.agentboard_token`；admin/admin123 可用。
- 本地 uvicorn 监听 58125，web_app.py 默认注入 58124 → `pg.route` 重写；Chromium 加 `--no-proxy-server`。
- 导航优先点侧栏 nav；`☰` 按 id（`#sidebar-toggle`/`#s-density-toggle`）。E2E 失败请求只计 js/css；`/api/*` ERR_ABORTED 良性。
- 侧栏整树预加载性能债（74 项目冷启动 ~20s）：E2E 先 `wait_for_function("!document.querySelector('.skeleton')", timeout=60000)`。
- venv：`C:\Users\jason\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（playwright 1.61.0）。
- Angular HttpClient PATCH Observable 不 emit（既有 bug）→ 前端用 `fetch()` 绕过。深链 goto 有 `tasks()` 信号竞态，必要时 retry/reload。

## 测试产物约定
- 测试脚本生成的截图/临时文件一律写 `tmp/`（根目录占位 `tmp/.gitkeep`）。`tmp/`、`screenshots/`、`tests/screenshots/`、`scripts/*.png` 已 gitignore，**禁止 `git add` 强追**。发现遗留先 git rm 再 commit。一次性调试脚本硬编码绝对路径改写为相对 `tmp/...`。

## 文档模块
`DOCUMENT_TYPES=['memory','plan','knowledge','design']`；signals `documents/docItem/docFilter*`；模板 `@else` 必须带 `@`。`createDocument/deleteDoc` 默认跳 `/documents`，项目 Tab 内就地更新。
- **文件夹（2026-08-03）**：`document_folders` 表（parent_id 自引用）+ `documents.folder_id`（FK SET NULL）。API `/api/document-folders` CRUD；`DocumentPatch.folder_id` 显式 null=移出根（用 `model_fields_set` 判断，`exclude_none` 吞 null）。删除文件夹=子项上提父级（不级联删）。前端面包屑即 drop 目标；拖拽 payload **三通道**：window `__agentboardDrag` → dataTransfer 自定义 MIME → 组件信号（drop 阶段 getData 不可靠）；`_docDropBusy` 防重入。拖拽 PATCH 在侧栏预加载风暴期间延迟 5-8s，networkidle 后 1.2s。

## Windows/IIS 生产
IIS(ARR) 反代 WebAPI:8000 / MCP:8001（NSSM），MariaDB，前端 IIS 托管（web.config 含 /api /mcp 反代 + SPA 回退）。打包 `scripts/package_windows.py`；两端 `AGENTBOARD_SECRET` 须一致。生产地址 `http://124.220.44.12/`。
