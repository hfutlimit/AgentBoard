# Story A — P0 安全整改设计总结

> **Epic 145 / Story 291 / Task 1176 (design)**
> 评审基线：2026-08-17 外部 DeepSeek 5 路并行深审，AgentBoard 安全评分 5.8/10。
> 本文档归档 Story A 范围内 6 条 P0 安全硬伤的威胁模型、修复设计、关键决策与验收证据，作为 Story 291 设计容器 task（1176）的交付物。
> 实现侧证据见各 Bug task 评论与下方「修复映射表」commit hash。

---

## 1. 范围与优先级

| Bug ID | Task ID | 标题 | 优先级 | 修复轮次 |
|--------|---------|------|--------|----------|
| B-A1 | 1191 | 轮换硬编码生产 API key（worker_portal.py:44-45） | P0-关键 | 第 2 轮 |
| B-A2 | 1192 | /api/agents/{id}/probe 任意 cli_command 执行（dev 默认匿名 RCE） | P0-关键 | 第 4 轮 |
| B-A3 | 1193 | .dockerignore 未排除 .env（Dockerfile 镜像层密钥泄漏） | P0-关键 | 第 1 轮 |
| B-A4 | 1186 | web_app.py:51 SPA 路径穿越 | P0-高 | 第 3 轮 |
| B-A5 | 1187 | .env 收紧（CORS=* / ALLOW_REGISTRATION=1 / REQUIRE_AUTH=0） | P0-高 | 第 5 轮 |
| B-A6 | 1188 | app.ts:7545 markdown 链接 XSS（属性逃逸注入） | P0-高 | 第 6 轮 |

**整体验收**：6 个子 Bug 状态全部 → done；每个 Bug 至少 1 套单元/集成测试覆盖；README 增加「生产环境部署前必读」章节（B-A5 交付）。

---

## 2. 威胁模型

### 2.1 B-A1 生产 API key 硬编码（P0-关键）
- **资产**：生产 AgentBoard API（`http://124.220.44.12`），完整身份 `abk_Lv493r01...`
- **攻击面**：任何 `git clone` 仓库者持有该 key，可调任意 API（创建/删除项目、写任务、改状态）
- **暴露路径**：`agentboard/worker_portal.py:44-45` 常量默认值 + git 历史
- **影响**：完全身份冒充，绕过所有访问控制

### 2.2 B-A2 probe 端点 RCE（P0-关键）
- **资产**：API 服务器进程（运行 `subprocess.run` 的宿主）
- **攻击面**：dev 默认 `AGENTBOARD_REQUIRE_AUTH=0` → 完全匿名
  - `POST /api/agents/register` body `{"agent_id":"x","cli_command":"cmd /c calc.exe"}` → 注册
  - `POST /api/agents/x/probe` → 服务端执行任意命令
- **暴露路径**：`features/scheduling/api_helpers.py:267-270` `_probe_cli_sync` 在服务器进程内 `subprocess.run(...)`，含 `cmd /c` 回退；`router.py:113-132` probe 端点 dev 模式无鉴权
- **影响**：远程任意命令执行（RCE），服务器完全沦陷

### 2.3 B-A3 Dockerfile 镜像层密钥泄漏（P0-关键）
- **资产**：`.env` 中的 `MINIMAX_API_KEY`、`amqp://guest:guest@...`
- **攻击面**：`docker build` 后任何拉镜像者 `docker history --no-trunc` 可读 `COPY . .` 层内容
- **暴露路径**：`Dockerfile:24` `COPY . .` + `.dockerignore` 未排除 `.env`
- **影响**：第三方密钥泄漏

### 2.4 B-A4 SPA 路径穿越（P0-高）
- **资产**：服务器任意文件（`.env`、`worker_portal.py`、`agentboard.db`）
- **攻击面**：`GET /..%2F..%2Fagentboard%2F.env` → 读 `.env`
- **暴露路径**：`agentboard/web_app.py:51` `angular_asset_or_route` 用户可控路径无 resolve 包含校验；FastAPI `{path:path}` 允许 `..` 段
- **影响**：源码与密钥泄漏，配合 B-A1 可完全接管

### 2.5 B-A5 默认配置开放（P0-高）
- **资产**：任意 CRUD、任意账号注册
- **攻击面**：
  - `AGENTBOARD_REQUIRE_AUTH=0` → 任何能访问 8000 端口的客户端 = 任意用户
  - `AGENTBOARD_CORS_ORIGINS=*` → 任何前端可代发请求
  - `AGENTBOARD_ALLOW_REGISTRATION=1` → 任何人可注册账号
- **暴露路径**：`.env` dev 模板默认值；`validate_runtime_security()` 已有但仅 prod raise
- **影响**：未授权访问 + 账号接管

### 2.6 B-A6 markdown 链接 XSS（P0-高）
- **资产**：用户 localStorage 中的 `agentboard_token`
- **攻击面**：文档/评论/任务描述中插入 `[x](https://a.com/" onclick="alert(document.cookie))`
- **暴露路径**：`frontend/src/app/app.ts:7545` `renderMarkdown` 链接分支不转义 `"`（图片分支 7537 已拒引号，链接分支遗漏）
- **攻击向量变体**：`[x](https://a.com/"onclick="alert\`1\`)` — ES6 标签模板 `alert\`1\`` 无需 `)` 即可执行 JS，绕过 markdown 链接 `)` 闭合限制
- **影响**：XSS + token 接管 = 完全账号失陷

