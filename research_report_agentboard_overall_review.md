# AgentBoard 整体代码 Review 报告

**审查对象**：`d:/AI/Projects/AgentBoard`，分支 `main`，HEAD = `6d613e2`（2026-08-28，"feat(worker): one-shot install + CliLocator + bug fixes for fresh-box deploy"）
**审查日期**：2026-08-28
**审查方式**：`git pull` 拉取最新代码 + 静态审查 + 实际执行测试验证

---

## 执行摘要

代码已同步至最新（`Already up to date.`，HEAD `6d613e2`）。整体上看，AgentBoard 是一个功能完整、分层设计意图清晰的系统，近期在 worker 隔离、错误分类、状态机收敛、行为配置与学习闭环等方向上有大量实质性投入，架构决策（FastAPI 作为 REST 契约真源、Alembic 作为 schema 真源、.NET BFF 契约跟随）是明确且被文档化的。

但本次 review 也确认了三个必须处理的问题：**smoke test 已失效**（`tests/test_smoke.py` 在 `test_rest_api` 阶段断言失败，且 CI 从不运行它，失效至少从 2026-08-26 起未被发现）；**测试套件存在跨文件 DB 绑定泄漏**（`tests/test_run_authorization.py` 与 `tests/test_run_read_authorization.py` 合并运行必挂，CI 因拆成独立进程而"侥幸"全绿，掩盖了根因）；**MCP HTTP 传输默认无鉴权**，且生产环境启动自检 `validate_runtime_security()` 不校验该项，属于可远程触达的高危面。

此外，仓库正处于"新旧双层结构"迁移中途：`core/`+`features/`+`domains/` 新分层与 `mcp_server.py`、`service.py` 门面等旧扁平模块并存，`core/application/service.py`（约 2600 行）等巨型文件与 `service.py` 的 `globals().update()` 动态再导出是主要技术债来源。

---

## 背景与项目现状

AgentBoard 是任务看板系统，领域模型为 Project → Epic → Story → Task/Bug，并内置 OpenSpec 风格 spec 与面向 AI Agent 的 MCP 接口。近 14 天有 273 次提交，活跃度很高，提交者包括人类（jason zhong）与 AI agent（Mavis）。

四个子系统的定位如下：

| 路径 | 语言 | 定位 | 状态 |
|---|---|---|---|
| `src/backend-fastapi` | Python | REST API + MCP + Web 宿主 + AI 运行时 | **活跃，契约/schema 真源** |
| `src/backend-dotnet` | C# (.NET 10) | BFF，未来切换目标 | 活跃开发，**未切流** |
| `src/frontend` | TypeScript (Angular 21) | SPA | 活跃 |
| `src/workers` | C# (.NET 10) | ProposalWorker Windows 服务 | 活跃，独立于 BFF 方案 |

"FastAPI 为真源"有明确文档依据：`README.md:48-51` 的架构图将 FastAPI 标注为 `Legacy["FastAPI (Source of Truth)"]`，`docs/contracts/contract-freeze.md:8-10` 声明 FastAPI 是公开 REST 契约的唯一真源，`src/backend-dotnet/contracts/README.md:3-9` 说明 .NET 侧的 OpenAPI 快照是从运行中的 FastAPI 服务 `GET /openapi.json` 拉取并做漂移检查。`src/backend-dotnet/migrations/` 仅有 README、无任何迁移文件，SQL 交给 Alembic 执行。

因此 **.NET BFF 不是死代码，但也还不是 canonical**。本次 review 的重点放在 FastAPI 侧。

---

## 一、测试健康度（本次 review 的最主要发现）

### 1.1 Smoke test 已失效 — 高危

按项目约定（`README.md:418` 记录为 `python tests/test_smoke.py`）运行 smoke test，**失败**：

```
File "tests/test_smoke.py", line 133, in test_rest_api
    assert c.put(f"/api/tasks/{t['id']}/status", json={"status": "done", "status_reason": "completed"}).status_code == 200
AssertionError
```

