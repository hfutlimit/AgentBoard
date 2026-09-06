# AgentBoard Worker-Owned 自动化流程 代码评审报告

评审日期：2026-09-06

评审对象（项目根 `E:\Projects\AgentBoard`）：

- `src\nodes\AgentBoard.Node\WorkerOwned\WorkerOwnedService.cs`（460 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\LocalWorkerRuntime.cs`（140 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\ConfigurationPortal.cs`（118 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\ConfigurationPortal.html`（内联 SPA）
- `src\nodes\AgentBoard.Node\WorkerOwned\DesignDocumentPublisher.cs`（89 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\WorkPlanner.cs`（170 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\WorkJournal.cs`
- `src\nodes\AgentBoard.Node\WorkerOwned\LocalConfigurationStore.cs`（84 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\LocalWorkConfiguration.cs`（115 行）
- `src\nodes\AgentBoard.Node\WorkerOwned\LocalAgentCatalog.cs`
- `src\backend-fastapi\agentboard\features\work_items\comment_format.py`（47 行）
- `src\backend-fastapi\agentboard\features\scheduling\worker_work.py`（complete 路径）
- `src\frontend\src\app\shared\utils\worker-comment-markdown.ts`（44 行）
- `src\frontend\src\app\app.ts`（renderMarkdown 引用方）

评审范围：commit `7f50c0c..HEAD`（约 14 个 commit，集中在 Worker-Owned 七类 orchestration、portal 启停、Design 文档化、评论 Markdown 化）。

Happy path 链路：

```
用户点"保存并启动" → LocalWorkerRuntime.StartExecutionAsync
  → WorkerOwnedService.ExecuteAsync
      ├─ preflight 协议版本检查
      ├─ Register worker + agents + instances
      └─ 并行 ReconcileLoop（轮询 /snapshot + POST /offers）+ Consume（basic.get）
  → Execute 调 adapter → ValidateResult → Design 走 DesignDocumentPublisher
  → POST /complete（server 调 format_worker_comment 写评论）
  → 前端 renderCommentMarkdown → renderMarkdown → 用户看到
```

---

## A. 亮点

- **协议版本守门员**：preflight 检查 `worker-work.discussions.v1`，不满足直接抛 "Deploy the discussion-capable FastAPI/MCP migration before starting this Worker"，把"老 server 不能 fence discussion turns"的不变量挡在 Execute 之前（WorkerOwnedService.cs:62-71）。
- **CAS 租约贯穿**：work_id + agent_id + token 三元组 claim，错误信息按原因分级（owner mismatch / independent required / identity / original participant），日志可读（WorkerOwnedService.cs:294-302）。`new_token_required` 触发后清 journal，强制重新认领，避免 stale token 复用（307 行）。
- **Fenced lease + 失败回滚语义清晰**：`HttpRequestException / TaskCanceledException` 透传（保留本地结果供重放），其他异常才走 `/fail` + 清理 journal（WorkerOwnedService.cs:396-409）。`_state.LastError` 与 portal 状态联动。
- **队列星型化防饿死**：把 `BasicNack(requeue=true)` 改成 `ReturnToTail`（durable 重新入队 + 原条 ACK），1 个 unack slot 不会把不能 claim 的消息卡在队首（WorkerOwnedService.cs:254-262）。
- **Design 文档幂等 + 写评论双向锚定**：workId + commit + markdown 算 SHA-256 当 marker，搜旧文档用 content 字面匹配，避免不同 work 误复用（DesignDocumentPublisher.cs:35-83）。读回做严格相等 check 防 server 中途改写。Task 评论里同步写链接。
- **portal 本机配置零凭据**：`IsLocalRequest` 同时校验 loopback IP + host + origin + 非 GET 加 `X-AgentBoard-Local-Portal` 头，防 DNS rebinding / 远程网站调用 / CSRF（ConfigurationPortal.cs:12-23）。`LocalConfigurationStore.Read` 在返回前清空 `agent.Runtime.AgentBoardToken`，浏览器/磁盘都不落业务凭据（LocalConfigurationStore.cs:30-32）。
- **Edit lock + runtime lock + process lock 三层互斥**：避免两个进程 / 两个 portal 同时改配置 / 启动同一个 worker（LocalConfigurationStore.cs:75-79、LocalWorkerRuntime.cs:66-68、WorkerOwnedService.cs:60-61）。
- **快照化的 prompt 输出**：`ExecutionInstructions` 按 kind 切换强约束，禁止"评审 = 自己写一段总结"等擅自改协议行为（WorkPlanner.cs:158-168）。讨论 turn 有专用 prompt（reviewer vs author 角色明确，150-155）。
- **前端 Markdown 渲染安全**：链接 / 图片协议白名单 + 属性逃逸字符过滤，规避 `[x](https://a.com/"onclick=alert\`1\`)` 这类 XSS 注入（app.ts:8286-8291，注释把攻击向量写明白）。
- **故障信号不泄露**：`Reap` 错误信息只暴露异常类型名，不外泄 URI / 凭据（LocalWorkerRuntime.cs:103-105）。
- **评论 Markdown 化前后端职责分明**：server 端 `format_worker_comment` 给新评论写 Markdown，前端 `renderCommentMarkdown` 给历史 JSON 评论做转换并兼容普通 Markdown / 不相关 JSON / 围栏代码块，单元测试覆盖四类输入（worker-comment-markdown.spec.ts:17-27）。