---

## 3. 修复设计

### 3.1 B-A1：fail-fast 凭据注入（P0-关键）
**策略**：移除硬编码默认值，缺凭据时启动失败（exit code 非 0）。

**关键设计决策**：
- 模块级 `app` 条件创建（env 齐全才建，缺则 `app=None`+warning），而非直接 `app=create_app()` — 避免 import 崩溃影响测试/工具链；fail-fast 留给 `main()` 触发
- `create_app()` 缺凭据时抛 `SystemExit`（非零退出码），错误信息含缺失 env 变量名
- `main()` 调整顺序：`create_app()` 提前到 print 之前，确保启动失败前无副作用输出

**未覆盖（运维侧手动）**：
- 服务器端 key 吊销（admin → API Keys 删除 `abk_Lv49...`）需 Jason 手动
- git 历史中 key 仍存在，需 `git filter-repo` 重写（独立窗口）

### 3.2 B-A2：dry-run + 鉴权强制 + 入口拦截（P0-关键）
**策略**：选「彻底修复」(dry-run) 而非「最小修复」(白名单) — probe 是前端「立即探测」按钮，真判活由 Worker 心跳负责，API 侧无需在服务器进程内执行外部命令。

**四层防御**：
1. `core/service_helpers.validate_cli_command`（新增）— 拒绝 shell 启动器（`cmd /c` / `powershell -` / `bash -c` / `wscript` 等 11 个）+ 元字符正则（`;` `|` `&` `>` `<` 反引号 `$()` `${}` 换行）
2. `api_helpers._probe_cli_sync` 改 dry-run — 不再 `subprocess.run`，仅返回 `dry-run: <argv> --version` 预览（≤120 字符）；移除 dead `import subprocess`
3. `router.probe_agent` 强制鉴权 — `if uid is None: 401`（不再 `_auth_is_required()` 软判定，dev 模式也要求登录）
4. 入口拦截：`register_agent` (features/scheduling/service.py) + `update_agent` (service.py facade) 调用 `validate_cli_command`

**关键设计决策**：
- model 字段注入双层防御：模板校验（`{model}` 放行）+ probe 替换后再校验
- 静态扫描用 AST 而非正则：正则误匹配 docstring 里的 `subprocess.run` 文本；AST 精确判定 Call 节点 func
- 行内注释剥离：`ln.split("#",1)[0]` 防误匹配注释里的 `_auth_is_required`

### 3.3 B-A3：.dockerignore 排除（P0-关键）
**策略**：`.dockerignore` 末尾追加 `.env` / `.env.*`。1 行修复，最小 blast radius。

### 3.4 B-A4：resolve 包含校验（P0-高）
**策略**：先 `(STATIC_DIR / path).resolve()` 再 `is_relative_to(STATIC_DIR_RESOLVED)` 校验，逃逸路径统一 404；保留 SPA 深链接回退 index.html 正常行为。

**关键设计决策**：
- 缓存 `STATIC_DIR_RESOLVED = STATIC_DIR.resolve()` 锚点，避免每次请求重复 resolve
- 两类路径区分：`%2F`/`%5C` 编码 `..` 原样进入 `{path:path}`（必须 404）；不编码 `..` 被 Starlette 路由前规范化（回退 SPA，200 但不泄露）

### 3.5 B-A5：分级 fail-fast + 生产模板（P0-高）
**策略**：dev 默认值不变（向后兼容）；prod 严格 raise；提供 `.env.production` 模板。

**关键设计决策**：
- **dev 默认值不变**（向后兼容）：代码默认值 `REQUIRE_AUTH=0 / ALLOW_REGISTRATION=1 / CORS=*` 保持，dev 仅 WARNING 不阻断
- **prod ALLOW_REGISTRATION=1 不 raise**：维护窗口需临时注册新 Agent 账号，raise 会阻断启动；改 WARNING 提醒事后恢复
- **日志断言用 monkeypatch spy 而非 caplog**：全量套件中其他测试修改 logging 配置导致 caplog propagation 失效；spy 直接拦截 `logger.warning` 彻底解耦
- **`_DEV_INSECURE_DEFAULTS` 用真实默认值兜底**：`os.getenv(var, default_val)` 而非空串，env 删除时正确检测不安全默认值
- 提取 `_has_wildcard_cors()` 辅助函数消除重复

### 3.6 B-A6：链接分支镜像图片分支（P0-高）
**策略**：链接分支改 callback，校验 URL 不含 `["'\s<>]` 命中则原文保留，输出 `"` → `&quot;` 双重防御。

**关键设计决策**：
- 断言精确性：`not.toContain('onclick')` 误判 — XSS 拒绝后原文纯文本含 `onclick` 字面词是安全的；精确断言校验「无标签含 on* 事件属性」：`not.toMatch(/<[^>]*\bon\w+\s*=/i)`
- 镜像图片分支 7535-7541 已有拒引号逻辑，链接分支遗漏是 copy-paste 缺陷

