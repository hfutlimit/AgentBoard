# AgentBoard — Agent Project Memory

> 项目专属知识沉淀。由 Mavis（agent）维护。跨会话累积，新会话启动时优先读本文件。
>
> 写入规则参考 Mavis memory 三段式：**规则 → 证据/原因 → 适用场景**。

---

## Proposal Worker: FakeAdapter（2026-08-21）

**规则 → Worker 想跑起来不依赖 workbuddy / MiniMax / codex 外部 CLI 时，加 `FakeAdapter`。**

- **证据/原因**：
  - 本地开发机一般没装 workbuddy / MiniMax / codex 三个 CLI；不装任何一个都会让相应 adapter 实际跑消息时炸。
  - 现有 `FakeAgentAdapter`（`workers/AgentBoard.ProposalWorker.Tests/Fixtures/FakeAgentAdapter.cs`）只是测试 fixture，不在生产代码里。
  - 2026-08-21 实装：新增 `workers/AgentBoard.ProposalWorker/Agents/FakeAdapter.cs`，`dotnet build` 0 警告 0 错误，后台启动后 `GET /health` 显示 4 个 agent（含 fake）均 `registered: true`。
- **做法**：
  1. `workers/AgentBoard.ProposalWorker/Agents/FakeAdapter.cs` 新增，类签名：
     ```csharp
     public sealed class FakeAdapter : IAgentAdapter
     {
         public string AgentType => "fake";
         public async Task<AgentExecutionResult> ExecuteAsync(ExecutionContext context, CancellationToken ct)
         {
             await Task.Yield(); // 让 dispatcher 走真正的异步路径
             // 构造 action: "ask" 的 JSON 返回
         }
     }
     ```
     不走 `IProcessExecutor`，无外部进程依赖。
  2. `Options.cs` 的 `AgentsOptions` 加 `public AgentOptions Fake { get; set; } = new() { Command = "" };`
  3. `appsettings.json` 的 `Agents` 段加 `Fake` 子段（`Command: ""`、`TimeoutMinutes: 1`、`MaxCapturedOutputChars: 20000`、`ApiKeyEnv: ""`）
  4. `Program.cs` 加 `builder.Services.AddSingleton<IAgentAdapter, FakeAdapter>();`
- **验证**：本地 `dotnet run --project workers/AgentBoard.ProposalWorker`，日志出现 `Registered agents: [workbuddy, minimax, codex, fake]`；`http://127.0.0.1:58240/health` 返回 `agents.fake.registered: true`。
- **不踩坑点**：
  - `IOptions<AgentsOptions>` 注入到 `FakeAdapter` 是 OK 的，即使 Fake 段我们根本不读字段（保持选项 schema 一致）。
  - `AgentAdapterRegistry` 走的是 `IEnumerable<IAgentAdapter>`，新增的 `FakeAdapter` 会被自动收进 registry，不需要额外改 registry 代码。
  - RabbitMQ URL 留空会让 `RabbitMqConsumerService` 报 `RabbitMq:Uri is required; consumer is disabled` —— 这是预期的，consumer 不会启用，HTTP portal 仍可访问。
- **适用场景**：
  - 本地无 CLI 时的 smoke 测试
  - CI 环境跑 dispatch 链路
  - e2e 跑流程又不想 mock 真 CLI
  - 演示 / 教学

---

## AgentBoard MCP 服务端的临时问题（2026-08-21）

**规则 → `mcp__agentboard__append_agent_memory` 当前报 `_MEMORY_PROJECT_TITLE` / `_MEMORY_AGENT_PREFIX` 未定义错误，无法使用。**

- **临时方案**：在 `docs/agent-memory.md` 维护项目级沉淀，Mavis 会话启动时先读这个文件。
- **不阻塞**：项目代码改动本身不依赖 mcp 记忆功能。
- **后续**：等服务端修好（看错误信息像模板渲染变量未注入）。

---