复现得到的实际响应为 `400 {"detail":"Task: in_progress → done is not allowed"}`。

这不是随机故障，而是**测试未跟随一个有意为之的行为变更**。根因在 `src/backend-fastapi/agentboard/features/work_items/state_machine.py:41-52`：

```python
# Review 2026-08-26 P1 #3：删除 ``{todo, in_progress} → done`` 两条边。
# AgentBoard workflow contract 要求：
#   Agent 自动流程：todo → in_progress → in_review → done（必经 Review）
#   Admin 强制完成：必须走 ``force_complete_task`` 显式命令（status_reason="manual_override"）
_TASK_TRANSITIONS: dict[Status, set[Status]] = {
    Status.TODO:        {Status.IN_PROGRESS, Status.BLOCKED},
    Status.IN_PROGRESS: {Status.IN_REVIEW, Status.TODO, Status.BLOCKED},
    Status.IN_REVIEW:   {Status.DONE, Status.IN_PROGRESS, Status.BLOCKED},
    Status.DONE:        {Status.IN_PROGRESS, Status.BLOCKED},
}
```

该变更落在提交 `44f048d`（2026-08-26，"fix: close execution runtime review findings"）。而 `tests/test_smoke.py` 最后一次被修改是 `97c9db9`（"fix(refactor): 修 Phase 4 拆分后 test 回归"），**早于或等于该行为变更**，断言仍写着旧的 `in_progress → done` 路径。

危害被两点放大：一是项目约定"每次改动后自行跑 smoke test"，而它现在是红的，等于该约定已失效；二是 **CI 完全不运行 `test_smoke.py`** —— `.github/workflows/application-stack-check.yml:41-78` 逐个列举了具体测试文件，其中没有 `test_smoke.py`。所以这个失败已经静默存在了至少两天。

建议：修正 smoke test 走 `in_progress → in_review → done` 合法路径（或改用 `force_complete_task`），并把它加入 CI 的 python job。

### 1.2 测试套件存在跨文件 DB 绑定泄漏 — 高危（CI 侥幸掩盖）

`tests/test_run_authorization.py` 与 `tests/test_run_read_authorization.py` **单独运行均通过**（分别为 6 passed / 12 passed），但**合并到同一 pytest 进程运行必挂**，且失败归属随顺序变化：

- 顺序 A→B：`test_run_authorization.py::test_last_event_id_replays_only_newer_events` 失败，`assert 'id: 5\n' in ''`
- 顺序 B→A：`test_run_read_authorization.py::test_sse_stream_replays_full_backlog_past_page_size` 失败，`missing event id=1`

**CI 为什么是绿的**：workflow 第 47 行与第 53 行是两次**独立的 `python -m pytest` 调用**，即两个独立进程、两套独立 `sys.modules`，冲突条件不成立。这是"绕开症状"而非修复根因——CI 配置文件最后修改于 `c37c032` "fix: wire transient retries and repair **ci gate**"，说明当时很可能是撞到这个冲突后把两行拆开了。

**根因（已验证）**：两个测试文件都在模块体（import 期）执行同一套"重置环境"写法，例如 `tests/test_run_authorization.py:12-29`：

```python
_DB = tempfile.mktemp(suffix=".db")
os.environ["AGENTBOARD_DB_URL"] = f"sqlite:///{_DB}"
for _module in list(sys.modules):
    if _module == "agentboard" or _module.startswith("agentboard."):
        del sys.modules[_module]
...
init_db()
```

而 `src/backend-fastapi/agentboard/core/infrastructure/database.py:20-24` 在 **import 期**一次性绑定：

```python
URL = os.getenv("AGENTBOARD_DB_URL", DEFAULT_URL)
engine: Engine = create_engine(URL, ...)
SessionLocal = sessionmaker(bind=engine, ...)
```

