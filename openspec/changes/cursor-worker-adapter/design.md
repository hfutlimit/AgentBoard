# Design — Cursor Worker Adapter

## 方案

按 Codex 适配器抄：`IAgentAdapter` + `IProcessExecutor` 一次性子进程。不引入 Cursor SDK / ACP。

默认命令：

```
agent -p --force --trust --approve-mcps --output-format json --model <id>
```

- Prompt 走 stdin（与 Codex 相同，避开 Windows 命令行长度限制）。
- `--output-format json` 的 CLI 信封从 `.result` 抽出业务 JSON，再复用 `TryExtractProviderJson`。
- 鉴权：转发 `CURSOR_API_KEY`（若存在）以及 Cursor 登录所需的 USERPROFILE/APPDATA；`ApiKeyEnv` 默认空，允许 `agent login` 会话。
- MCP：注入 `AGENTBOARD_MCP_TOKEN` / `AGENTBOARD_API_URL`，并带 `--approve-mcps`。

Worker-owned 工厂把同一份 `AgentOptions` 填进 `AgentsOptions.Cursor`。配置台模型默认 `cursor-grok-4.6-high`，命令为 `agent`。
