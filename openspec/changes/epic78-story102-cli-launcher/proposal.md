# Epic 78 · Story 102 — 模式 A：Launcher（CLI Agent 主动拉起）

**status**: in_review
**date**: 2026-08-04

## 问题

Story 101 交付了适配器框架（`AgentAdapter` / `LauncherAdapter` / `TriggerAdapter` /
注册表），但 `ADAPTERS` 注册表里**还没有任何一个真实 Agent 的实现**——`resolve_adapter`
对任意 agent 名都回退到 `NotConfiguredAdapter`，`launch` 直接抛错。执行器仍无法
真正拉起一个 Agent。

Epic 78 的目标是「执行器把任务主动推给 Agent」。模式 A（Launcher）是其中最直接
的形态：**执行器直接 spawn CLI Agent 子进程（Codex / Claude），把任务当参数喂入，
零轮询、真推送**。

## 目标

1. `CliLauncher(LauncherAdapter)` 基类：CLI 命令解析（支持环境变量覆盖路径）、
   `subprocess.Popen` 拉起（非交互、UTF-8 输出捕获）、`read_output` 读回结果。
2. `CodexLauncher` / `ClaudeLauncher` 两个具体适配器，注册进 `ADAPTERS`
   （键 `codex` / `claude`）。
3. `build_prompt` 覆写：组装 `title + spec + 项目记忆 + 验收标准` 完整任务上下文。
4. 最小单次驱动 `launch_run(session_factory, run_id)`：认领 pending run →
   标记 running → 选适配器 → launch → poll 至终态（超时兜底）→ 回写
   `output / error_message / status / started_at / finished_at`。
5. CLI 入口 `python -m agentboard.executor --run <id>` / `--once`（处理第一个
   pending run），便于手动验收。

## 非目标（后续 Change 承接）

- Webhook Trigger 实现 → Story 103
- 执行器 daemon 主循环（轮询 pending → 并发认领 → 租约续期）→ Story 104
- `report_run_result` MCP 工具 → Story 104
- AgentSchedule 绑定松绑（项目/Agent 级 + 筛选）→ Story 106

## 方案

### 模块位置

`agentboard/executor.py` 增量扩展（在 Story 101 基础上追加，不删改既有符号），
纯新增代码，零 REST 契约变更、零数据库变更。

### 核心类型

```python
class CliLauncher(LauncherAdapter):
    command: list[str]            # 默认命令（如 ["codex", "exec"]）
    env_var: str = ""             # 环境变量名（如 AGENTBOARD_CODEX_BIN）覆盖命令

    def build_command(self, ctx) -> list[str]: ...   # 解析 env 覆盖 + prompt 注入
    def build_prompt(self, run, task, ctx) -> str: ...  # title + spec + memory + 验收
    def launch(self, run, task, ctx) -> RunHandle:
        # Popen(text=True, stdout=PIPE, stderr=STDOUT, encoding="utf-8", errors="replace")
        # stdin=DEVNULL（非交互）；返回挂 process 的 RunHandle
    def read_output(self, handle) -> str: ...        # 读 stdout 全文（已捕获）
```

### 命令与 prompt 注入策略

- Codex 默认命令 `["codex", "exec", "--json"]`（非交互 print 模式）；
  若 env `AGENTBOARD_CODEX_BIN` 设置了**完整命令串**（如
  `python /path/fake_codex.py`），则按 shell 拆分替换首参数。
- Claude 默认命令 `["claude", "-p"]`（print 模式）；env `AGENTBOARD_CLAUDE_BIN`
  同理。
- prompt 以 `stdin` 管道写入（`communicate(input=prompt)`）或作为最后参数传入
  ——由具体子类决定；默认实现把 prompt 作为 `communicate(input=...)` 喂入，
  避免超长参数受 OS 命令行长度限制。

### 最小单次驱动

```python
def launch_run(session_factory, run_id: int, *, poll_interval=1.0,
               max_poll_seconds: float | None = None) -> AgentRun | None:
    # 1. 取 run（pending 才处理）
    # 2. 组装 AgentRunContext：schedule.title → ctx; task title/spec → ctx;
    #    project key/name → ctx; memory 从 Document.type=memory 抽取
    # 3. update_run(run_id, status=RUNNING, started_at=now)
    # 4. cls = resolve_adapter(ctx.agent) → 实例化 → launch()
    # 5. 循环 poll_status：RUNNING → sleep；SUCCESS/FAILED → 回写并 break
    # 6. 超时 → handle.fail("timeout ...") → 回写 FAILED
    # 7. 回写 output / error_message / finished_at / status
```

agent 名解析：`ctx.agent` 默认取环境变量 `AGENTBOARD_DEFAULT_AGENT`
（默认 `workbuddy`，但 workbuddy 无 CLI Launcher 时回退 codex）；后续
Story 106 松绑后由 schedule 字段决定。

### CLI 入口

```
python -m agentboard.executor --run 123      # 单次执行指定 run
python -m agentboard.executor --once         # 执行第一个 pending run 后退出
```

## 验收

1. `ADAPTERS` 注册 `codex` / `claude` 两个具体适配器，`get_adapter` 可取回。
2. `CodexLauncher` / `ClaudeLauncher` 以真实子进程拉起 Fake CLI 脚本：
   - 退出码 0 → `SUCCESS`，stdout 全文写入 `run.output`；
   - 退出码非 0 → `FAILED`，stderr/错误写入 `run.error_message`；
   - 超时 → `FAILED` 带 timeout 错误；
   - 命令不存在（env 指向不存在路径）→ `AdapterError` → run 落 `FAILED` 不裸崩。
3. `build_prompt` 含 title / spec / 项目记忆 / 验收标准四要素。
4. `launch_run` 全链路：DB 中 pending run → running → success/failed，
   时间戳 / output / error_message 正确回写。
5. 回归：既有 pytest 套件无新增失败。
6. 不得修改任何既有 REST 契约；不得触碰端口 18001。
