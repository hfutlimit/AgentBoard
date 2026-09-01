# AgentBoard Proposal Worker (.NET 10)

This is a Windows Worker Service that consumes AgentBoard proposal RabbitMQ messages and invokes the locally configured WorkBuddy CLI. WorkBuddy uses its existing MCP and memory to read/claim/update proposals. The service also hosts an operations Portal and writes local history to SQLite.

## Configure

Copy `appsettings.json` beside the executable (or use `appsettings.Production.json`) and replace at least:

- `Worker:Id`: stable unique ID, such as `dev-pc-01`.
- `RabbitMq:Uri`: the worker-accessible broker URI.
- `AgentBoard:ServerUrl` + `AgentBoard:StartupToken`: the FastAPI base URL and a
  service-account token for the WORKER identity. With
  `AgentBoard:RequireRegistration=true` the worker fails fast at startup when
  `ServerUrl` / `RabbitMq:Uri` is missing or no WorkBuddy/Codex agent could be
  registered (e.g. `Agents:*:Command` left empty) — instead of silently
  starting and leaving every Story stuck in todo.
- `Agents:WorkBuddy:Command` / `Agents:Codex:Command`: explicit command
  (`codebuddy` / `codex` or a full path). An empty `Command` disables that
  agent entirely — CLI auto-discovery (`CliLocator`) only affects execution,
  NOT registration: an agent with an empty Command is invisible to the
  scheduler.
- `Agents:*:AgentBoardToken`: per-agent FastAPI service-user token. Reviewer
  isolation (the server excludes candidates whose `user_id` equals the task
  assignee) REQUIRES distinct identities per logical agent: register one
  FastAPI user per agent (e.g. `service-user-wb` / `service-user-codex`), add
  both to the project as members, and paste their tokens here. Empty = falls
  back to the shared `AgentBoard:StartupToken`, which breaks multi-agent
  review on a single worker.
- `Portal:ApiKey`: a long random secret; the portal requires it in `X-AgentBoard-Worker-Key`.

`AgentBoard:HeartbeatUrl` and `AgentBoard:WebSocketUrl` intentionally default to empty. Set them only after the server-side worker-coordination endpoints are implemented.

## Run locally

```powershell
dotnet run --project src\workers\AgentBoard.ProposalWorker
```

Portal health: `GET http://localhost:58240/health`. Open `http://localhost:58240/` (or the worker machine's LAN address) to view worker state, execution history/detail, CLI output, pause/resume consumption, and request an operator-approved retry. The page asks for the portal key and retains it only in that browser session.

## Publish and install as a Windows service

```powershell
dotnet publish src\workers\AgentBoard.ProposalWorker -c Release -r win-x64 --self-contained false -o C:\AgentBoard\ProposalWorker
sc.exe create "AgentBoard Proposal Worker" binPath= "C:\AgentBoard\ProposalWorker\AgentBoard.ProposalWorker.exe" start= auto
sc.exe start "AgentBoard Proposal Worker"
```

Run the service under a dedicated Windows account that can execute WorkBuddy and access its MCP/memory configuration. Do not run it as LocalSystem unless that account has been explicitly prepared with the same CLI credentials and filesystem access.

## Follow-up routing contract for the future server implementation

- New proposal: publish the existing payload to exchange `agentboard.proposals`, routing key `dispatch`.
- Follow-up when original worker is healthy: publish the same payload to `agentboard.proposals.direct`, routing key `worker.<claimed_by>`.
- Original worker unhealthy or heartbeat expired: publish to the public route instead.
- Keep payload `{ proposal_id, round, reason, ts }`; consumers return to MCP/API for all proposal state.
