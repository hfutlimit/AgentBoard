# Design: Proposal 澄清 Worker

## 1. 调度模型

```
                ┌──────────────── poll_once() ────────────────┐
                │                                             │
   reclaim_stale()            fetch_work()                handle(p)
   analyzing 超租约      ┌── GET /pending  (queued)    ┌── claim  → analyzing
        → queued         └── GET ?status=answered      ├── build_context (全量重放)
                                                       ├── invoker.invoke → decision
                                                       └── ask / finalize / fail
```

每个周期先做**崩溃恢复**再消费，保证被遗弃的提案总能在下一轮回到候选池。

## 2. 为什么必须同时扫 `queued` 和 `answered`

`GET /api/proposals/pending` 只返回 `queued`。但状态机里，用户答完一轮后提案会自动落到
`answered`，而 `answered → analyzing` 正是「进入下一轮澄清」的入口。

若 Worker 只消费 `queued`，第一轮之后整条链路就再也不会被驱动——提案永远停在 `answered`，
表面看「用户已作答」，实际是死锁。测试 `test_fetch_work_covers_both_queued_and_answered`
把这条边钉死。

## 3. 全量重放：为什么不做增量会话

三个理由：

| 维度 | 全量重放 | 增量会话 |
|---|---|---|
| 崩溃恢复 | 天然幂等，任意 Worker 接手结果一致 | 需要持久化会话上下文并做恢复 |
| 横向扩容 | Worker 无状态，随便加 | 会话与 Worker 绑定，需粘性路由 |
| 重投防护 | 叠加 `(proposal_id, round_no)` 唯一约束即可 | 需额外去重表 |

代价是每轮 prompt 变长。以澄清场景的轮次上限（默认 5 轮）估算，
完全在可接受范围内。

## 4. Agent 契约：一次调用、一次决策、纯 JSON 收口

Worker 不理解 Agent 内部，只认一个 JSON 决策对象：

```jsonc
{"action":"ask","questions":["..."],"summary":"..."}          // 继续澄清
{"action":"finalize","converged_spec":"..."}                  // 收敛，等人工终审
{"action":"fail","error":"..."}                               // 无法处理
```

### 输出抽取为什么不用正则

真实 CLI（WorkBuddy / Claude Code / Codex）的 stdout 混着进度日志、思考过程、
` ```json ` 包裹。正则做不了嵌套括号配对，也会被字符串里的 `{}` 带偏。
`extract_decision_json()` 用**手写括号配对扫描 + 字符串态跟踪**，从后往前取第一个
带 `action` 的可解析对象（Agent 往往先思考再给结论）。

### prompt 走 stdin 而非命令行参数

超长 prompt 在 Windows 上会被命令行长度限制截断，且引号转义极易出错。stdin 一劳永逸。

## 5. 崩溃恢复：租约而非心跳

选择「`updated_at` 停滞判定」而非独立心跳表：

- 不需要新表、不需要新端点（**零契约变更**的硬约束）；
- `analyzing → queued` 这条回退边在 `PROPOSAL_TRANSITIONS` 里**本来就预留了**
  （注释写着「超时回退，复用 DaemonScheduler」），本实现正是它的第一个真实消费者；
- 副作用可控：Agent 正常工作时不会更新 `updated_at`，所以租约必须 >> 单次 Agent 耗时，
  默认 30 分钟对应 `agent_timeout` 默认 15 分钟，留了 2 倍余量。

`test_fresh_analyzing_is_not_reclaimed` 反向钉死「未到期不能抢」，
避免把租约调得过激导致两个 Agent 同时分析。

## 6. 失败语义：宁可 failed，绝不卡死

`handle()` 用三层 `except` 把所有路径收敛到 `mark_failed`：

1. `AgentInvocationError` / `AgentOutputError` —— 可预期失败（超时、退出码非零、输出非法）；
2. 适配器实现方的**意外异常** —— 第三方 Invoker 不可信，同样兜住；
3. 落库阶段异常 —— 回写问题/写规格失败也不能让提案留在 `analyzing`。

`failed` 在状态机里可回退 `queued` 重投，所以「转 failed」不是终点，是可恢复的挂起。
反面案例是留在 `analyzing`：既不在任何 Worker 的候选池里，又要等满整个租约才被救回来。

## 7. 配置项

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `AGENTBOARD_API_URL` | `http://127.0.0.1:58124` | AgentBoard REST 地址 |
| `AGENTBOARD_WORKER_TOKEN` | 回退 `AGENTBOARD_MCP_TOKEN` | 服务账号 `abk_` key |
| `AGENTBOARD_WORKER_AGENT` | `worker` | 落到轮次记录上的 agent 标识 |
| `AGENTBOARD_WORKER_INTERVAL` | `10` | 轮询间隔（秒） |
| `AGENTBOARD_WORKER_BATCH` | `5` | 单轮最多处理提案数 |
| `AGENTBOARD_WORKER_LEASE` | `1800` | analyzing 租约（秒） |
| `AGENTBOARD_WORKER_MAX_ROUNDS` | `5` | 澄清轮次上限 |
| `AGENTBOARD_WORKER_AGENT_CMD` | 空 | 无头 Agent 命令模板 |
| `AGENTBOARD_WORKER_AGENT_TIMEOUT` | `900` | 单次调用超时（秒） |

未配置 `AGENT_CMD` 且未显式传 `invoker` 时**构造期就抛 ValueError**，
而不是跑起来后静默空转——运维最怕「进程活着但什么也没干」。

## 8. Windows 命令拆分的双坑

`shlex.split` 在两种模式下各有一个坑，必须一起填：

- `posix=True`：把 `C:\Users\x` 的反斜杠当转义符吃掉 → 路径损坏；
- `posix=False`：引号原样留在 token 里 → `subprocess` 拿到 `"C:\...python.exe"`
  （含字面引号）→ `WinError 2 系统找不到指定的文件`。

`split_command()` 在 Windows 上走 `posix=False` 再剥掉成对外层引号。
这个 bug 在首轮测试中真实触发了 4 个用例失败，值得留档。

## 9. P2（RabbitMQ）衔接点

只需把 `fetch_work()` 换成从队列 consume，其余全部复用：

- `claim()` 仍是必要的（at-least-once 投递会重复）；
- 全量重放让「消息只带 `proposal_id` + `round`」成为可能，消息体不需要携带上下文；
- 租约回收可继续保留，作为 MQ ack 之外的第二道兜底。

同时应在 P2 引入**服务端 CAS 认领端点**（如 `POST /api/proposals/{id}/claim`
带 `expected_status`），彻底消灭本实现里的 TOCTOU 窗口。
