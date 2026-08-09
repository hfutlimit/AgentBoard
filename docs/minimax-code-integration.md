# MiniMax Code 集成（Worker 无头 Agent）—— 适配器方案

> 状态：适配器 + 配置就绪（2026-08-09）；真实 CLI 执行需 MiniMax API Key，待用户侧验证
> 相关：Ticket 全流程（Story 确认 → Agent 自动处理）、docs/workbuddy-cli-integration.md

## 1. 结论（先说清边界）

- **官方「MiniMax Code」是桌面 GUI 应用**（v3.0.56，macOS/Windows，基于 OpenCode+Pi
  harness），**没有 headless CLI 接口** → 无法直接接入 Worker 的
  stdin→prompt / stdout→JSON 无头协议。
- **可接入的是 MiniMax 生态的 npm CLI `minimax-cli`**（AGPL-3.0）：提供无头模式
  `minimax -p "<prompt>"`（单次执行退出），本仓库用 `scripts/minimax_adapter.py`
  桥接 Worker 协议。
- **能力边界**：minimax-cli 无 MCP 集成，agent 无法经 AgentBoard MCP 调用
  `set_status` / `submit_task_for_review` 等写库工具 → **仅适用于不需要 MCP 的
  决策轮**：
  - ✅ Proposal 澄清轮（`ask` / `finalize` / `fail` 纯文本决策）
  - ❌ Story 执行轮 / Ticket 创建轮（需 MCP 推进 task 状态）—— 请用 codebuddy 通道
- Windows 命令行 32K 上限：Story 全量重放等大 prompt 场景自动拒绝并提示走 codebuddy。

## 2. MiniMax 生态盘点（选型依据）

| 工具 | 类型 | headless | MCP | 能否作 Worker agent |
|---|---|---|---|---|
| MiniMax Code（官方桌面） | GUI 应用 | ❌ | - | ❌ 无 CLI 接口 |
| `minimax-cli`（npm/bun） | 对话式 CLI | ✅ `-p` | ❌ | ⚠️ 仅澄清/分析轮 |
| `minmax-code`（npm TUI） | TUI | ❌ | ✅ | ❌ 无 headless |
| `mmx-cli`（官方 API 工具） | 媒体/检索 CLI | ✅ | 被调用方 | 工具集，非编码 agent |
| `minimax-coding-plan-mcp`（uvx） | MCP server | - | 对外提供 | 可给 codebuddy 等补 MiniMax 编码能力 |

## 3. 安装与登录（用户侧执行）

```bash
# Node.js 18+（或 bun）
npm install -g minimax-cli          # 或 bun add -g minimax-cli
export MINIMAX_API_KEY="sk-..."     # 或 ~/.minimax/user-settings.json 配 apiKey
minimax --version
# 验证无头模式
minimax -p "用一句话回答：1+1=?"
```

## 4. Worker 启动（agent_cmd 模板）

```bash
AGENTBOARD_API_URL=<api_url> \
AGENTBOARD_WORKER_TOKEN=<abk_ key 或登录 token> \
AGENTBOARD_WORKER_AGENT_CMD="\"C:/Users/<user>/.workbuddy/binaries/python/envs/default/Scripts/python.exe\" \"E:/Projects/WorkBuddy/AgentBoard/scripts/minimax_adapter.py\"" \
MINIMAX_CLI_PATH="minimax" \
MINIMAX_MODEL="minimax-pro" \
AGENTBOARD_WORKER_AGENT_TIMEOUT=300 \
python -m agentboard.worker --loop
```

适配器环境变量（可选）：`MINIMAX_CLI_PATH`（默认 minimax）、`MINIMAX_MODEL`
（minimax-pro / minimax-fast-1）、`MINIMAX_DIRECTORY`（工作目录）、
`MINIMAX_TIMEOUT`（默认 600s）。

## 5. 踩坑记录（重要）

1. **无 MCP = 无写库能力**：minimax-cli 只会在对话中给决策 JSON，无法推进 task
   状态。把 minimax 通道用于「澄清/分析」，把 codebuddy 用于「执行/写库」，
   二者可共存（不同 worker 实例 / 不同 agent 注册）。
2. **`-p` 是参数不是 stdin**：Worker 协议是 stdin 喂 prompt，故必须经
   `scripts/minimax_adapter.py` 桥接；直接配 `minimax -p` 会因 Worker 把 prompt
   塞进 stdin 而拿不到。
3. **超长 prompt 拒绝**：适配器对 >20K 的 prompt 直接返回
   `{"action":"fail","error":"prompt 过长..."}`，避免 Windows 32K 命令行截断
   产生静默失败。
4. **API Key 必配**：`MINIMAX_API_KEY` 或 `~/.minimax/user-settings.json`
   （`{"apiKey": "sk-..."}`），否则 minimax-cli 交互式卡在登录引导，
   无头模式直接失败。
5. **AGPL-3.0 许可**：minimax-cli 为 AGPL-3.0，商业分发需注意合规（个人/内部使用无碍）。

## 6. 验证方法

```bash
# 1. 适配器冒烟（本机无需 minimax-cli 也能验证协议桥接——会走到"无法启动"分支）
echo '{"action":"ask"}' | python scripts/minimax_adapter.py

# 2. 真实 CLI 无头（安装并配 Key 后）
printf '你是需求澄清分析师。请输出决策 JSON：{"action":"finalize","converged_spec":"# OK"}' \
  | MINIMAX_CLI_PATH=minimax python scripts/minimax_adapter.py

# 3. 端到端（同 codebuddy 模式）：起 API 18099 + worker --loop，建提案确认后
#    观察 worker 拉起适配器 → minimax-cli 输出含决策 JSON → worker 落库
```

## 7. 后续演进（可选）

- MiniMax 官方若发布 headless CLI（对标 claude code），补 `--mcp-config` 支持后
  可升级为全通道 agent（同 codebuddy 模式）；
- 给 codebuddy 通道补 `minimax-coding-plan-mcp`（uvx）可让现有 agent 获得
  MiniMax 编码计划/推理能力（MCP 多 server 叠加）。
