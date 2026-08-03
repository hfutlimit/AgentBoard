# Design: .NET 10 Proposal Worker Service

## Dispatch topology

The database remains the source of truth. RabbitMQ messages only tell a worker to inspect and process a proposal through MCP.

```mermaid
flowchart LR
  A[AgentBoard API] -->|new proposal| E[proposal exchange]
  E -->|dispatch| Q[shared work queue]
  A -->|follow-up, preferred worker healthy| D[proposal direct exchange]
  D -->|worker.{workerId}| WQ[per-worker queue]
  Q --> WS[any healthy Worker Service]
  WQ --> WS
  WS --> C[WorkBuddy CLI]
  C -->|configured MCP| M[AgentBoard MCP]
  WS --> P[local operations portal]
  WS -. heartbeat / websocket when configured .-> A
```

`agentboard.proposals` and `dispatch` remain the existing public exchange/routing key. The service adds a direct exchange named `<namespace>.direct` and a durable worker queue named `<namespace>.worker.<workerId>`, bound with `worker.<workerId>`.

For an answered proposal, the server dispatcher should publish direct only when the prior `claimed_by` worker is healthy; otherwise it publishes back to the public exchange. The current service consumes both routes, making the later server change additive.

## Safety and acknowledgement

- RabbitMQ prefetch is one, so one worker never starts multiple CodeBuddy sessions concurrently.
- The process is marked busy before CLI launch and cleared in `finally`.
- A message is ACKed only after WorkBuddy exits successfully. Failure is NACKed without requeue and lands in the existing DLQ; the portal can republish a recorded payload after operator review.
- WorkBuddy receives the proposal id, round, reason, worker id, and an instruction to use its configured AgentBoard MCP for claim/read/question/finalization. It must not access AgentBoard's database directly.
- The service never writes API or MCP tokens into history or Portal responses.

## Operations portal

Kestrel binds to the configured LAN address. `/health` is unauthenticated for monitoring. All operational endpoints require `X-AgentBoard-Worker-Key`, sourced from local configuration, and return 503 until a non-empty key is supplied. The portal exposes read-only history/detail plus pause, resume, and retry commands.

## Coordination clients

`HeartbeatUrl` and `WebSocketUrl` default to empty. When configured, the service sends heartbeat snapshots and reconnects to WebSocket notifications with exponential backoff. This avoids fabricating a server contract while giving the future AgentBoard API a stable client shape.