该文件**没有提供任何 `reset_engine()` / `dispose()` 接口**。于是：后导入的文件 purge 并重建了 `engine`/`SessionLocal`，指向自己的临时库；但**先导入文件的 `seeded` fixture（module-scoped）已把 run / events 写进了它自己的库**。而两个 SSE 测试都是**在函数体内** `from agentboard.features.scheduling.router import stream_run_events`（`test_run_authorization.py:247`、`test_run_read_authorization.py:245`），调用时拿到的是**后导入文件的新 router 模块**，其 `stream_run_events` 内部 `with SessionLocal() as replay_session:`（`router.py:214`）用的是**新 engine**，去查一个不存在该 run 的库 → 回放结果为空 → 断言失败。

值得注意的是，router 里 `run_event_bus: IRunEventBus = InProcessRunEventBus()`（`router.py:57`）同样是 import 期单例，但它**不是**故障原因：发布端（`router.py:143`）与订阅端（`router.py:187`）都位于同一个被重新导入的模块内，始终自洽；且两个失败用例的假 `ReplayRequest.is_disconnected()` 恒返回 `True`，断言完全走 DB 回放路径、不经过 bus。

**影响面**：全仓库使用这一 purge 写法的**只有这两个文件**，因此 `pytest tests/` 整体运行时，这对文件必然互相污染。任何开发者本地跑全量测试都会遇到红灯，进而在"CI 是绿的"情况下产生误判。

**修复方向**（未实施）：为 `core/infrastructure/database.py` 增加 `reset_engine(url=None)`/`dispose()`；purge 后调用它，并把旧 engine dispose 掉（当前旧 engine 从不释放，泄漏临时库句柄）。更彻底的做法是迁移到 `tests/conftest.py:22-24` 已推荐的 `StaticPool` + `dependency_overrides` 模式。

### 1.3 测试套件结构性问题

- **规模与构成**：`tests/` 目录被 git 跟踪 393 个文件，其中约 178+ 个文件依赖 Playwright、需要浏览器与常驻服务（`http://127.0.0.1:4200` + 活 API）；真正自含的单元测试集中在 `tests/unit/`，实测 **306 passed**。
- **命名混乱、缺少分层目录**：根目录下平铺着 `test_epic28_…` 到 `test_epic139_…` 等大量按 epic/版本命名的 e2e 文件，与 `tests/e2e/`、`tests/e2e_story_slim_tasks/`、`tests/e2e_nightly_20260821_2205/`、`tests/e2e_dotnet_bff/` 等子目录并存，缺乏统一归档约定，历史一次性验证脚本与常驻测试混在一起。
- **CI 覆盖过窄**：`application-stack-check.yml:41-78` 用硬编码文件清单逐个跑测试，而非目录收集。新增测试文件不会自动进入 CI，本次 smoke test 失效之所以无人发现，正是这一策略的直接后果。建议改为按目录收集（如 `pytest tests/unit tests/integration`）并用 `-p no:cacheprovider` 保证可重复。

---

## 二、安全问题

### 2.1 MCP HTTP 传输默认无鉴权 — 高危

MCP 服务是**独立进程**（`api.py` 中并未挂载 MCP）。`src/backend-fastapi/agentboard/mcp_server.py:1634-1649` 依据 `AGENTBOARD_MCP_TRANSPORT` 选择传输方式，其中 `http`/`streamable-http` 会绑定 `AGENTBOARD_MCP_HOST`（默认 `127.0.0.1`）的 8001 端口 `/mcp` 路径。

问题在于鉴权默认值，见 `mcp_server.py:45` 与 `:66`：

```python
MCP_REQUIRE_AUTH = os.getenv("AGENTBOARD_MCP_REQUIRE_AUTH", "0").lower() in {"1","true","yes"}
mcp = FastMCP("AgentBoard", auth=AgentBoardTokenVerifier() if MCP_REQUIRE_AUTH else None)
```

`mcp_server.py:1637-1639` 确实有启动校验，但它校验的是**令牌签名密钥**的强度（`AGENTBOARD_SECRET` 长度 ≥ 32 且非默认值），**并不校验鉴权是否开启**——两个不同的控制点被混为一谈。结果是：运维可以在生产环境启动一个网络可达、但**完全无鉴权**的 MCP 端点，所有写工具（`create_project` `:159`、`delete_project` `:173`、`delete_task` `:298`、`batch_delete_tasks` `:709`、`create_webhook` `:856`、`agent_register` `:1451` 等约 100 个工具）均可匿名调用。

