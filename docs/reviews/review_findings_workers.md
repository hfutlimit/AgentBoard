# AgentBoard workers feature 代码评审报告

评审对象（项目根 `D:\AI\Projects\AgentBoard`）：

- `agentboard\features\workers\worker.py` (509 行)
- `agentboard\features\workers\invokers.py` (221 行)
- `agentboard\features\workers\config.py` (171 行)
- `agentboard\features\workers\cli.py` (84 行)
- `agentboard\features\workers\heartbeat.py` (98 行)
- `agentboard\features\workers\maintenance.py` (97 行)
- `agentboard\features\workers\__init__.py` (78 行)
- `agentboard\features\workers\__main__.py` (7 行)
- `agentboard\features\workers\handlers\base.py` (38 行)
- `agentboard\features\workers\handlers\clarify.py` (258 行)
- `agentboard\features\workers\handlers\story.py` (467 行)
- `agentboard\features\workers\handlers\ticket.py` (208 行)
- `agentboard\features\workers\handlers\__init__.py` (32 行)

背景层通读：`features\proposals\service.py`（CAS 认领/回收）、`features\scheduling\service.py`（Run/Story 状态机）、`core\exceptions.py`、`core\service_helpers.py`、`core\state_machine.py`、`mq.py`（消费拓扑）。

评审重点：认领与并发、进程安全、命令执行、状态一致性、输入校验、错误处理。

---

## A. 亮点

- **认领 CAS 全部下沉服务端单条条件 UPDATE，判定+写入同一条 SQL**，且注释明确解释了为什么 PUT 不能仲裁（analyzing→analyzing 是幂等 no-op，N 个 Worker 会全拿 200）——`proposals/service.py:183-229`（claim_proposal）、`scheduling/service.py:526-555`（claim_story）、`proposals/service.py:560-587`（claim_ticket_request）。
- **认领先于 invoke 的次序在所有 handler 一致**：clarify.py:229、story.py:285、story.py:434 都是先 CAS 再拉起 agent，竞争失败零成本跳过，不产生重复副作用。
- **MQ「消息只是提示、DB 是事实源」+ 回查再决策 + `(proposal_id, round_no)` 唯一约束双重兜底**（mq.py:14-24、proposals/models.py:180）——at-least-once 语义下消息重投天然幂等，MQ 可随时摘除。
- **`extract_decision_json` 解析健壮**：括号配对扫描（跳过字符串内花括号）、取最后一个带 action 的对象、三级降级（带 action → 任意 dict → AgentOutputError），并带原始输出尾部便于排障（invokers.py:70-126）。
- **Windows 生产坑的显式修复**：`PYTHONIOENCODING/PYTHONUTF8` 注入子进程 + `errors="replace"` + `split_command` 双坑注释（invokers.py:129-144, 189-198）——这是实测踩坑后的工程细节。
- **崩溃自愈回路完整**：analyzing 租约回收用 `claimed_at` 而非 `updated_at` 判定（防他人作答无限续期崩溃者租约，proposals/service.py:720-763）+ failed 关键词自动重投 + `auto_retry_count` 上限 + ticket processing 超时回退。
- **决策协议校验严格**：action 白名单、ask 必须 ≥1 问题、finalize 必须非空 spec（config.py:136-162）。
- **幂等收尾**：`complete_story` CAS（scheduling/service.py:798-828）、`unclaim_story` CAS、`execute_ticket_request` 对 done 复用既有结果（proposals/service.py:465-467）。
- **MQ 断线自愈**：PikaBroker/PikaWorkflowBroker 消费循环指数退避重连（mq.py:462-539, 1208-1278），worker 各后台线程独立 try/except，单线程崩溃不拖死进程。
- **double-claim 生产事故的完整复盘沉淀在注释里**（ticket.py:185-196），认领收敛到 execute 内部 CAS，是「事故→修复→文档化」的正向循环。

