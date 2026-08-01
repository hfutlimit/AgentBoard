# Proposal clarification capability

## Purpose

AgentBoard persists Proposals independently from Stories so an initial idea can pass through a durable human-agent clarification loop before work items are created.

## Requirements

### Requirement: Durable state and history

The system SHALL persist the original Proposal, clarification rounds, questions, answers, agent lease, failure details and converged Markdown specification.

#### Scenario: Continue after restart

- **WHEN** the API, broker or Worker restarts
- **THEN** the current Proposal state and all prior clarification history remain available from the database

### Requirement: Guarded workflow

The system SHALL enforce `draft -> queued -> analyzing -> awaiting -> answered -> analyzing -> converged -> story_created`, including explicit failure and retry transitions.

#### Scenario: Competing workers

- **WHEN** two Workers claim the same queued Proposal
- **THEN** exactly one conditional database update succeeds and the other receives a conflict

#### Scenario: Expired Worker

- **WHEN** an analyzing Proposal exceeds its lease
- **THEN** recovery moves it to queued and makes it discoverable again

### Requirement: Reliable notification

RabbitMQ SHALL provide durable wake-up messages and a dead-letter queue. The database SHALL remain the source of truth, and Workers SHALL scan the database backlog so a broker outage cannot strand a Proposal.

### Requirement: Human-approved conversion

The Proposal Worker SHALL NOT create Stories or Tasks. A human SHALL review converged Markdown, choose an Epic and explicitly convert it. Markdown `- [ ]` checklist lines SHALL become sibling Tasks under the new Story.

### Requirement: MCP worker contract

MCP SHALL expose `proposal_pending`, `proposal_claim`, `proposal_get`, `proposal_ask`, `proposal_finalize` and `proposal_fail` through the REST API boundary.