更关键的是，生产环境 fail-fast 自检 `validate_runtime_security()`（`core/infrastructure/auth.py:50-94`，由 `api.py:52` 在 lifespan 调用）**只检查 `SECRET`、`REQUIRE_AUTH`、`CORS` 三项，不检查 `AGENTBOARD_MCP_REQUIRE_AUTH`**。因此 `AGENTBOARD_ENV=production` 能通过全部启动校验，同时留下一个开放的 MCP HTTP 端点。

危害会与后端默认值叠加：MCP 进程通过 REST 访问后端（`features/mcp/shared.py:43-63`），鉴权关闭时 `_current_token()` 返回 `None` → 不携带 `Authorization` 头 → 若后端 `AGENTBOARD_REQUIRE_AUTH=0`，即退化为匿名开放 CRUD。

**建议**：将 `AGENTBOARD_MCP_REQUIRE_AUTH` 纳入 `validate_runtime_security()` 的生产断言；把 `AGENTBOARD_MCP_TRANSPORT=http` 与 `MCP_REQUIRE_AUTH=1` 绑定为强约束（transport 为 http 时鉴权不可关闭）；并使 `_http()` 在无 token 且鉴权开启时直接失败，而非静默降级为匿名调用。

### 2.2 MCP 工具层无纵深防御 — 中危

约 100 个 `@mcp.tool()` 定义中**没有任何一个**带 `_authorize`、`require_auth` 或 scope 装饰器，每个工具都只是 REST 的薄代理。而 `features/mcp/shared.py:43-63` 的 `_http` 在 token 缺失时是"静默降级"而非报错：

```python
token = _current_token()
if token and "Authorization" not in headers:
    headers["Authorization"] = f"Bearer {token}"
```

作为"委托给 REST 层鉴权"的设计可以理解，但这意味着 MCP 层**完全没有独立防御**，全部安全姿态压在后端 `AGENTBOARD_REQUIRE_AUTH` 与 `AGENTBOARD_MCP_REQUIRE_AUTH` 两个默认值上。

### 2.3 其余安全面（已核查，未见高危）

- **子进程/RCE 面**：应用会 shell 出 codex / codebuddy / minimax 等 CLI。相关调用位于 `agent_runtime/invokers.py` 等处；仓库已存在 `tests/unit/test_probe_rce_security.py`、`tests/unit/test_runtime_security.py` 等针对性测试，且 `src/workers` 侧新增的 `CliLocator` 采用"显式绝对路径 → 已知安装位置 → `where.exe` → 兜底"的探测顺序并新增了 `cmd /c` 包装（修复 Win32 193）。未见 `shell=True` 的高危用法暴露在 agent 可控数据路径上。
- **路径穿越**：`web_app.py` 的静态资源服务已有 `tests/unit/test_web_app_path_traversal.py` 覆盖，修复在源码中确实存在。
- **SQL 注入**：未发现 SQL 文本中的 f-string / `%` / `.format()` 插值，ORM 查询为主。
- **提交到库的密钥**：`.gitignore` 已覆盖 `.env`、`*.db`、`logs/`、`tmp/`、`screenshots/` 等；`git ls-files` 过滤后仅有 `.env.example`、`.env.production`、`config/deployment/worker.env.example` 三个文件被跟踪，需确认其内容为占位符而非真实值。

---

## 三、架构与技术债

### 3.1 迁移中途的双层结构

`src/backend-fastapi/agentboard/` 下同时存在新分层（`core/`、`features/`、`domains/`）与旧扁平模块（`mcp_server.py`、`executor.py`、`api_helpers.py`、`service.py`、`agent_runtime/`）。当前生效路径是：路由注册于 `api.py:435-446`（13 个 `include_router`），但注册的 `features/*/router.py` 内部调用的是 `service.py` 这个**运行时再导出门面**，且直接抛 `HTTPException` 而非新分层定义的领域异常（`core/exceptions.py`）。即新分层已搭好骨架，但生产代码仍走旧通路。