---

## B. 职责 / 重复问题

- **HTTP 辅助四处复制**：`_request`/`_get_json` 在 worker.py:113-119、clarify.py:73-79、story.py:142-148、ticket.py:76-82 各写一份，应提为共享 mixin/基类。
- **ProposalProcessor 兼容转发层过厚**：worker.py:136-213 约 80 行转发到 handlers，连 `_apply_ask`/`_story_fail` 这类私有成员都要转发，旧 API 与私有符号长期共存，拆分不彻底且漂移风险高（改 handler 签名必须同步改转发层）。
- **claim 客户端包装三份且语义不一致**：clarify.py:117-131 区分 409/404；story.py:166-174 只认 200/201 其它全吞；story.py:433-442（task 版）又一种写法。服务端 `claim_story`/`claim_development_task` 更在 scheduling/service.py:526 与 projects/service.py:1271、work_items/service.py:230 重复实现（「同步自 service.py」复制粘贴模式），修 bug 需多处同步。
- **租约默认值双源**：`ProcessorConfig.lease_seconds=1800`（config.py:77）与 `DEFAULT_CLAIM_LEASE_SECONDS=1800`（proposals/service.py:58 重绑定）各一份。
- **`recover_failed` 硬编码 `window_seconds=120, max_retries=5`**（maintenance.py:68），与 router 默认值（proposals/router.py:111-113）重复，改一处漏一处。
- **与 scheduling 的职责边界**：workers 完全不触碰 `AgentRun`，边界干净但**无联动**——worker 拉起的 agent 执行结果（成败/耗时）不写入 Run，调度侧观测不到 worker 任务质量；若设计意图是「worker 执行也走 Run 状态机」，则联动缺失。
- **观察（超范围但影响信任模型）**：`/api/proposals/claim`、`reclaim-stale`、`recover-failed` 等端点无任何鉴权参数（proposals/router.py:78-123, 169-179），worker token 是可选配置——系统可能完全裸奔，任何可达 API 者都能强制回退/认领提案。
- 其他小重复：worker.py:26 与 76 重复导入 `build_handlers`；`_parse_dt`（worker.py:43-55）与 core `_parse_due_date` 语义重叠未复用。

---

## C. 问题清单

### 严重

1. `[严重] story.py:166-174, 404-457 + worker.py:216-223` **story/task 认领后无租约回收**：Story 认领（confirmed→todo）后 worker 崩溃 → 永远卡 todo（story 扫描只捞 confirmed）；task 认领（→in_progress）后崩溃/agent 失败 → 永远卡 in_progress；`task.available` 瞬时错误进死信后无任何轮询兜底（注释声称「轮询兜底会再捞」，但不存在 task 轮询）。维护回路只覆盖 proposal（analyzing 租约）与 ticket request（processing 超时）。→ **影响**：任务/Story 静默卡死，只能人工干预。→ **修复**：给 story todo / task in_progress 加 claimed_at 租约 + 服务端批量回收端点，纳入 maintenance.py 周期回收。

2. `[严重] ticket.py:141-159, 185-208` **`ticket_created` 信任不验证**：agent 谎报成功（未真正执行 MCP execute）→ 请求仍 pending → 每轮 fetch 重新拉起 agent（每轮最长 900s），无终止条件；execute 的 CAS 只挡并发、不挡谎报。→ **影响**：同一请求无限重复 agent 调用，成本/日志爆炸，提案卡 ticket_preparing。→ **修复**：ticket_created 后回查 request 状态（复用 `_lookup_ticket_request`），非 done 一律走 fail；或对 rid 加进程内去重/退避。

3. `[严重] clarify.py:200-210 + 251-258` **`_apply_finalize` 两步非原子**：PATCH converged_spec 成功、PUT status 失败 → WorkerError → mark_failed → 提案 failed 但 spec 已写入；且错误串「推进 converged 失败…」不含 AGENT_ERROR_KEYWORDS → 不被自动重投，永久 failed。→ **影响**：半写状态 + 需人工介入。→ **修复**：服务端提供单事务 finalize 端点（spec+status 同 commit），或失败时按已写 spec 重试 PUT。

