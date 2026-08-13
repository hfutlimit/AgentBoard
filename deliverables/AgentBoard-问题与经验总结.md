# AgentBoard 项目问题与经验总结

> 整理自 2026-07-10 ~ 2026-08-12 共 33 份自动开发日志与长期记忆。
> 目的：把高频踩坑、根因、修复方案与可复用经验固化，避免重复踩雷。
> 同步沉淀位置：`.workbuddy/memory/MEMORY.md`（「问题与经验」章节）。

---

## 一、构建与部署（最高频、最隐蔽）

| # | 问题 | 根因 | 经验 / 修复 |
|---|------|------|------------|
| D1 | 更新前端后 curl `:28080` 仍是旧 hash | `web_app.py` 的 `_angular_dist_dir`（镜像 Dockerfile 打包的 `frontend/dist` 快照）**优先于**挂载的 `agentboard/web/static` | dev compose 的 web 须注入 `AGENTBOARD_WEB_STATIC_DIR=/app/agentboard/web/static`；生产 compose/Dockerfile 无此 env → 更新前端必须**重建镜像**或注入同 env |
| D2 | 改动后端代码后 API 行为未变 | 生产 compose **无源码挂载**，api 跑镜像快照 | `docker cp agentboard/. agentboard-api-1:/app/agentboard/ && docker restart agentboard-api-1`（`docker compose restart` 不会更新代码）；注意**勿触碰 18001 MCP 容器** |
| D3 | 迁移链脱节导致 `/api/search/stories` 500（`Unknown column stories.in_kanban`） | 本地 alembic 版本 `y7z8a9b0c1d2` 不在当前链，看板迁移 `z0a1b2c3d4e5` 未应用 | 远程生产正常；本地迁移链单独排期修复 |
| D4 | 部署了"中间版本"前端（渲染 `[object Object]`/`#undefined`） | 第二次 `npm run build` 早于后端模型结构修正完成就 cp + restart | 必须确保构建产物含最终修正再 cp + restart web；部署后 curl 确认 `main-*.js` hash 变更 |
| D5 | Windows/IIS 部署护栏失败（test_epic98 6 failed） | `dist/` 是 8/6 旧快照，与源码 70 处差异 | 部署前必须 `python scripts/package_windows.py` 现打（dist 已 gitignore，勿依赖 git 快照） |
| D6 | proposal 澄清 / ticket 生成卡 pending | worker 无生产部署单元（docker-compose 仅 api/web/mcp/db），无 run-worker 脚本 | 须在 compose 加 worker 服务或用 NSSM 注册 `run-worker.ps1` |
| D7 | `cp` 进 static 产生残留 `static/browser/` | 误把 `browser/` 整目录 cp 进 static | 正确：`cp -r frontend/dist/frontend/browser/. agentboard/web/static/` |
| D8 | `web/static` 构建产物滞后源码 | static 是 git 跟踪产物 | 顺序：`git pull → npm run build → cp static → docker compose restart web`；`mermaid.min.js` 为手动资源勿删 |

**流程经验**：git 跟踪的 static 仅保留 6 个部署必需文件（index/main-*.js/styles-*.css/mermaid.min.js），严禁 pattern ignore `main-*.js`（会误伤新产物），只能定期物理清理旧残留。

---

## 二、数据库迁移与状态机（数据破坏性）

| # | 问题 | 根因 | 经验 / 修复 |
|---|------|------|------------|
| M1 | MariaDB 存量数据迁 CHECK 约束冲突 | `ready→confirmed` 违反旧 9 值 CHECK（confirmed 不在旧约束内） | 统一映射 `pending_review/ready → todo`（目标值须在旧 CHECK 内）；**先 `UPDATE` 再重建约束** |
| M2 | `PATCH /epics/{id}` 设为 `blocked` 报 IntegrityError | `ck_epics_status` **不含 blocked**，但 `_check_status` 接受 | 潜在缺陷：Epic 状态机与 CHECK 不一致，需补 blocked 或限制 PATCH |
| M3 | MariaDB 写入 1406（行超长） | 状态列 `String(40)`，`design_pending_review` 21 字符超 `VARCHAR(20)` | 状态列宽需 ≥ 实际最长枚举 |
| M4 | Story 状态流转 400 拒绝 | 2026-08-09 起 Story 状态机演进：`backlog→confirmed→todo→in_progress→in_review`，**confirmed 为必经中间态**，backlog 不能直接→todo/in_review | 逐级迁移；`needs_design=true`（默认）的 Story 下 task 不能 `todo→in_progress`（须过设计流） |
| M5 | SQLite 改列失败 | SQLite 不支持 `ALTER COLUMN` | 须用 `batch_alter_table` |