---

## B. 职责 / 重复问题

- **HTTP 客户端每次 candidate 都新建**：`using var client = Client(profile);` 在 `Execute` 的 foreach 内部（WorkerOwnedService.cs:291），N 个 candidate 就有 N 次 base address + 凭据注入 + dispose。`IHttpClientFactory` 的设计意图是复用，但这里走的是裸 `CreateClient()` 仍然每次 new。规模小不痛，但意义模糊。
- **状态字符串硬编码三处**：`"completed" / "failed" / "in_progress" / "todo" / "in_review"` 等字面量散落在 WorkerOwnedService.cs:188, 311, 315, WorkPlanner.cs:91-112。应该由 `WorkerWorkKinds` / `WorkerWorkStates` 之类的常量集中。Server 端若重命名，worker 这边编译不报错、运行沉默 fail。
- **`status: running` / `starting` 文案在 portal 与 runtime 各写一份**：`ConfigurationPortal.html` 里的 `labels` 字典与 `LocalRuntimeStatus.State` 的可能值是隐式契约。任一处新增状态，另一处不会立刻发现。
- **"本地锁文件路径"三处算**：`WorkerOwnedService` 的 `.worker-owned.lock`、`LocalWorkerRuntime` 的 `.runtime.lock`、`LocalConfigurationStore` 的 `.edit.lock`，逻辑相似但各自实现（FileStream + FileShare.None + IOException 捕获）。
- **format_worker_comment 的 LABELS / DECISIONS 字典与 worker-comment-markdown.ts 重复**：两份双语 label map、decision 字符串白名单，任意一边改字段另一边不同步就会导致 server 写"提交评审"、前端显示"submit"。
- **`foreach (var entityType in new[] { "proposal", "task" })`**（WorkerOwnedService.cs:163）：裸字符串数组，没有走 `WorkerWorkKinds.EntityTypes`（如存在）。同样问题。
- **`/api/local/projects` 的内联分页**（ConfigurationPortal.cs:99-110）：while + offset，20000 上限写死。建议改 `_options` 上限或封装 `Pager`。

---

## C. 问题清单

### 严重

1. **Server 422/409 走 `/fail` 路径，本地结果被吞**（WorkerOwnedService.cs:389-409）。Worker 本地 `WorkPlanner.ValidateResult` 已通过，但 server 端按自己的契约再校验失败时，本地 `_journal.Remove(workId)` + POST `/fail` 让 Agent 真的"白干"：本地结果被清、用户看到的是 fail 评论，原始 evidence 丢失。Dev / QA 跑了十几分钟的任务尤其痛，下次 Claim 是新 token，Agent 看不到自己的旧 stdout。**根因**：422 语义混用了"客户端结构错"和"服务端语义不收"，两者处理方式应不同。**修复方向**：捕获 422 时把本地 result 写进 `state.LastError` + 写一条 task comment（含 `output` 摘要 + 服务端拒绝原因），journal 保留 N 周期供人工 pull，让 operator 决定 re-offer 或强制 fail。

