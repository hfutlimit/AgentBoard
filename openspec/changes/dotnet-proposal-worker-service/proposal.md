# Change: .NET 10 Proposal Worker Service

## Why

The existing Python worker can consume proposal work, but worker computers need a deployable Windows-native host with operational visibility. Proposal follow-up work must prefer the worker that handled the prior round, while still recovering to the shared pool when that worker is unhealthy.

## What changes

- Add a standalone .NET 10 Windows Worker Service under `workers/AgentBoard.ProposalWorker`.
- Consume the existing shared RabbitMQ proposal queue and a per-worker direct queue.
- Start WorkBuddy CLI with a full MCP-oriented proposal prompt; WorkBuddy owns proposal updates through its configured MCP and memory.
- Host a LAN-accessible, API-key-protected operations portal with health, pause/resume, retry, execution history, detail, and CLI output.
- Persist local execution/audit history in SQLite.
- Add configurable HTTP heartbeat and WebSocket clients as forward-compatible AgentBoard coordination boundaries. No unimplemented Python API route is called by default.

## Non-goals

- Do not remove or modify the existing Python worker.
- Do not add an unimplemented server-side worker registry, affinity dispatcher, or WebSocket endpoint in this change.
- Do not change the existing public proposal message payload.

