# Change: Proposal clarification loop

## Why

Initial product ideas are often too ambiguous to become executable Stories. AgentBoard needs a durable human-agent clarification loop that preserves questions, answers, failures and the final approved specification.

## What Changes

- Add Proposal, ProposalRound and ProposalQuestion persistence.
- Add a guarded Proposal state machine and REST API.
- Add six MCP worker tools and a configurable Worker process.
- Add RabbitMQ wake-ups, CAS claims, leases, recovery and a dead-letter queue.
- Add Web screens for submission, answers, review and human-approved Story/Task conversion.
- Add local Docker and native installation guidance.

## Impact

- New database migration and three tables.
- New optional RabbitMQ service and Proposal Worker Compose profile.
- New REST and MCP public contracts.
- Existing Project/Epic/Story/Task flows remain compatible.