2. **Design 文档 readback 严格相等比对触发误判 fail**（DesignDocumentPublisher.cs:59-64）。读回后比对 `title` / `content` / `project_id` / `story_id` / `type` 必须**字面相等**。Server 端若对 Markdown 做行尾规范化、加水印、HTML 转义，会让 readback 不等，抛 "Design document differs from the frozen result; preserved without overwriting, submission deferred" —— 整条 work 走 fail 路径，本地 journal 删除。**根因**：`Content` 字段不应当做严格相等比较（server 端 round-trip 安全修改应被允许）。**修复方向**：比对 `title` + 设计 marker + 关键 section 锚点，**不比对** content 全等；或将差异写到 task comment 让 reviewer 看到原因再决定。

3. **preflight 之前就打开 `*.worker-owned.lock` 文件，validate 失败留尸**（WorkerOwnedService.cs:60-65）。`processLock = new FileStream(... + ".worker-owned.lock", FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None)` 早于 `_options.Validate()`。`Validate` 抛 `Map a project before starting execution` 等配置错误时，`processLock` 已经 `using`，会在 `ExecuteAsync` 退出时 dispose —— 看似没事，但若 Validate 抛**前**有更早异常（preflight 内部 `HttpRequestException` / `JsonException`），锁已建文件、Validate 后续不再跑，**下次启动被同一锁阻塞**直到人工介入。**根因**：validate 顺序错位 + 锁副作用先于校验。**修复方向**：在 Validate 通过后再获取 processLock；或加 try/finally 在构造锁失败时主动 delete 文件。

### 中等

4. **ReconcileLoop 串行 per-project 扫描延迟 LastScanAt**（WorkerOwnedService.cs:159-202）。`foreach (var project in _options.Projects)` 内嵌套 `foreach (var entityType in new[])` + `do { ... } while (cursor > 0)`，且最外层 `Task.Delay`。5 个 project × 2 entity_type × 多次 cursor 时，单次轮询可能 10-30s。`LastScanAt` 只反映"完成时间"，portal 看到的状态机长期卡在 `starting`（LocalWorkerRuntime.cs:114-119 的 60s 阈值内）。**根因**：per-project 串行 + delay 在最外层。**修复方向**：每个 project 独立后台循环，共享 broker 与 registry；LastScanAt 拆 per-project；UI 状态分 `connecting` / `starting` 两态。

5. **`renderCommentMarkdown` 对每条评论都跑 JSON.parse**（worker-comment-markdown.ts:34-39）。虽然有 `startsWith('{')` 早返回，但只要 source 以 `{` 开头就触发 `JSON.parse`。评论数 1000+ 的 Tab 是真实开销，移动端可见。**根因**：缺"Worker JSON 指纹"标记。**修复方向**：server `format_worker_comment` 产出的 comment 总以 `###` 开头，前端对有这个前缀的评论跑 parse + format，否则原样返回（与正常 Markdown 走同一路径）。要么 server 额外写一个 `is_worker_report: true` 标记位（结构化），要么约定 `### 设计/开发/QA结果` 前缀。

6. **`design_document_url` 渲染为纯文本**（comment_format.py:43-46 + app.ts:8286-8291）。`format_worker_comment` 把 URL 写成 `#### 设计文档链接\n\nhttps://...`，前端 `renderMarkdown` 的 inline 规则只处理 `[text](url)`，**裸 URL 不会被自动转链接**。用户看到完整 URL 但要复制粘贴。**根因**：format / render 职责割裂 —— format 不知道 render 规则，render 没有 auto-link。**修复方向**：`format_worker_comment` 输出 `[设计文档]({url})` 文本；或 render 层加 auto-link 规则（带域名白名单避免歧义）。

7. **跨 worker 启动锁 await 阻塞整个 portal**（LocalWorkerRuntime.cs:65-78, gate 是 SemaphoreSlim(1,1)）。`processLock` 用 `FileShare.None` 同步打开，若配置目录在 NFS / 网盘（"Worker 跑在 NAS"场景），OpenOrCreate 可能挂 30s+。期间 `Status` / `GET /configuration` / `GET /projects` 全部排队等 `gate`。**根因**：`FileShare.None` + 无超时 + gate(1,1) 串行化。**修复方向**：FileStream 操作包 `Task.Run` + `WaitAsync(timeout)`；超时后让 Status 直接返回 stale + 提示用户重试；改 `gate` 为更细粒度锁（configuration 读 / processLock 写 分开）。