## Angular HttpClient `status: 0` "Unknown Error" 排查套路（2026-08-21）

**规则 → 前端报错 `Http failure response for /api/...: 0 Unknown Error`（或 toast 0 Unknown Error）时，先按这套 60 秒排查套路定位，不直接看代码。**

- **证据/原因**：
  - `status: 0` 不是 HTTP 4xx/5xx，是 Angular HttpClient 在**网络层**根本没收到 HTTP 响应。常见根因（按概率）：
    1. **proxy 目标端口没起**：`frontend/proxy.conf.json` 把 `/api` 转发到某端口（如 AgentBoard = 58125 = .NET BFF），但该端口 LISTEN 不存在
    2. **CORS preflight 失败**：浏览器 OPTIONS 预检被拦截（生产 web_app `AGENTBOARD_CORS_ORIGINS` 缺前端 origin）
    3. **后端进程崩了 / 端口被换**：API 进程退出或换了端口
    4. **混合内容**：HTTPS 页 → HTTP API 被浏览器拦
    5. **STATIC_DIR stale**：模块加载时缓存的 `STATIC_DIR_RESOLVED` 没刷新（重启 web 进程才生效）
- **排查套路**（按顺序执行）：
  1. **看 LISTEN 端口**：
     ```powershell
     Get-NetTCPConnection -LocalPort 18000,28080,4200,58124,58125 -State Listen
     ```
     对比项目期望：AgentBoard dev 期望 18000=API + 28080=web_app / 4200=ng serve + 58125=.NET BFF
  2. **直连后端 POST 登录**：
     ```powershell
     Invoke-RestMethod -Uri 'http://127.0.0.1:18000/api/auth/login' -Method POST `
       -Body '{"username":"admin","password":"admin123"}' -ContentType 'application/json'
     ```
     - 200 + token → 后端 + 账号都没问题，问题在前端链路
     - 401 → 账号密码错（去 DB 查 `users.password_hash` 或重新 register）
     - Connection refused → 端口没起或进程崩
  3. **检查 proxy 配置**：`frontend/proxy.conf.json` → `/api` target 端口必须 LISTEN
  4. **检查 web 进程**：web_app（28080）若用 `local-start-web.ps1` 跑过，看 `STATIC_DIR` 是否变了（`AGENTBOARD_WEB_STATIC_DIR` 环境变量）
  5. **检查 CORS**：FastAPI `agentboard/api.py` 里 `CORSMiddleware allow_origins` 是否含前端 origin
- **AgentBoard 项目特定**：
  - `proxy.conf.json` 把 `/api` → 58125（.NET BFF）
  - 双栈模式（Stage 0 default）需要同时起 18000（Python API）+ 58125（.NET BFF）
  - **单跑 Python 模式 → 登录必 0 Unknown Error**；要么起 .NET BFF，要么改 proxy 指向 18000
  - dev 凭据：`admin/admin123`（e2e_docker_setup.py / verify_admin.py / track_epic39_status.py 等多个脚本的默认登录）
- **适用场景**：
  - dev-up / docker compose up / ng serve / local-start-web 任意一种启动方式后，前端第一次 API 调用失败
  - 用户截图报 "Http failure response for /api/...: 0 Unknown Error"
  - 巡检脚本里 API 调用突然全 0 错
- **具体事件**：2026-08-21 review follow-up 提交 `de86f83` push 后，用户截图登录页 0 Unknown Error。根因：proxy target 58125 (.NET BFF) 没 LISTEN，浏览器请求被拒绝。18000 直连 admin/admin123 成功（id=1, is_admin=True, token 拿到）。用户当场要求「部署后自己先用测试账号登录验证」（已沉淀到 user memory 跨项目偏好）。

---

## `agentboard.web_app` 本地 dev 注入 API URL 失败的隐藏 bug（2026-08-21）

**规则 → `agentboard/web_app.py` 读 `AGENTBOARD_API_URL` 而不是 `.env` 约定的 `AGENTBOARD_WEB_API_URL`，本地 dev 模式下 web 端拿不到正确 API 地址。已修：web_app.py 同时兼容两个 key。**

- **证据/原因**：
  - `.env:9` 写 `AGENTBOARD_WEB_API_URL=http://127.0.0.1:18000`（项目对外约定 key）
  - `docker-compose.yml:102` 用 `${AGENTBOARD_WEB_API_URL:?...}` 读这个 key，再映射到容器内 `AGENTBOARD_API_URL`
  - `agentboard/web_app.py:20` 原代码只读 `AGENTBOARD_API_URL`——**本地 dev 模式没 docker-compose 帮映射**，所以 `.env` 里的 `AGENTBOARD_WEB_API_URL` 永远拿不到
  - 表现：浏览器访问 28080 web_app，index.html 注入 `window.AGENTBOARD_API = 'http://127.0.0.1:58124'`（默认值），前端发请求到 58124（.NET BFF）但 58124 没 LISTEN → 0 Unknown Error
  - **修复**：`web_app.py:20` 改成 `os.getenv("AGENTBOARD_WEB_API_URL") or os.getenv("AGENTBOARD_API_URL") or "http://127.0.0.1:58124"`，本地 dev 走 `AGENTBOARD_WEB_API_URL`，docker 容器内仍走 `AGENTBOARD_API_URL`（docker-compose 映射的），默认值兜底