**状态机事实**：仅 Task/Bug 有强制迁移（`TRANSITIONS`+`set_status`）；Epic/Story PATCH 只校验取值不校验迁移；`blocked` 全向可达并记录 `previous_status`；Bug/Task 同表同状态机仅 `type` 区分。

---

## 三、MCP 与自动化（身份与热更新陷阱）

| # | 问题 | 根因 | 经验 / 修复 |
|---|------|------|------------|
| C1 | MCP 调用身份错位（换了目标用户） | `MCP_REQUIRE_AUTH=0`（默认）→ 调用方 key 不生效，MCP 固定以服务端 `.env` `AGENTBOARD_MCP_TOKEN` 运行 | 身份错位 = 换目标用户 `abk_` key；MCP 用非管理员 key（`make-mcp-token.py` 建 `mcp-service`） |
| C2 | 改 MCP 代码不生效 | 18001 容器跑**内存中旧代码**，进程启动后不重载 | **自动化约束禁止重启 18001**（会切断 WorkBuddy MCP 连接）；改动靠自包含测试验证，重部署留独立运维窗口 |
| C3 | `mcp__agentboard__set_status` 沙箱序列化 bug | FastMCP 序列化问题 | 用 curl REST 更新状态（2026-08-06 起可直接用，异常走 REST） |
| C4 | 生产 worker reclaim/ticket 流程 403 断流 | worker 须 **admin token**（全局端点仅 admin 可访问）；非 admin key 403 | 生产 `AGENTBOARD_WORKER_TOKEN` 必须是 admin 服务账号 |
| C5 | 直连生产 MCP 端点响应乱码 | 响应无 charset，requests 默认 ISO-8859-1 破坏 UTF-8 JSON | `resp.content.decode('utf-8')`；直连 `http://124.220.44.12/mcp` 走 Streamable HTTP（initialize→tools/call），无需 token |
| C6 | 重试计数丢失 | 重试计数编码进 error 文本，worker 每次 `mark_failed` 覆盖 error | 改独立字段 `auto_retry_count`（迁移 `w4x5y6z7a8b9`） |

**新增 MCP 工具规范**：用 `_http(method,path)`（路径带 `/api`）；注册验证 `asyncio.run(mcp.list_tools())`（FastMCP 3.x 无 `_tool_manager`）；须设 `AGENTBOARD_MCP_TOKEN`+`AGENTBOARD_API_URL`。

---

## 四、前端与 E2E（测试假通过最坑）

| # | 问题 | 根因 | 经验 / 修复 |
|---|------|------|------------|
| F1 | E2E "假通过" | `page.goto` 整页刷新销毁 JS 定时器（轮询/路由守卫） | 必须用 **SPA 内 UI 点击导航**（面包屑→列表→行），不能整页刷新 |
| F2 | 打开任意 Proposal 被生成中提案抢回 | `startTicketPolling` 3s 轮询回调无条件覆盖全局 `proposalItem`，导航离开不停止 | `loadRoute()` 开头 `stopTicketPolling()` + 轮询回调路由守卫（URL 非 `/proposals/{id}` 即停） |
| F3 | 既有 flaky 测试（events=[]） | 多测试文件顶层 `del sys.modules` 各自重载 agentboard + 各自设 `AGENTBOARD_DB_URL` 互相干扰 | 单独跑全绿；结构断言替代跨文件 DB 内容断言；属测试基建缺陷，留独立窗口修 |
| F4 | 前端类型对齐后端失败（渲染 undefined） | `rounds` 是 `{avg_story_round, avg_task_round}` 对象非数字；`by_reviewer` 字段为 user_id/name/... | 前端 models.ts 须对齐后端真实结构 |
| F5 | Angular DevTools 红条 | `provideBrowserGlobalErrorListeners()` 在 prod 误启用 | `...(isDevMode() ? [...] : [])` |
| F6 | CORS 拦截（127.0.0.1 被拒） | 白名单只含 `localhost:28080` | E2E 脚本 host 用 `localhost` 而非 `127.0.0.1` |