### 3.2 `service.py` 的 `globals().update()` 反模式

`src/backend-fastapi/agentboard/service.py` 仅 20 行，通过 `globals().update()` 把 `core/application/service.py` 的全部公开符号在运行时注入自身命名空间。核查确认：**`src/` 生产代码中 0 个模块**直接 `from agentboard.core.application.service import ...`，根门面是唯一入口（`api.py`、`api_helpers.py`、`scheduler.py`、`executor.py` 均走它），且 `tests/` 中大量依赖 `from agentboard import service`。

该模式的代价是：IDE 跳转、mypy/pyright、jedi 全部失效；`from service import X` 拿到的对象其 `__module__` 仍是 `agentboard.core.application.service`，导致 traceback 与日志归因错位；`__all__` 亦为动态推导，linter 与文档生成器无法静态读取。由于被生产代码与测试同时依赖，不能简单删除，建议改为显式的 `from .core.application.service import (...)` 再导出以恢复静态可分析性。

### 3.3 巨型文件

排除 `web/static` 后，超过 1000 行的 Python 文件包括：

| 文件 | 约行数 | 说明 |
|---|---|---|
| `core/application/service.py` | ~2600 | **核心 god object**，混含 6 类实体搜索、Story 生命周期、Agent 注册表、Run 管理等至少 8 个限界上下文 |
| `mcp_server.py` | ~1700 | 约 100 个 MCP 工具定义集中于此 |
| `core/infrastructure/messaging/rabbitmq.py` | ~1400 | |
| `features/work_items/service.py` | ~1000 | |
| `features/work_items/router.py` | ~851 | 32 条路由定义 |
| `agent_runtime/worker.py`、`invokers.py`、`behavior/context_builder.py`、`coordinator.py`、`executor.py` | 各约 800 | |

### 3.4 重复与未迁移实现

- `core/state_machine.py`（175 行）是通用基类，`features/work_items/state_machine.py` 正确继承它；但 `features/proposals/state_machine.py`（118 行）**未继承**，自行重复定义了 `TransitionSpec`、`ProposalStateMachine`、`IllegalTransitionError`、`TransitionValidationError`，属于未迁移的平行实现。
- `models.py`、`features/*/models.py`、`domains/*/models.py` 三者经核查**不是重复映射**：根 `models.py`（60 行）开头即声明为兼容门面，`from .domains.work_items.models import Attachment, AuditLog, Comment, Task`，未出现"两个类映射同一张表"的冲突。
- `features/documents/` 与 `domains/documents/`、`features/proposals/` 与 `domains/proposals/` 同理为门面转发关系。

### 3.5 业务逻辑泄漏进中间件

`api.py:357-417` 的 `project_access_middleware` 内含约 60 行授权策略（项目解析、根资源与子资源的 owner 判定、成员关系检查）。授权决策放在 HTTP 中间件里，对单元测试不可见，也无法被 MCP / worker 等其他入口复用。此外 `api.py` 仍残留 `execute_ticket_request_by_id_inner`（`:229-251`）、`fail_ticket_request_inner`（`:253-259`）、`claim_ticket_request_inner`（`:261-273`）等业务函数，以及 `:58`、`:60`、`:106`、`:112`、`:222`、`:275` 等多处遗留的分节标记注释。

---

## 四、最新提交 `6d613e2` 的针对性审查

该提交为 .NET 10 ProposalWorker 做了三组改动（A. 三个 bug 修复；B. 新增 `CliLocator`；C. 默认全权限参数 + 生产配置模板 + 一键安装脚本）。A、B 两组的修复本身是正确的：`WorkerState.cs:43-45` 的 `worker_id` 不再硬编码空串、`MiniMaxAdapter.cs:47-62` 不再把环境变量名当作值写入、`Program.cs:12-20` 锁定 CWD 以适配 `sc.exe` 启动（CWD 为 `system32` 时找不到 `appsettings.Production.json`）；`CliLocator` 的探测顺序（显式路径 → `where.exe` → 已知安装位置 → 兜底）与 `ProcessExecutor.cs:177-197` 的 `.cmd`/`.bat` 包装也是合理设计。