- **不要踩的坑**：
  - **别只改 `.env`**——`.env` 的 key 是项目约定的，改 web_app.py 才正确
  - **别删默认 fallback**——生产环境（.NET BFF 58124）可能没设任何 env
  - **别用 `[Environment]::SetEnvironmentVariable` 不带 target 参数**——只设当前进程 env，`Start-Process` 子进程拿不到（`local-start-web.ps1` 也有这隐藏 bug，但暂未修）
  - **别用 `EnvironmentVariableTarget.User` SetEnvironmentVariable**——子进程不重读 registry，要新 PowerShell 进程才生效
  - **正确启动方式（本地 dev）**：
    ```powershell
    # 方式 1：cmd 包装 set（推荐，env 显式传递）
    $cmdLine = "set AGENTBOARD_WEB_API_URL=http://127.0.0.1:18000 && `"$exe`" -m uvicorn agentboard.web_app:app --host 127.0.0.1 --port 28080"
    Start-Process cmd.exe -ArgumentList '/d','/s','/c',$cmdLine ...
    # 方式 2：直接 inline python（不用 uvicorn CLI）
    Start-Process cmd.exe -ArgumentList '/d','/s','/c',"set AGENTBOARD_WEB_API_URL=http://127.0.0.1:18000 && `"$exe`" -c `"import uvicorn; uvicorn.run('agentboard.web_app:app', host='127.0.0.1', port=28080, log_config=None)`"
    ```
- **验证 web_app 注入对错**（必做，比 POST 登录更快定位）：
  ```powershell
  $r = Invoke-WebRequest -Uri 'http://127.0.0.1:28080/' -Method GET -UseBasicParsing
  $m = [regex]::Match($r.Content, 'AGENTBOARD_API\s*=\s*["'']([^"'']+)["'']')
  $m.Groups[1].Value  # 期望你设的 API 地址，不是 58124
  ```
- **适用场景**：
  - 本地 dev 启动 28080 web_app 后，浏览器 API 请求全 0 Unknown Error，但 18000 API 直连 OK
  - `AGENTBOARD_API_URL` vs `AGENTBOARD_WEB_API_URL` 命名困惑
  - 任何 web_app（28080）+ FastAPI（18000）单栈模式启动
- **具体事件**：2026-08-21 修完 de86f83 后启 28080，用户登录后报「Agent 列表加载失败: Http failure response for http://127.0.0.1:58124/api/agents: 0 Unknown Error」。根因：web_app 注入 58124 而非 18000。debug 流程：inline `os.getenv` 拿得到 18000，但 `web_app.API_URL` 是 58124 → 锁定 web_app.py:20 key 不一致。修法已 commit 待 push。

---

## `Failed to execute 'open' on 'XMLHttpRequest': Invalid URL` 根因（2026-08-21）

**规则 → web_app.py 注入 `API_URL` 时必须 `.strip()`，且本地 dev 启动 cmdline 写法不能有 `set NAME=VAL && ` 那个 trailing space。**

- **证据/原因**：
  - **cmd.exe `set` 吞 trailing space**：`set X=http://y && python ...` 中 set 后面的空格被 set 当作 X 值的一部分 → 进程内 `os.getenv('X')` = `'http://y '`（带尾空格）
  - 注入到 index.html：`window.AGENTBOARD_API = "http://127.0.0.1:18000 "`（带尾空格）
  - 前端 `XMLHttpRequest.open('POST', 'http://...:18000 ')` 抛 `Invalid URL`（URL spec 拒绝尾空格）
  - 表象：登录页能渲染但**点登录按钮后完全没 /api/* 请求发出**（XMLHttpRequest 构造直接挂，无 console error）
- **修法（双管齐下）**：
  1. `agentboard/web_app.py:20` 注入前 `.strip()` 防御兜底：
     ```python
     API_URL = (
         (os.getenv("AGENTBOARD_WEB_API_URL") or os.getenv("AGENTBOARD_API_URL") or "http://127.0.0.1:58124")
         .strip()
     )
     ```
  2. 启动 cmdline 写 `set NAME=VAL&&"exe"`（无空格）：
     ```powershell
     $cmdLine = "set AGENTBOARD_WEB_API_URL=http://127.0.0.1:18000&&`"$exe`" -m uvicorn ..."
     # 注意：set 后立刻 &&，不要空格
     ```
- **为什么 Playwright 验证比 PowerShell 强**：
  - PowerShell `Invoke-WebRequest` 测后端是好的（API 200 + token），但**前端构造 URL 失败这种纯前端 bug 看不见**
  - Playwright `page.on('request', lambda r: all_reqs.append((r.method, r.url)))` 能看到**实际浏览器发起的所有 URL**，没有 `/api/*` 就是前端 hang 住
  - 抓 `window.AGENTBOARD_API` 字面值：带尾空格就是 invalid URL 的物证
- **完整排查套路**（如怀疑前端 URL 构造 bug）：
  ```python
  # Playwright 脚本
  console_msgs, all_reqs = [], []
  page.on("console", lambda m: console_msgs.append(f"[{m.type}] {m.text}"))
  page.on("request", lambda r: all_reqs.append((r.method, r.url)))
  # ...
  info = page.evaluate("({api_url: window.AGENTBOARD_API, token: localStorage.getItem('agentboard_token')})")
  # 1) api_url 有 trailing space? repr() 比对
  # 2) /api/* 请求数 = 0? 前端 hang
  # 3) console errors? 可能在 catch 里 swallow
  ```
- **适用场景**：
  - 启动 web_app (28080) 后，登录按钮点了**无任何反应**（没 /api/* 请求发出）
  - 浏览器报 `Failed to execute 'open' on 'XMLHttpRequest': Invalid URL` 或 `new URL('...')` 抛错
  - PowerShell 测后端 OK 但前端死活不动
- **具体事件**：2026-08-21 f93bb0f 修 web_app key 不一致后，用户硬刷 28080 仍报「Agent 列表加载失败: Failed to execute 'open' on 'XMLHttpRequest': Invalid URL」。debug：Playwright 抓 `window.AGENTBOARD_API = "http://127.0.0.1:18000 "`（**带尾空格**），同时启动日志里 `set AGENTBOARD_WEB_API_URL=...:18000` 后面有个空格被 set 吞。修法：web_app.py 加 .strip() + 修 cmdline 无尾空格。已 commit 待 push。