---

## 4. 修复映射表

| Bug | 修复文件 | 测试文件 | 用例数 | Commit | Task → done |
|-----|----------|----------|--------|--------|-------------|
| B-A1 | `agentboard/worker_portal.py` | `tests/unit/test_worker_portal_security.py` | 12 | `bccf1e9` | 1191 ✅ |
| B-A2 | `agentboard/core/service_helpers.py`（新）、`agentboard/features/scheduling/api_helpers.py`、`agentboard/features/scheduling/router.py`、`agentboard/features/scheduling/service.py`、`agentboard/api/service.py` | `tests/unit/test_probe_rce_security.py` | 30+ | `ebd7223` | 1192 ✅ |
| B-A3 | `.dockerignore` | `tests/unit/test_dockerignore_security.py` | 7 | `1a19f87` | 1193 ✅ |
| B-A4 | `agentboard/web_app.py` | `tests/unit/test_web_app_path_traversal.py` | 15 | `78d465e` | 1186 ✅ |
| B-A5 | `agentboard/core/infrastructure/auth.py`、`.env.production`（新）、`.env.example`、`.env`、`README.md` | `tests/unit/test_runtime_security.py` | 15 | `6491aa4` | 1187 ✅ |
| B-A6 | `frontend/src/app/app.ts`、`frontend/src/app/models.ts`、`frontend/src/app/app.spec.ts` | `frontend/src/app/app.spec.ts`（B-A6 套件） | 12 | `8877046` | 1188 ✅ |

**累计回归验证**：
- `pytest tests/unit/`：159 passed（B-A5 轮基线，B-A6 为前端测试不并入此数）
- `npx ng test --watch=false --include="**/app.spec.ts"`：12/12 B-A6 测试通过，63/64 总测试通过（1 预先存在失败与 B-A6 无关）

---

## 5. 整体验收清单

| 验收项 | 状态 | 证据 |
|--------|------|------|
| 全部 6 个子 Bug 状态 → done | ✅ | 见上表 Task → done 列 |
| 每条 Bug 至少 1 套测试覆盖 | ✅ | 累计 91+ 用例（12+30+7+15+15+12） |
| README「生产环境部署前必读」章节 | ✅ | `README.md:149-185`（B-A5 交付） |
| `.env.production` 模板存在 | ✅ | `.env.production`（B-A5 交付） |
| `validate_runtime_security()` prod fail-fast | ✅ | `agentboard/core/infrastructure/auth.py`（B-A5 强化） |
| 6 个 commit 全部 push 到 main | ✅ | `bccf1e9` / `ebd7223` / `1a19f87` / `78d465e` / `6491aa4` / `8877046` |

---

## 6. 未覆盖项（运维侧手动，超出代码范围）

| 项 | 责任 | 状态 |
|----|------|------|
| B-A1 服务器端 key 吊销（admin → API Keys 删除 `abk_Lv49...`） | Jason 手动 | 待办 |
| B-A1 git 历史重写（`git filter-repo` 清除 key 历史） | 独立窗口 | 待办 |
| 生产环境实际切换到 `.env.production` 模板 + 强随机密钥 | 部署窗口 | 待办 |

---

## 7. 关联文档

- AgentBoard 系统文档 77《AgentBoard 安全威胁模型与 P0 修复指南》（type=design）
- AgentBoard 系统文档《2026-08-17 完整代码评审与业务价值分析》（type=knowledge）
- `README.md` § 生产环境部署前必读（安全检查清单）
- `.workbuddy/memory/MEMORY.md` § 访问控制 / MCP / 自动化（坑点）
- `deliverables/AgentBoard-问题与经验总结.md`（分类目录）

---

## 8. 设计复盘

**整体策略一致性**：6 条 P0 修复遵循同一原则 — **最小 blast radius + fail-fast + 纵深防御**。每条修复都保留 dev 向后兼容（不破坏本地开发），prod 侧通过 `validate_runtime_security()` 或显式鉴权强制收紧。

**测试策略**：每条 Bug 独立测试文件，用例覆盖正常路径 + 攻击向量变体 + 边界。B-A2 用 AST 静态扫描防回归（防止后续 PR 重新引入 `subprocess.run`）。B-A6 断言精确化（`on\w+\s*=` 而非字面词）避免误判。

**遗留风险**：
- B-A1 git 历史未重写 — clone 旧 commit 仍可读 key，必须配合服务器端吊销
- B-A5 维护窗口 `ALLOW_REGISTRATION=1` 是非阻断 WARNING — 需运维事后恢复
- B-A2 dry-run 改变了 probe 语义（不再真执行）— 前端「立即探测」按钮行为变化，需用户感知（真判活由 Worker 心跳负责，不影响功能）

**后续 Story 衔接**：Story A 关闭后，Story B（前端架构治理）/ C（测试 CI）/ D（后端架构）/ E（基础设施）按优先级推进。Story A 的 `validate_runtime_security()` 与测试体系为 Story C（pytest 配置）奠定基础。
