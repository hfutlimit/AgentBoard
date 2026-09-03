# Agent 集成矩阵分析 + 验证状态

> 状态盘点：2026-08-13（OpenSpec change `agent-integration-codex-minimax-e2e` 落地后）
> 关联：`docs/workbuddy-cli-integration.md`、`docs/minimax-code-integration.md`、
> `docs/agent-config-center.md`、`agentboard/executor.py`

## 1. 现状盘点（2026-08-13）

worker 端两层架构都有接缝，**多数 Agent 已在仓库里有代码**，完成度差异大：

### 1.1 Python Worker 层（`src/backend-fastapi/agentboard/agent_runtime/`）

- `SubprocessProcessorInvoker`（`invokers.py`）：通用 stdin→prompt / stdout→JSON 协议，任何 headless CLI 可接
- `AgentConfig Center`（`docs/agent-config-center.md`）：同 CLI 多 Agent + `{model}` 占位符，Worker 周期 `--version` 探活
- `minimax_invoker.py` / `minimax_invoker.py`：MiniMax 直打 API 桥接

### 1.2 C# ProposalProcessor 层（`src/workers/AgentBoard.ProposalProcessor/`）

- `WorkBuddyRunner.cs`：拉起 `workbuddy`（实际是 codebuddy CLI，subprocess + stdin），生产正在跑
- `appsettings.json` 里 `WorkBuddy:Command/WorkingDirectory/TimeoutMinutes` 是配置项

### 1.3 Executor 适配器层（`agentboard/executor.py`）—— 关键

- `KNOWN_AGENTS = ("codex", "claude", "workbuddy", "qoder")` 在调度器侧
- `CodexLauncher(CliLauncher)`：`codex exec --json` 已实现（覆盖 OpenAI Codex）
- `ClaudeLauncher(CliLauncher)`：`claude -p` 已实现（覆盖 Claude Code）
- `MiniMaxLauncher(CliLauncher)`：**2026-08-13 新增**（本次 change），包装 `scripts/minimax_invoker.py`
- `WebhookTrigger` 注册为 `workbuddy` / `qoder` 名字 —— QoderWork 的 webhook 唤醒常驻 Runner 路径已开

## 2. 验证状态矩阵

| Agent | 仓库现状 | 完成度 | 验证日期 | 验证路径 |
|---|---|---|---|---|
| **WorkBuddy (codebuddy CLI)** | C# `WorkBuddyRunner.cs` + Python `SubprocessProcessorInvoker` 都有 | ✅ 端到端（生产） | 2026-08-08 | `docs/workbuddy-cli-integration.md` 验证记录 |
| **Codex (OpenAI `codex exec`)** | `CodexLauncher` + 单元测试 13 个 | ✅ 端到端（fake CLI 模拟真实协议） | **2026-08-13** | `tests/test_codex_e2e.py` 8 passed |
| **MiniMax (直打 API)** | `minimax_invoker.py` + `MiniMaxLauncher` | ✅ 端到端（fake API server） | **2026-08-13** | `tests/test_minimax_e2e.py` 5 passed + `tests/test_minimax_invoker_unit.py` 15 passed |
| **MiniMax (minimax-cli)** | `minimax_adapter.py` 桥 minimax-cli | ⚠️ 框架就位 / minimax-cli v1.0.1 MCP HTTP 缺陷未解，验证受阻 | 2026-08-09 | `docs/minimax-code-integration.md` |
| **Claude Code** | `ClaudeLauncher` 类 | ⚠️ 单元测试 1 个 + e2e 框架就位，未单独写 launcher e2e | 2026-08-04 | `tests/test_epic78_story102_launcher.py::test_launch_run_claude_adapter` |
| **Qoder** | Webhook 模式 + `TRIGGER_AGENTS` | ⚠️ 框架就位、需验证 webhook 契约 | 2026-08-04 | `agentboard/executor.py:866` |
| **Qoder CLI (`@qoder-ai/qodercli`)** | 框架可接（CliLauncher 子类） | ❌ 未实现 | - | 建议优先级 P2 |
| **QoderWork（macOS 桌面）** | 框架可接（WebhookTrigger） | ❌ 未验证（macOS only） | - | 建议优先级 P3 |
| **GLM-4 / DeepSeek / Kimi K2** | 直打 chat API 模式可复用 `minimax_invoker.py` 模板 | ❌ 未实现 | - | 建议优先级 P2 |
| **Gemini CLI** | `gemini -p` headless | ❌ 未实现 | - | 建议优先级 P3 |
| **Aider** | `aider --message` | ❌ 未实现 | - | 建议优先级 P3 |
| **Cursor CLI** | `agent` 子命令 | ❌ 未实现 | - | 建议优先级 P3 |

## 3. 本次交付（OpenSpec change `agent-integration-codex-minimax-e2e`）

### 3.1 改动清单