4. `[严重] cli.py:68-80 + worker.py:286-306, 396-430` **无 SIGTERM 优雅停机**：只有 KeyboardInterrupt；容器/k8s 发 SIGTERM 进程直接死 → 正在运行的 agent CLI 子进程成孤儿继续执行（可能产生 ticket/task 副作用），未 ack 的 MQ 消息重投 → 重复执行；租约要等 30 分钟才回收。→ **修复**：注册 signal handler（SIGTERM→stop.set()），POSIX 用 `start_new_session`+killpg 在退出时终止子进程组。

### 中等

5. `[中等] clarify.py:240-242 + maintenance.py:61-80 + service.py:2590-2593` **超时/退出码失败不自动重投，关键词匹配脆弱**：`AgentInvocationError("Agent 调用超时（>900s）…")`、`"Agent 退出码 N…"` 均不匹配 AGENT_ERROR_KEYWORDS（只有启动类+宽泛的「找不到」）→ 一次 15 分钟超时即永久 failed；反之「找不到」子串过宽（如「找不到史诗」）会误触发自动重投。→ **修复**：按异常类型区分 retryable（AgentInvocationError 全类可重投），弃用字符串匹配。

6. `[中等] clarify.py:186-198, 237-249 + proposals/service.py:271-314` **轮次上限护栏可被 agent 回传的 round 击穿**：护栏比较服务器 `current_round`，而 `_apply_ask` 透传 `decision.round`（create_proposal_round 按 max() 推进）——agent 持续回传旧 round → current_round 停滞 → 无限 ask 循环，max_rounds=5 永不触发。→ **修复**：Worker 自算 round（服务器 current_round+1）并忽略/覆写 agent 的 round 字段。

7. `[中等] heartbeat.py:59-97 + worker.py:251-256` **心跳串行全量探测阻塞主循环**：每周期对每个 agent 串行跑子进程（最坏 8s/个）+ 串行 HTTP 上报，N 个 agent 单轮最长 8N 秒；poll 模式下 `agent_heartbeat_once` 内联在 `poll_once` 里，阻塞整个提案轮询。→ **修复**：poll 模式复用 MQ 模式已有的 `_agent_heartbeat_loop` 独立线程。

8. `[中等] invokers.py:205-209` **子进程输出无大小上限**：`capture_output` 全量读入内存，失控 agent 打印 GB 级输出 → OOM 拖死 worker；也无 POSIX rlimit。→ **修复**：限制 stdout/stderr 读取量（如 1MB 截断）或 preexec_fn 设 RLIMIT。

9. `[中等] story.py:446-457` **direct task 重投重复处理 in_progress 任务**：MQ at-least-once 下 worker 在 ack 前崩溃 → task.assigned 重投 → 状态仍 in_progress → 无条件再 `process_task` → 重复拉起 agent、重复落评论。→ **修复**：direct 路径校验认领者/状态（仅 backlog/todo 可处理），评论带幂等键。

10. `[中等] ticket.py:161-170 + clarify.py:212-219 + worker.py:352-360` **写失败被静默吞掉但返回成功语义的结果码**：`_fail_ticket_request`/`mark_failed` 仅 log.error 就返回 "failed"，请求实际仍 pending → 下轮 fetch 再拉起 agent → 高频重试无退避（ticket 场景每 10s 一轮、每轮一次最长 15 分钟 agent 调用）。→ **修复**：写失败抛异常或置内存退避，让调用方跳过该实体一段时间。