8. **首次 `LastScanAt` 之前 portal 看不到状态变化**（LocalWorkerRuntime.cs:112-119）。`Status()` 把 `LastScanAt is null || > 60s` 视为 `starting`。但第一次 `ReconcileLoop` 跑完才更新 LastScanAt —— 5-10s（取决于网络）。这期间 `runtimeStatus` 一直显示"正在启动或恢复连接"，用户反复刷新以为是卡死。**根因**：`LastScanAt` 只覆盖"完成时间"，没有"开始尝试时间"。**修复方向**：暴露 `_lastReconcileAttemptAt`（或注册 ILocalWorkerRun 事件），状态机分 `connecting`（已发起）/ `starting`（首次完成）/ `running` 三态。

9. **所有候选 Forbidden → silent requeue，3 次满后置 blocked**（WorkerOwnedService.cs:286-417）。`return false` 让 Consume 把消息 `ReturnToTail`，**work 一直 requeue 直到 3 次 attempt 满后 server 置 blocked**。期间评论里没有，Task status 也不变（一直 `todo` / `in_review`）。是 happy path 之外的"无声失败"。**根因**：Forbidden 是"暂时拒绝"语义，但当所有候选都拒绝时，应该作为可观察事件上报。**修复方向**：循环结束后若全部 Forbidden，写一条 task comment "无可用 Agent 领取（owner mismatch / independent required / 等）" + 触发 `state.LastError` 暴露给 portal，让 UI 显示原因而不是单纯 waiting。

10. **`format_worker_comment` 的 null vs missing 不区分**（comment_format.py:17-30）。`_value(null) → "未提供"`、`_value({}) → "无"`、`_value([]) → "无"`。Worker 提交 `{"summary": null, "decision": "submit"}` 时，用户看到 `### 执行结果 · 提交评审\n未提供\n#### ...`，**null summary 应是"无 summary"语义，但渲染成"该 agent 没提供说明"**——可读性差。**根因**：null vs missing 在 Markdown 模板里没区分。**修复方向**：把"用户友好摘要"与"原始证据完整性"分开：null 渲染为空（直接 skip section），missing 渲染为"未提供"（说明 agent 漏字段）。

11. **`renderMarkdown` 的列表嵌套 markdown 渲染顺序问题**（app.ts:8324-8336）。无序 / 有序列表扫描用 `while + inline` 一次性拼接，遇到缩进嵌套（如 `- foo\n  - bar`）会平铺。Worker 提交 `defects: [{ title, description: "步骤:\n- 步骤1\n- 步骤2" }]` 时，外层是"#### 问题与阻塞"下的 numbered list，内层 markdown list 不会被识别为嵌套 `<ul>`，而是作为段落里 raw markdown 字符。**根因**：renderMarkdown 是行级扫描，没跟踪 indent 状态。**修复方向**：加缩进感知的 list 栈；或要求 description 字段用 HTML `<ol>/<ul>`（破坏 Markdown 一致性），或由 `format_worker_comment` 在 description 前置 indent（4 空格）。

### 轻微

12. **portal 3s polling 在 stopped 状态仍持续**（ConfigurationPortal.html:99）。`setInterval(refreshRuntime, 3000)` 只判断 `!document.hidden && !runtimeBusy`，**不感知 worker state === 'stopped'**。用户关闭 worker 后 interval 还在每 3s 打 `/api/local/runtime`，直到页面关闭或 reload。**根因**：portal 状态机里有 `stopped` 态，但轮询器不感知。**修复方向**：runtime 状态进入 `stopped` 时 clearInterval，或 polling 间隔随 state 调整（stopped = 30s，running = 3s）。

13. **没有"force re-publish"路径**（DesignDocumentPublisher.cs:42-58）。幂等搜索：workId + digest 命中已有文档就复用。如果用户发现设计文档被手改、想看 Agent 重新出新版，**只能去手动 delete document → 重跑 task**。happy path 缺一个 explicit "force re-publish" 入口。**根因**：幂等是默认行为，但没有 override 通道。**修复方向**：task comment 加 `[Force re-publish design]` 标记，Worker 检测到后跳过搜索直接 create；或任务 description 里加 `_force_republish: true` 字段，Worker 解析后行为变化。

14. **rerun 失败重试时无 retry-after hint**（WorkerOwnedService.cs:259-262）。`ReturnToTail` 后 `await Task.Delay(TimeSpan.FromSeconds(2), ct)`。这是固定 2s 硬编码，server lease 30s 过期前没区分"server-side 主动收回" vs "本机暂时无 candidate"。**根因**：retry 策略单一。**修复方向**：失败原因映射 backoff（Forbidden=10s, Conflict=2s, HttpRequestException=5s），或由 server 返回 `Retry-After` 头。

