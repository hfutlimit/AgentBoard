# Proposal — Executor 常驻 daemon 模式（--daemon 循环认领 pending run）

**status**: in_review

## 背景

Epic 78（AgentRun 执行器与主动推送闭环）Story 101-106 已交付：适配器框架、Launcher/Trigger、
`execute_run` 统一状态机主循环、RunStatus 枚举对齐、AgentSchedule 绑定松绑。
但 Epic 78 验收标准明确要求：

> 「执行器 **daemon** 运行后，到期 schedule 能真正触发 Agent 并落 success/failed」

Story 102 设计原文同样写明「容器里 `python -m agentboard.executor --daemon` 即可让
Codex 定时自主开发」。当前 `executor.py` CLI 仅支持**单次驱动**（`--run` / `--once` /
`--trigger` / `--trigger-once` / `--execute`），缺常驻循环：一次拉起只能处理一个 run，
无法让执行器像 scheduler daemon 一样持续自主开发。这是 Epic 78 无法验收收尾的最后缺口。

## 方案（纯增量，零 REST / DB 契约变更）

在 `agentboard/executor.py` 新增：

1. `run_daemon(session_factory, *, poll_interval, idle_sleep, max_runs, stop_event, max_poll_seconds)`：
   - 每轮取 id 升序第一个 `pending` AgentRun → 交 `execute_run`（已自动按 agent 分派
     Launcher / Trigger，外部 `report_run_result` 回写同样被感知）；
   - 无 pending run 时 `idle_sleep` 后继续轮询（`stop_event` 提供时用
     `stop_event.wait(idle_sleep)` 可被提前唤醒退出）；
   - `max_runs` 限制总处理数（测试 / 单次验收用），`None` = 无限常驻；
   - `stop_event`（threading.Event）置位或 KeyboardInterrupt 优雅退出；
   - **单 run 异常兜底**：`execute_run` 抛错不拖垮常驻循环，该 run 标记 failed 后继续。

2. CLI 新增 `--daemon` / `--daemon-poll-interval` / `--daemon-idle-sleep` /
   `--daemon-max-runs`（`--daemon-max-runs 0` 立即退出，便于冒烟/测试）。

## 为什么不做更多

- 并发认领（多 daemon 实例分抢 run）依赖 DB 行锁 lease，scheduler 已有该机制；
  Story 104 主循环暂为串行语义，daemon 复用同一语义，保持简单可验收。
- 不引入进程守护（systemd/supervisor）——那是部署层职责，非执行器代码。

## 验收

- `python -m agentboard.executor --daemon --daemon-max-runs N` 连续处理 N 个 pending 后退出；
- 无 pending run 时 idle sleep 后继续轮询，不崩溃；
- 单测（Fake session + 预置 pending run）覆盖：逐个处理 / 异常兜底 failed / 无 pending idle /
  max_runs 停止 / CLI max-runs=0 立即退出；
- E2E：自起 API+Web，REST 造 pending run，CLI daemon 真实处理（run 离开 pending），
  Playwright Web 渲染 0 报错；
- 回归：现有 pytest 套件无新增失败；
- 不触碰端口 18001；零既有 REST/DB 契约变更。