11. `[中等] invokers.py:123-126, 211-220 + clarify.py:235-242, 255-258` **错误信息泄露内部细节并落库展示**：异常消息带完整命令模板 `self.cmd`、CLI stderr 尾部 400 字符、stdout 片段，经 `mark_failed(str(e))` 直接写进提案 error（≤2000 字符）展示给用户/前端。→ **影响**：内部路径、CLI 细节、可能的敏感输出外泄。→ **修复**：用户可见摘要 vs 日志详情的错误分级，落库前截断/脱敏。

12. `[中等] story.py:136-138, 356-369` **失败计数是进程内状态**：多 Worker 实例各计各的 → N 个实例最多 3N 次失败才 blocked，重试上限随实例数放大；且 `_story_attempts`/`_story_fail_counts` 无界增长（长跑内存泄漏）。→ **修复**：失败计数落 DB（story 字段或计数端点），内存 dict 定期清理。

### 轻微

13. `[轻微] worker.py:299-300, 357-360 + story.py:369-372` **周期异常无去重 log.exception**：某实体持续失败时每 10s 打全栈 → 日志爆炸。→ 同实体失败限频（首次全栈，后续摘要）。

14. `[轻微] maintenance.py:83-97` **sweep 只覆盖 clarify 域且受 batch_size 截断**：只重投 queued/answered proposal，不重投滞留 pending ticket request / confirmed story；超过 batch_size 的滞留项永不重投。→ 改服务端「按状态批量重投」覆盖三域。

15. `[轻微] worker.py:257-259` **poll_once 每轮 3 个维护 POST 无节流**：10s 一轮 × N 个 worker 持续打 reclaim/recover 端点，数据库扫描负载放大；与 maintenance_interval（60s）语义重复。→ poll 模式按 maintenance_interval 节流维护调用。

16. `[轻微] heartbeat.py:27-31, 87-91` **`{model}` 占位符先替换后 split**：model 含空格/引号会改变 argv 结构（无 shell 注入但可追加参数）；且探测失败立即 deregister——CLI 冷启动 >8s 时 agent 每 60s 抖动下线。→ 先 split 后按占位注入；deregister 前加连续失败 N 次判定。

17. `[轻微] invokers.py:31-37 + worker.py:78` **全局 `_prompt_builder` 进程级单例**：同进程构造第二个 ProposalProcessor（测试/多租户）会互相覆盖 prompt 构建器。→ 改为实例级注入。

18. `[轻微] clarify.py:42-46 + story.py:68-80, 110-116 + ticket.py:54-60` **提示词输入无长度上限**：proposal content/title、story/task description 直接内插，超长正文 → 巨型 prompt → agent 输入溢出/超时；仅 recall 段有截断（story.py:244）。→ 统一截断（如 4000–8000 字符）+ 超长告警。

19. `[轻微] config.py:77-83` **agent_timeout/lease_seconds 无下限校验**：0 或负值直接生效（服务端才拦负数），配置错误难以及时发现。→ from_env 后 clamp/校验。

---

## D. 成熟度评级：beta

**理由**：并发与幂等的骨架（CAS 认领、幂等收尾、租约回收、MQ at-least-once 兜底、断线自愈）设计正确且经过生产事故复盘（double-claim、Windows 编码、MQ 断线都是实测修复），测试意识与维护闭环完整；但存在四个未闭合的故障模式——story/task 认领后无回收（卡死）、ticket_created 不验证（无限重拉）、finalize 非原子半写、无优雅停机（孤儿进程+重复执行）——这些是「能跑、常规路径正确、但真实故障下会卡死或重复执行」的特征，属 beta 而非 stable。距 stable 还差：任务级租约、决策验证闭环、信号处理、异常分类重试。

## E. 总结

**CAS+租约+消息兜底的并发骨架扎实、生产意识强（beta 上沿），但 story/task 无租约回收、ticket_created 信任不验证、finalize 两步非原子、无 SIGTERM 优雅停机四个故障模式会在真实故障下造成卡死或重复副作用，需优先闭合。**