15. **`Reap` 不区分 `OperationCanceledException` 与真实故障**（LocalWorkerRuntime.cs:100-105）。`RunCompletion.IsFaulted` 包含取消（用户主动 stop）也算 faulted，状态机会短暂显示 "failed" 然后被下一次状态请求覆盖回 "stopped"。**根因**：取消语义未识别。**修复方向**：检查 `completion.IsCanceled` 走 `stopped` 分支，仅 `IsFaulted` 走 `failed`。

16. **`ConfigurationPortal.html` 一次性 save + start 缺 dry-run**（ConfigurationPortal.html:startWorker 段）。`保存并启动` 一气呵成，配置错误要等服务端 reject 才反馈。**修复方向**：start 前先做一次只读 dry-run（验证 project path 存在、agent CLI 可执行、broker URI 合法、server 协议版本匹配），失败弹 toast 不启动。

17. **多 worker 共享 project 时 workspace lock 路径碰撞**（WorkerOwnedService.cs:434-445）。`LockWorkspace` 按 workspace 路径 SHA-256 算 lock key，**多 worker 同时跑同一个 project 互相阻塞**直到一个完成。**根因**：lock 是 per-workspace 而非 per-(workspace, work)。**修复方向**：把 work_id 纳入 key，或用租约已经在 server 端 fence，worker 这边只做"防止同时修改"的轻锁（用 NOLOCK 文件 + 短超时）。

18. **DesignDocumentPublisher 写评论前的 GET 全量拉取**（DesignDocumentPublisher.cs:71-83）。`GET /api/tasks/{taskId}/comments` 拉到全部评论，遍历找内容匹配。评论 1000+ 时每次 Design 完成都是一次 O(N) 扫描。**根因**：缺幂等键（用 marker 在 server 端做 content 哈希去重）。**修复方向**：server 端 `POST /api/tasks/{id}/comments` 支持 `idempotency_key`，Worker 用 marker 哈希作 key；或 server 给 comments 加 `marker` 索引。

19. **`format_worker_comment` 序列化 "无" / "未提供" / "是" / "否" 不是 i18n key**（comment_format.py:17-30）。直接是中文字面量，未来多语言要全表替换。**根因**：i18n 没规划。**修复方向**：把 `LABELS` / `DECISIONS` 提取为 dict 注入，server 与前端共享 schema（或前端 override）。

20. **`return new(...)` 的 WorkOffer 默认 `TargetAgent=null`**（WorkPlanner.cs:111-112）。非 discussion 任务走 `Subscriptions()`（无 target agent），由 Consume 端轮询所有 candidate。Discussion 任务走 `target_agent` 显式路由。**类型 nullability 边界**对 reviewer 可读性不够友好 —— `target` 出现 = discussion，否则 = 普通 offer，应该用不同 record 类型。**修复方向**：拆 `WorkOffer` / `DiscussionOffer` 两个 record，consume 端各自循环，互不污染。

---

## D. 用户体验优化方向（持续改进 backlog，不属代码缺陷）

按"投入产出比"从高到低排，每条独立成 PR。

### D.1 实时可见性（高 ROI）

- **执行中直播**：Execute 阶段把 stdout 最后 N 行 / 当前 step 写到 task ephemeral state（SSE 或 WebSocket），portal 显示"正在跑: `npm test`... 12/30 tests passed"。当前用户要等评论出来才知道有没有卡住。
- **per-Agent 活动视图**：portal 加 "Worker 活动" tab，显示每个 agent 当前在跑什么、累计成功/失败数、最后 24h 吞吐。
- **断线状态细分**：状态机加 `connecting`（已发起首次扫描）/ `reconnecting`（broker 断）/ `syncing`（journal replay）。当前全归到 `starting`，用户看不出差异。

### D.2 失败可恢复（高 ROI）

- **一键 Retry**：fail 评论加 "Re-offer this work" 按钮，直接 POST `/api/worker-work/offers` 同 kind/iteration+1。避免用户去 UI 改状态、再等 reconcile。
- **失败原因展开面板**：点 fail 评论展开原始 stdout 末 200 行、stderr、Agent 名、Provider、错误堆栈。当前 fail 评论只有 Markdown 摘要，排查要 ssh 上机器看日志。
- **设计文档差异比对**：rework 重新提交 design 时，把"新文档 vs 旧文档" diff 嵌在 review 评论里（reviewer 直接看到改了什么），而不是只能看到完整新版。

