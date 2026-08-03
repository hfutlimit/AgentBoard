# Tasks: .NET 10 Proposal Worker Service

## Implementation

- [x] Create a .NET 10 Windows Service / ASP.NET Core host.
- [x] Add shared and per-worker direct RabbitMQ consumers.
- [x] Add WorkBuddy CLI execution with MCP-oriented prompt and bounded output capture.
- [x] Add local SQLite execution history, retry queue, and LAN operations portal.
- [x] Add pause/resume, busy health state, heartbeat client, and reconnecting WebSocket client.
- [x] Add sample configuration and Windows installation guide.

## Verification

- [x] Restore and build the .NET project with no warnings or vulnerable packages.
- [ ] Configure a real RabbitMQ URL, Portal API key, and WorkBuddy command on a worker machine.
- [ ] Verify public dispatch, targeted follow-up dispatch, CLI MCP update, heartbeat, and Portal authorization end to end.
