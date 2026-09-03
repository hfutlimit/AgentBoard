# AgentBoard Node (.NET 10)

This is the AgentBoard execution node: a Windows Service that consumes AgentBoard RabbitMQ messages and invokes the locally configured agent CLIs (WorkBuddy / Codex / MiniMax / Qwen). Each agent uses its own CLI, MCP and memory to read/claim/update the work it is given. The service also hosts an operations Portal and writes local execution history to SQLite.

Naming (P7b, 2026-09-03): the executable, the project and the Windows service are all `AgentBoard Node`. The word `Worker` is still used where it means "the workstation identity" — the node id surfaces as `worker_id` in `/health` and is the RabbitMQ routing key the server uses to route follow-up work back to the same machine.

## Configure

Generate `appsettings.Production.json` from the tracked template with:

```powershell
pwsh -File scripts\new-node-appsettings.ps1 -OutFile C:\AgentBoard\Node\appsettings.Production.json
```

then replace at least:

- `Node:Id`: stable unique ID, such as `dev-pc-01`.
- `RabbitMq:Uri`: the node-accessible broker URI.
- `AgentBoard:ServerUrl` + `AgentBoard:StartupToken`: the FastAPI base URL and a
  service-account token for the WORKSTATION identity. With
  `AgentBoard:RequireRegistration=true` the node fails fast at startup when
  `ServerUrl` / `RabbitMq:Uri` is missing or no agent could be registered (e.g.
  `Agents:*:Command` left empty) — instead of silently starting and leaving
  every Story stuck in todo.
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
  review on a single machine.
- `Portal:ApiKey`: a long random secret; the portal requires it in
  `X-AgentBoard-Worker-Key` (the header name is part of the existing portal
  contract and is deliberately unchanged).

The generator writes **both** the canonical `Node` section and a mirrored legacy
`Worker` section. Program.cs binds `Worker` first as the baseline and then
layers `Node` on top, overriding only keys actually present in `Node` — so
configure values under `Node` and never duplicate them into both sections. The
`Worker` mirror exists so a rollback to the previous binary (which only
understands `Worker`) reads the same configuration instead of falling back to
shipped defaults.

`AgentBoard:HeartbeatUrl` and `AgentBoard:WebSocketUrl` intentionally default to empty. Set them only after the server-side node-coordination endpoints are implemented.

## Run locally

```powershell
dotnet run --project src\nodes\AgentBoard.Node
```

Portal health: `GET http://localhost:58240/health`. Open `http://localhost:58240/` (or the node machine's LAN address) to view node state, execution history/detail, CLI output, pause/resume consumption, and request an operator-approved retry. The page asks for the portal key and retains it only in that browser session.

## Publish and install as a Windows service

Automated (preferred — verifies prerequisites, sets the service account and runs
`/health` assertions):

```powershell
pwsh -File scripts\install-node.ps1 -WorkerId "prod-pc-01" -AmqpUri "amqp://agentboard:***@broker.example.com:5672/%2F"
```

Manual:

```powershell
dotnet publish src\nodes\AgentBoard.Node -c Release -r win-x64 --self-contained false -o C:\AgentBoard\Node

# Generates appsettings.Production.json (Node section + mirrored Worker
# section). Do NOT skip this and hand-copy the template: the config would then
# have no Worker mirror and a rollback to the previous binary would read
# shipped defaults instead of your production values.
pwsh -File scripts\new-node-appsettings.ps1 -OutFile C:\AgentBoard\Node\appsettings.Production.json

sc.exe create "AgentBoard Node" binPath= "C:\AgentBoard\Node\AgentBoard.Node.exe" start= auto
sc.exe start "AgentBoard Node"
```

Run the service under a dedicated Windows account that can execute the agent CLIs and access their MCP/memory configuration. Do not run it as LocalSystem unless that account has been explicitly prepared with the same CLI credentials and filesystem access.

## Follow-up routing contract for the future server implementation

- New proposal: publish the existing payload to exchange `agentboard.proposals`, routing key `dispatch`.
- Follow-up when original node is healthy: publish the same payload to `agentboard.proposals.direct`, routing key `worker.<claimed_by>`.
- Original node unhealthy or heartbeat expired: publish to the public route instead.
- Keep payload `{ proposal_id, round, reason, ts }`; consumers return to MCP/API for all proposal state.