- `agentboard/executor.py`：
  - `CliLauncher.launch()` 注入 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1` 到子进程 env，**修 Windows zh-CN 默认 cp936 编码下 Python 子进程写 stdout 父进程按 UTF-8 解码拿到 replacement char 的 bug**（与 `SubprocessProcessorInvoker` 行为一致）
  - 新增 `MiniMaxLauncher(CliLauncher)`，注册名 `minimax`
  - 新增 `import sys, pathlib.Path`
- `tests/_fake_codex.py`：fake codex CLI，按 `codex exec --json` 真实协议（stderr progress chatter + stdout 决策 JSON + 退出码可控）
- `tests/test_codex_e2e.py`：8 个用例，覆盖 ask / finalize / fail 决策、退出非 0、超时、命令缺失、CLI 冒烟
- `tests/test_minimax_invoker_unit.py`：15 个用例，覆盖决策 JSON 抽取（think 剥离 / markdown 包裹 / 中文 / 错误）、HTTP 错误、进程入口
- `tests/test_minimax_e2e.py`：5 个用例，覆盖 launcher 注册、`launch_run` 全链路、API 500 降级、env 覆盖

### 3.2 验证结果

```
41 passed in 24.78s
  - tests/test_epic78_story102_launcher.py: 13 passed（既有，未破坏）
  - tests/test_codex_e2e.py: 8 passed（新增）
  - tests/test_minimax_invoker_unit.py: 15 passed（新增）
  - tests/test_minimax_e2e.py: 5 passed（新增）
```

### 3.3 踩坑记录（值得记一笔）

1. **CliLauncher 子进程 UTF-8 编码 bug**：`Popen(encoding="utf-8", errors="replace")` 只在**父进程**层面按 UTF-8 解码，但**子进程**默认按系统 locale（中文 Windows 是 cp936）写 stdout → 子进程写 cp936 字节 → 父进程 UTF-8 解码 → 输出含 `\ufffd` replacement char。解法是 `subprocess.Popen` 的 `env` 注入 `PYTHONIOENCODING=utf-8` + `PYTHONUTF8=1`，强制子进程按 UTF-8 写。`SubprocessProcessorInvoker` 早就有这个修复（`invokers.py:189-197` 注释），`CliLauncher` 漏了。
2. **monkeypatch.setattr 不支持 side_effect**：mock 时要把 side_effect 包在 `mock.Mock(side_effect=...)` 里再传。
3. **minimax_invoker.py 的模块常量在 import 时从 os.environ 读**：测试要 `setattr` 必须在 import 之前 patch env，否则模块级常量已是旧值。
4. **Python 3.14 移除 `urllib.server`**：本地 HTTP server 用 `http.server`，别用 `urllib.server`。
5. **minimax_invoker "失败不 crash" 设计**：API 4xx/5xx → 写 `{"action":"fail"}` + exit 0，Executor 看到 success。失败信息在 `run.output` 的 fail decision 里，调用方按 worker 协议解析即可。

## 4. 建议优先级（剩余未做的）

按"落地价值 × 工作量"排序：

1. **GLM-4 / DeepSeek 直打**（半天）：复用 `minimax_invoker.py` 模板，新增 `glm_invoker.py` / `deepseek_invoker.py`，各 ~50 行；Executor 加 `GlmLauncher` / `DeepseekLauncher` 同款包装。模型路由 + 备胎通道立等可取。
2. **Qoder CLI（`@qoder-ai/qodercli`）**（1-2 天）：阿里 Qwen-Coder 模型 + MCP 支持。CliLauncher 子类即可。Story/Ticket 执行轮走它能省海外 API 钱。
3. **QoderWork（macOS Webhook）**（1 天）：验证 `WebhookTrigger` 契约。需 macOS 验证（你的环境是 Windows，建议降级或暂缓）。
4. **Gemini CLI**（半天）：`gemini -p` headless + MCP，CliLauncher 子类。
5. **Aider / Cursor CLI / 其它**：低优先级，价值是多样性。

## 5. 跑测试

```bash
# 全 agent 集成测试（含 Story 102 / Codex / MiniMax）
PYTHONPATH=. python -m pytest tests/test_epic78_story102_launcher.py tests/test_codex_e2e.py tests/test_minimax_invoker_unit.py tests/test_minimax_e2e.py -q

# 仅 Codex e2e
PYTHONPATH=. python -m pytest tests/test_codex_e2e.py -q

# 仅 MiniMax
PYTHONPATH=. python -m pytest tests/test_minimax_invoker_unit.py tests/test_minimax_e2e.py -q
```

跑通即视为 Codex / MiniMax 端到端路径正常。**真实 CLI / 真实 API 的冒烟**需 `codex` CLI 已装 / `MINIMAX_API_KEY` 已配置，CI 不会自动跑（属人工/本地环境前置）。
