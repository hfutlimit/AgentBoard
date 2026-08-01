# Design

## State model

`draft -> queued -> analyzing -> awaiting -> answered -> analyzing -> converged -> story_created`

Any active processing state may move to `failed`; failed work can return to `draft` or `queued`. Story creation is terminal.

## Consistency

The relational database is the source of truth. RabbitMQ carries durable wake-up messages only. Workers also scan `queued` and `answered` rows at startup and during polling, so broker outages do not strand work.

Claims use a conditional database update from `queued|answered` to `analyzing`. A worker identity and timestamp form a lease. Stale analyzing rows return to queued and are republished.

## Human gate

The agent may ask questions or write `converged_spec`. It cannot convert the result. A user reviews the final Markdown, selects an Epic and explicitly creates the Story. Markdown checklist items become Tasks in the same transaction.

## Agent adapter

The Worker invokes a configurable command with JSON on stdin. This keeps the orchestration independent of a particular LLM vendor or local CLI. The command returns either an `ask` or `finalize` JSON decision.