**E2E 基础设施**：登录 `add_init_script` 注入 `localStorage.agentboard_token`（admin/admin123）；侧栏骨架屏先 `wait_for_function("!document.querySelector('.skeleton')")`；`/api/*` ERR_ABORTED 良性；测试产物写 `tmp/`（gitignore）；仓库 `*_e2e.py` 硬编码 `127.0.0.1:8090` **不可直接套用**（本机 docker 28080/18000）。

---

## 五、网络 / 端口 / 环境

| # | 问题 | 根因 | 经验 / 修复 |
|---|------|------|------------|
| N1 | localhost 打到旧实例 | 宿主有**遗留进程监听 `[::1]:18000`**，curl/python 优先 IPv6 `::1` | 一律用 `127.0.0.1` 显式 IPv4；urllib 加 `ProxyHandler({})` 禁系统代理 |
| N2 | Docker 端口 bind 失败（Windows 保留段） | Hyper-V/WSL 动态端口范围随时重分配 | 避开 8000 段 + 8500 以下，用 18xxx/28xxx；修改后 `docker compose up -d --force-recreate --no-deps <svc>` |
| N3 | MCP 报 MODULE_NOT_FOUND（`e:\c\Users` 路径） | Git Bash 路径转换 | 控制台环境正常，避开 Git Bash 跑该 CLI |

---

## 六、架构与设计经验（正向沉淀）

- **统一 markdown 管线**：`renderMarkdown()` 是文档/任务/Story/Epic 描述 + 全部评论的唯一入口（图片 + XSS 白名单一处实现，含暗色主题）——避免多处分支导致安全/渲染不一致。
- **评论三实体**：comments 表 task/story/epic_id 恰一非空；service 方法 keyword-only（位置调用 TypeError）；前端发布后只重载评论列表勿 `run()`。
- **COS 上传签名**：V5 四步签名 `StringToSign = sha1\n{KeyTime}\n{SHA1(HttpString)}\n`（非直接 HMAC HttpString）；参数/头按 key 字典序，值编码保留 `;/:=+,-_.~`；`parse_qs` 加 `keep_blank_values=True`。
- **文档文件夹工具**：Python 参数 `None` 无法区分"未传/显式 null" → 移出根目录用布尔开关 `remove_from_folder`/`move_to_root`（API 层仍靠 `model_fields_set` 区分）。
- **Worker 双模式**：默认 `polling_once` 轮询 + `--mq` MQ 模式消费 clarification。
- **Dashboard 性能债**：`GET /api/overview` 一次返回聚合（15s TTL 缓存），但前端 `loadDashboardFullTree` 四级级联请求爆炸——已知待优化。
- **Proposal 闭环**：`converged→story_created` 终态；转化幂等（story_id 回填 + 复用）。

---

## 七、Git / 协作硬规则

- `git add <本次文件>`（**勿 `add .`**，工作区常有记忆脏文件）→ commit → **立即 push**；失败须提示重试。
- remote `ssh://git@ssh.github.com:443/hfutlimit/AgentBoard.git`（SSH over 443）。
- 构建产物不入库：`dist/`、`frontend/dist/` 已 gitignore；`agentboard/web/static/` 仅跟踪 6 个部署必需文件。
- 文档驱动：`docs/requirements.md`、`docs/tasks.md`、`openspec/changes/<id>/{proposal,design,tasks}.md`。

---

## 八、一句话核心教训

1. **前端部署先看 hash 再信"已更新"**——`web_app` 静态目录优先级是头号隐蔽坑。
2. **E2E 绝不能整页刷新**——定时器/路由守卫被销毁 = 假通过。
3. **MCP 身份与热更新**：默认无鉴权、跑内存旧代码，改与验证都受限。
4. **状态机演进要同步 CHECK 约束与存量数据**——MariaDB 约束冲突会直接 500。
5. **worker 是隐形依赖**：缺部署单元 / 非 admin token 都会静默断流（log warning 不阻塞）。
6. **本地端口 IPv6 优先陷阱**：遗留进程占 `[::1]` 时一律用 `127.0.0.1`。
