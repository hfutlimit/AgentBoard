# Cursor CLI Worker Adapter

**status**: in_progress
**date**: 2026-09-13

## 为什么做

Worker-owned 本机执行只认 `codex` / `workbuddy` / `minimax`。Cursor CLI（`agent -p`）未接入，无法把 Cursor Grok 4.6 配成可领取工作的本机 Agent。

## 改什么

- 新增 C# `CursorAdapter`：无头拉起 Cursor CLI，复用既有 prompt / JSON / MCP 身份注入。
- Worker-owned provider 白名单、配置台、catalog 增加 `cursor`，模型含 `cursor-grok-4.6-high`。
- 注册进 DI、CliLocator、ReadinessProbe、startup slot。

## 影响范围

`src/nodes/AgentBoard.Node/` 适配器与 Worker-owned 配置台；对应 .NET 测试与 portal DOM 测试。不改 FastAPI 业务状态机。