但 C 组（安装脚本与默认参数）存在若干需要处理的隐患：

1. **`scripts/install-worker.ps1` 的 `sc.exe` 环境块几乎肯定是坏的**（约 334-339 行）：代码拼出 `env=NAME=value` 后套用模板 `"env=$_`"`$_`"`，会产生 `env=NAME=value"NAME=value"`；随后 `scArgs += $envBlock` 把**含空格的单个字符串**作为一个数组元素追加，`sc.exe` 收到的是被拆乱的参数。结果是本该注入服务的机密（RabbitMQ 凭据、Portal API Key）实际不会正确写入。
2. **同一脚本的 JSON 替换未转义**（约 301-303 行）：`-replace` 的替换串被当作正则处理，`$PortalApiKey` / `$AmqpUri` 中若含 `$1`、`$&` 或反斜杠会破坏输出；更严重的是这些是直接字符串拼接、**没有做 JSON 转义**，AMQP 密码里含 `"` 或 `\` 就会生成格式非法的 `appsettings.Production.json`。应改用 `ConvertTo-Json`，至少也要 `[regex]::Escape`。
3. **明文打印 Portal API Key**（约 389、395 行）：安装结束时把完整密钥回显到控制台并嵌入建议命令，而第 254 行此前只以 `(generated) xxxxxxxx...` 形式展示，两处不一致且会把机密泄漏到终端回滚与 CI 日志。
4. **`--full-auto` / `-y` 作为默认值**需重新评估安全姿态：`appsettings.json:37`（codex `--full-auto`）与 `:21`（`-p -y`）使 worker 在项目目录内获得无需逐次审批的读写能力。配合 `CliLocator` 在全新机器上的自动发现，等于默认授予无人值守的全权限。建议改为显式 opt-in。
5. **`CodexAdapter.cs:40-42` 的硬编码兜底与文档默认值不一致**：提交说明与 `appsettings.json` 都写明默认含 `--full-auto`，但 C# 侧的兜底值是 `{"exec","--json"}`（不含 `--full-auto`），一旦配置被清空，Codex 会静默以非全自动模式运行。

测试方面，该提交的覆盖存在缺口：核心新增行为 `CliNotFoundException` 的抛出路径（commit message 强调的 fail-fast 契约）**没有测试**；`Sprint7_CliLocatorTests.cs:78-95` 名为 `Locator_does_not_silently_swallow_resolution_failures_via_warning`，但注释自承"我们在此仅作文档化断言"，实际断言与用例名相反；风险最高的 `install-worker.ps1`（密钥生成、`sc.exe` 注册、JSON 替换）**零测试覆盖**，所谓"75/75 passing"仅覆盖 C# 单元测试。

---

## 五、仓库卫生

- `.gitignore` 经过多轮精心维护（含 2026-08-09、2026-08-14、2026-08-15 三次清理段落），已覆盖 `data/`、`logs/`、`tmp/**`（保留 `.gitkeep`）、`screenshots/`、`*.db`、`_test_*.db`、`commit_msg.txt`、各类 agent 运行期产物（`.agent_history/`、`.minimax/`）等。`git ls-files` 核对未发现应忽略却已跟踪的运行时产物。
- 工作区根目录仍存在未被跟踪的本地产物（`agentboard.db`、`_test_task_sm_tmp.db`、`commit_msg.txt`、`tmp/`、`logs/`、`data/`），已被 gitignore 正确排除，无需处理。
- CI 有两个 workflow：`application-stack-check.yml`（python job / frontend job / windows worker job）与 `dotnet-contract-check.yml`（.NET 与 FastAPI 的契约漂移检查）。契约漂移检查是这套双栈架构里很有价值的一环，建议保持。