### D.3 配置与启动（中等 ROI）

- **预启动健康检查**：`Start` 按钮 click 时先做 dry-run check（server 连通、broker URI 合法、project path 存在、agent CLI 可执行）→ 通过才真正启动，失败原因直接弹 toast。
- **diff-when-restart**：保存配置时如果 `revision` 变化，在 portal 顶部显示"即将生效: 删了 Agent X / 改了 Y 的 pre prompt"，给个 "Show diff" 展开。
- **失败启动诊断面板**：`runtimeStatus` 旁边加红点"上次启动失败: NullReferenceException" + "查看日志" 跳到本机 log 路径，而不是只显示错误类型名。

### D.4 评审与协作（中等 ROI）

- **讨论 turn 的"@提醒"**：reviewer 提 discuss 时，如果 owner agent 在 portal 看不到（它是 Agent 不是人），应该 push 到对应 agent instance 的 inbox，下次 reconnect 拉走 —— 避免 reviewer 等 30 分钟没人回。
- **跨 Story 的 design 引用**：很多 design 是跨 Story 复用的（同一项目的接口文档），portal 加 "Used in N Stories" 链接。当前要靠 reviewer 手动 cross-ref。
- **Story 完成的 sanity check**：worker 报 "all done" 自动 close story 时，给 owner 一条 "Story X closed by Worker A. 1 day 内可 reopen" 通知。当前是 silent close。

### D.5 大局视图（低优先级，高 effort）

- **Worker 拓扑图**：portal 加一个图，显示 worker-hostname → agents → projects → active tasks。当前是"portal + 4 个 list"散落信息。
- **跨项目 SLA 看板**：每个 project 的"任务平均完成时间 / 阻塞率 / QA 通过率"，30 天趋势。

---

## E. 成熟度评级：stable（happy path 仍需闭合 3 个严重问题后升级）

**理由**：骨架完整度显著高于 legacy workers（CAS 认领、租约回收、fenced lease、MQ 拓扑、portal 隔离、Design 文档化、评论 Markdown 化都有），且最近 14 个 commit 集中在可观察改善（设计交付物变文档、评论可读、portal 启停），测试覆盖与生产验证到位（`worker-owned-production-validation-20260905.md`）。但三个严重问题都在 happy path 边缘、且对真实故障场景影响大：

- **Issue 1（422 → fail 吞结果）**：是 happy path 的"隐藏地雷"，Dev/QA 重任务一旦命中即白干十几分钟
- **Issue 2（Design readback 严格比对）**：依赖 server 端不做 round-trip 修改，是隐性假设
- **Issue 3（锁副作用先于 validate）**：单点配置错误 → 永久锁死，运维成本高

**距 stable 还差**：Issue 1-3 闭合 + 严重问题回归测试 + Issue 4-6（中等等级）至少闭合其中 3 个（直播 + Retry 按钮 + 设计 diff）。Issue 7-11 持续改进即可。

---

## F. 总结

**Worker-Owned 自动化流程主线（portal 启动 → 用户看到评论）是正确的**，最近一批改动在三个具体可观察点（评论 Markdown 化、Design 文档化、portal 启停控制）完成度很高，代码与测试质量在线。

**真正需要在 happy path 修的硬伤**：
1. server 422 误入 fail 路径 → 吞掉 worker 本地结果（Issue 1）
2. Design 文档 readback 严格相等比对 → 误判 fail（Issue 2）
3. 锁副作用先于配置 validate（Issue 3）
4. `design_document_url` 渲染为纯文本（Issue 6）

**真正需要补的 UX 缺口**：
1. 执行中无可见性 → 直播 stdout / step
2. 失败不可恢复 → Retry 按钮 + 原因面板
3. 启动慢无诊断 → 预检 + 失败原因展开

**如果只挑一个先做**：**Issue 1（422 → fail 路径吞结果）优先级最高**，因为它在 happy path 边缘发生，影响所有 work kind，且对 Dev/QA 任务是真实的时间损失；其次 Issue 6（设计文档链接不渲染为超链），修复成本极低、用户感知明显。
