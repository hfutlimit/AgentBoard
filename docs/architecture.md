# Python architecture

AgentBoard is a modular monolith. Business code is grouped by domain under
`agentboard/domains`; transport and infrastructure stay outside the domains.

## Dependency direction

`api / mcp / scheduler -> domain services -> domain models -> domains/common`

Domain modules must not import FastAPI, FastMCP, or the application entrypoints.
Cross-domain database references use foreign-key identifiers rather than ORM
relationships, keeping imports acyclic. Infrastructure owns sessions and
transactions.

## Domains

- `projects`: projects, membership, epics, stories, and sprints.
- `work_items`: tasks, comments, and attachments.
- `identity`: users and notifications.
- `scheduling`: agent schedules and runs.
- `common`: the shared ORM registry, enums, and small domain primitives.

`agentboard/models.py` is a compatibility facade for existing integrations.
New code should import from its owning domain. The same migration pattern will
be used for `service.py` and `api.py`: move one vertical slice at a time, keep a
temporary facade, then remove the facade after callers have migrated.
