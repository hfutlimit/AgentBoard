# Design — 模式 A：Launcher（CLI Agent 主动拉起）

## 架构定位

```
                    ┌─────────────────────────────────────────────┐
                    │        Executor 主循环 (Story 104, 后续)     │
                    │  轮询 pending → 并发认领 → 租约续期          │
                    └──────────────────┬──────────────────────────┘
                                       │ launch_run() (★本 Change 最小驱动)
                    ┌──────────────────▼──────────────────────────┐
                    │   CliLauncher(LauncherAdapter)   ★本 Change │
                    │  build_prompt: title+spec+memory+验收       │
                    │  launch: Popen(非交互, UTF-8 捕获)          │
                    │  poll_status: process.poll() 退出码判定     │
                    └───────────────┬────────────────┬────────────┘
                                    │                │
                        ┌───────────▼────┐   ┌───────▼───────────┐
                        │ CodexLauncher  │   │ ClaudeLauncher    │
                        │ codex exec     │   │ claude -p         │
                        │ (--json print) │   │ (print 模式)      │
                        └────────────────┘   └───────────────────┘
```

## 关键设计决策

### 1. prompt 走 stdin，不走 argv

CLI Agent 的 prompt 可能很长（spec + 记忆 + 验收标准动辄数 KB），OS 命令行
有长度限制（Windows ~32K，Linux ~2MB 但 argv 传递有编码风险）。因此：
- `Popen(stdin=DEVNULL)` 拉起，**prompt 通过 `communicate(input=prompt)` 喂入
  stdin**；
- Fake CLI 测试脚本从 stdin 读 prompt，验证四要素齐全后按约定退出码退出。

### 2. 命令路径环境变量覆盖（可测试性 + 生产灵活性）

- 默认命令：Codex = `["codex", "exec", "--json"]`；Claude = `["claude", "-p"]`。
- env `AGENTBOARD_CODEX_BIN` / `AGENTBOARD_CLAUDE_BIN` 可覆盖**完整命令串**
  （如 `python C:/fakes/codex.py`），用 `shlex.split(posix=not win32)` 拆分。
  这样测试用 Fake CLI 脚本注入，生产用真实 CLI，**同一套适配器代码**。

### 3. 输出捕获：stderr 合并进 stdout

`stderr=STDOUT` 合并，`text=True, encoding="utf-8", errors="replace"`：
- Windows 下非 UTF-8 输出不会让 decode 崩溃（errors=replace 兜底）；
- 单管道读取，避免子进程写满 stderr 管道缓冲导致死锁（PIPE 缓冲 64KB）。
- `communicate()` 一次性收完，无死锁风险。

### 4. 退出码判定 + 超时兜底

- `poll_status`：`process.poll()` → `None`=RUNNING；`0`=SUCCESS；非 0=FAILED。
- 超时：`launch_run` 循环累计等待超过 `max_poll_seconds`（默认 `None` =
  用适配器 `timeout_seconds`），调 `process.kill()` 后落 `FAILED("timeout")`。
  `timeout_seconds` 默认 1800s（LauncherAdapter 类属性），测试注入小值。

### 5. agent 名解析顺序（Story 106 前的临时策略）

```
ctx.agent = env AGENTBOARD_DEFAULT_AGENT (默认 "codex")
```
- Story 106 为 AgentSchedule 增加 agent 字段后，此解析点改为读 schedule 字段，
  `launch_run` 的组装处是唯一改点，适配器层零改动。

### 6. launch_run 回写 DB 的字段映射

| AgentRun 字段    | 来源                                   |
|------------------|----------------------------------------|
| status           | RUNNING(启动) / SUCCESS / FAILED       |
| started_at       | launch 前                             |
| finished_at      | 终态判定时                            |
| output           | 退出码 0 时 stdout 全文                |
| error_message    | 退出码非 0 或超时时的错误信息          |

## 兼容性

- 纯增量：仅扩展 `agentboard/executor.py`，新增 `tests/test_epic78_story102_launcher.py`。
- 零 REST 契约变更、零数据库变更、零前端变更。
- 不修改 `scheduler.py` / `service.py` / `api.py` / `mcp_server.py`。
- Story 101 既有符号（AgentAdapter / RunHandle / 注册表等）不做破坏性改动。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| CLI 未安装（codex/claude 不存在） | env 未配置时 Popen 抛 FileNotFoundError → launch 转为 AdapterError → run 落 FAILED（含可读错误），不裸崩 |
| 子进程长期挂起 | timeout_seconds + launch_run 超时循环 kill |
| Windows 编码问题 | encoding="utf-8", errors="replace", text=True |
| prompt 含特殊字符 | stdin 管道传递，不经 shell 拼接 |