---

## 结论

回答"整体 review"这一诉求：代码已同步到最新，主干功能完整、架构决策清晰且有文档支撑，`.NET BFF` 的契约跟随策略与契约漂移 CI 是扎实工程实践；`tests/unit` 306 个单元测试全绿，本次 CI 所选后端测试集除下述隔离问题外亦可通过。

但当前**不能认为主干是健康的**，因为存在三个已验证的具体缺陷，按优先级排列：

1. **smoke test 失效**（`in_progress → done` 已非合法迁移，测试未跟随；CI 从不运行它）—— 直接使项目既定的"改动后自测"约定失效。
2. **测试跨文件 DB 绑定泄漏**（`database.py:20-24` 无 reset 接口 + 两处 `sys.modules` purge 写法）—— 本地全量跑测试必挂，CI 因拆分进程而掩盖，属于典型的"绿色 CI 但坏的可信度"。
3. **MCP HTTP 传输默认无鉴权且生产自检不校验**（`mcp_server.py:45/66`、`core/infrastructure/auth.py:50-94`）—— 可远程触达的高危面，建议最优先修复。

在此基础上，建议的中期方向是：把 CI 从"硬编码文件清单"改为按目录收集（这是让 smoke test 与未来新增测试真正生效的前提）；为 `database.py` 增加 `reset_engine()` 并把两个 SSE 测试迁移到 `conftest.py` 已推荐的 `StaticPool` + `dependency_overrides` 模式；将 `service.py` 的动态再导出改为静态再导出；并把 `core/application/service.py`（约 2600 行）按限界上下文拆分。

---

## 局限性说明

- 本次为静态审查 + 局部动态验证。受环境限制，未启动完整服务运行 178+ 个 Playwright e2e 用例，前端 `src/frontend` 与 .NET BFF `src/backend-dotnet` 的运行时行为未做端到端验证。
- `src/frontend` 与 `src/backend-dotnet` 的审查深度低于 FastAPI 侧；这两部分的主要改造建议需另开专项。
- 关于 `install-worker.ps1` 的 `sc.exe` 环境块与 JSON 转义问题，结论基于静态代码阅读，未在真实 Windows 服务环境实测确认故障现象。
- 未对 `.env.production`、`config/deployment/worker.env.example` 的实际内容做逐项机密扫描，仅确认其被 git 跟踪且 gitignore 已排除同名非 example 文件。

---

## References

1. [AgentBoard README — 架构与当前阶段](d:/AI/Projects/AgentBoard/README.md)
2. [契约冻结说明 — FastAPI 为 REST 契约真源](d:/AI/Projects/AgentBoard/docs/contracts/contract-freeze.md)
3. [CI workflow — application stack check](d:/AI/Projects/AgentBoard/.github/workflows/application-stack-check.yml)
4. [Task 状态机 — in_progress→done 边删除说明](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/features/work_items/state_machine.py)
5. [数据库 engine/SessionLocal import 期绑定](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/core/infrastructure/database.py)
6. [MCP server — transport 与鉴权默认值](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/mcp_server.py)
7. [运行时安全自检 validate_runtime_security](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/core/infrastructure/auth.py)
8. [SSE 流端点与 run_event_bus 单例](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/features/scheduling/router.py)
9. [RunEvent 进程内总线实现](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/features/scheduling/run_event_bus.py)
10. [service.py 门面（globals().update 再导出）](d:/AI/Projects/AgentBoard/src/backend-fastapi/agentboard/service.py)
11. [测试隔离问题文件之一](d:/AI/Projects/AgentBoard/tests/test_run_authorization.py)
12. [测试隔离问题文件之二](d:/AI/Projects/AgentBoard/tests/test_run_read_authorization.py)
13. [Smoke test（当前失败）](d:/AI/Projects/AgentBoard/tests/test_smoke.py)
14. [Worker 一键安装脚本](d:/AI/Projects/AgentBoard/scripts/install-worker.ps1)
15. [.gitignore](d:/AI/Projects/AgentBoard/.gitignore)
