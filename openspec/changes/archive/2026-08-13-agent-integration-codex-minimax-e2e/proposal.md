# Agent Integration E2E — Codex + MiniMax 端到端验证

**status**: completed
**date**: 2026-08-13

## 背景

`docs/agent-integration-analysis.md`（2026-08-13）盘点出 worker 端可集成的 8+ Agent，
其中 **Codex（OpenAI `codex exec`）** 和 **MiniMax（minimax-cli / `minimax_invoker.py` 直打 API）**
对个人开发者最常用，是最值得补的"两个口子"。

现状盘点（两个 Agent 在仓库内已有的代码）：

| Agent | 框架就位情况 | 测试 | 文档 |
|---|---|---|---|
| Codex | `CodexLauncher(CliLauncher)` 已注册（`agentboard/executor.py:481`），`AGENTBOARD_CODEX_BIN` env 覆盖 | Story 102 单元测试 13 个全绿（fake CLI 子进程） | 仅 README "Codex" 一节 |
| MiniMax | `scripts/minimax_invoker.py` 直打 MiniMax API；`scripts/minimax_adapter.py` 桥 minimax-cli | 无 pytest 测试；`backups/_e2e_minimax_invoker_20260811.py` 是临时脚本，依赖外部 API + 真实端口 | `docs/minimax-code-integration.md` 详尽，但未反映**方案 E 直打 API**新路径 |

## 问题

1. **Codex** 的 launch_run 测试只到「fake CLI 退出 0/非 0」层面，**没有覆盖 codex 真实输出格式**（`--json` 输出是 JSONL 流而非纯文本，`subprocess.Popen` + `communicate` 行为可能与 codex 实际不一致）。
2. **Codex 端到端流**（proposal 创建 → worker 轮询 → CodexLauncher 拉起 → 决策落库）**未验证**；只有单元层的 fake CLI 路径，缺全链路冒烟。
3. **MiniMax** 的 `minimax_invoker.py` 是**裸 Python 脚本**，不接入 `executor.py` 的 `ADAPTERS` 注册表 —— schedule/CLI 调度器看不到它，无法用 `python -m agentboard.executor --run <id>` 统一驱动。
4. **MiniMax** 决策 JSON 抽取逻辑（`<think>` 剥离 / Markdown 包裹 / 括号配对扫描）**无单元测试覆盖**。`backups/_e2e_minimax_invoker_20260811.py` 是真 e2e 但绑死了真实 API + 端口 18000，跑一次几百秒、且按调用计费。
5. 文档（`agent-integration-analysis.md`）把 Codex / MiniMax 标为"框架已就位、未见 E2E 验证" —— 完成本次后应升为"端到端验证"。

## 目标

1. **Codex 端到端**：
   - 新增 `tests/test_codex_e2e.py`：
     - 用 fake codex CLI（行为严格按 `codex exec --json` 真实输出协议：先打印 chatter 进度行 + 最后输出决策 JSON 到 stdout）；
     - 跑 `launch_run` 全链路：DB pending run → running → 拉起 fake codex → 决策 JSON 写入 `run.output` → success；
     - 覆盖 codex 退出非 0 / 超时 / 命令不存在三个失败分支。
2. **MiniMax executor 接入**：
   - `agentboard/executor.py` 新增 `MiniMaxLauncher(CliLauncher)`：默认命令 `<python> <abs>/scripts/minimax_invoker.py`，env `AGENTBOARD_MINIMAX_BIN` 覆盖；注册键 `minimax`。
   - 这样 `AgentSchedule.agent = "minimax"` 直接走 Executor；`launch_run` 通用驱动复用；agent 中心 / WebSocket 心跳（`docs/agent-config-center.md`）天然支持。
3. **MiniMax 单元测试**：
   - `tests/test_minimax_invoker_unit.py`：
     - mock `urllib.request.urlopen` 模拟 MiniMax API；
     - 覆盖 `ask` / `finalize` / `fail` 决策 + `<think>` 剥离 + Markdown 包裹 + 错误 HTTP 状态码 → fail action；
     - 覆盖 API Key 缺失 → 进程非零退出（与现有 `minimax_invoker.py` 行为对齐）。
4. **MiniMax E2E**：
   - `tests/test_minimax_e2e.py`：
     - 起本地 fake HTTP server（`http.server` 监听随机端口），回放 MiniMax 决策 JSON；
     - `launch_run` 走 `MiniMaxLauncher`，验证 DB success + output 含 fake server 回的 `converged_spec`。
5. **文档**：
   - `docs/agent-integration-analysis.md` 把 Codex / MiniMax 行从"框架已就位" → "端到端验证"（附测试文件路径）；
   - `README.md` 在「### Codex」节后追加「### MiniMax」节，给一段能跑通的命令模板。

## 非目标（留给后续 Change）

- **真实 Codex / MiniMax API E2E**（带计费、需要 token）：本次只到 fake CLI / fake server 层面，留 Story 178 或单独 change 做"凭据就位时的真实拉起冒烟"；
- **WorkBuddy (codebuddy CLI) 现状已端到端**（`docs/workbuddy-cli-integration.md`），本次不动；
- **Claude Code / Qoder / QoderWork**：本次只动 Codex + MiniMax；其他 Agent 验证见后续 change；
- **minimax-cli 路径（方案 B）**：直打 API（方案 E）已够用，minimax-cli v1.0.1 的 MCP HTTP 缺陷未解决前不重接；
- **executor 通用抽象再抽高**（`cli` / `http_api` / `webhook` 三类）：本次只补 MiniMax 直打 API 入口，不动框架。

## 验收（全部完成）

1. ✅ `pytest tests/test_codex_e2e.py -q`：**8 passed**；
2. ✅ `pytest tests/test_minimax_invoker_unit.py -q`：**15 passed**；
3. ✅ `pytest tests/test_minimax_e2e.py -q`：**5 passed**；
4. ✅ `pytest tests/test_epic78_story102_launcher.py tests/test_codex_e2e.py tests/test_minimax_invoker_unit.py tests/test_minimax_e2e.py -q`：**41 passed, 0 failed**（不破坏既有 Story 102）；
5. ✅ `python -c "from agentboard.executor import ADAPTERS; assert 'minimax' in ADAPTERS; assert 'codex' in ADAPTERS"` 退出码 0；
6. ✅ `docs/agent-integration-analysis.md` 中 Codex / MiniMax 行状态已更新；
7. ✅ 既有 REST 契约 / 端口 18001 不动；零 Alembic 迁移；零 MCP 工具增删。

## 验证结果（实跑数据）

```
41 passed in 24.78s
  - tests/test_epic78_story102_launcher.py: 13 passed（既有，未破坏）
  - tests/test_codex_e2e.py: 8 passed（新增）
  - tests/test_minimax_invoker_unit.py: 15 passed（新增）
  - tests/test_minimax_e2e.py: 5 passed（新增）
```

## 副产品（值得记一笔的修复）

`CliLauncher.launch()` 注入 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` 到
子进程 env —— **修 Windows zh-CN 默认 cp936 编码下 Python 子进程写 stdout
父进程按 UTF-8 解码拿到 replacement char 的 bug**（与 `SubprocessAgentInvoker`
早就有这个修复对齐）。这其实是真实 bug，不是为测试加的 fix —— 任何在
中文 Windows 上用 CliLauncher 拉起 Python 子进程的场景都会触发。
