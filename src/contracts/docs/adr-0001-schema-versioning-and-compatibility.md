# ADR-0001 · Schema versioning and compatibility

- Status: Accepted (A0)
- Date: 2026-09-04
- Applies to: every cross-boundary contract in `AgentBoard.Contracts`

## Context

doc 151 §11 requires every contract to be independently versioned — workflow
schema, policy schema, command/result envelope, event envelope,
AgentProfile/ProviderDefinition, HandoffContext and persisted state
transitions — and fixes the evolution rules:

- a minor version may add backward-compatible fields;
- a major version that is incompatible must be explicitly rejected or migrated;
- a consumer must ignore optional fields outside its known range;
- a producer must confirm consumer capability before switching schema;
- durable records must not be silently reinterpreted by the running code
  version.

The failure mode this has to prevent is a contract change that works in
testing because both sides were redeployed together, then breaks in production
where a Node is offline for a week and comes back carrying an old attempt's
messages.

## Decision

**Version format is `{name}.v{major}[.{minor}]`** — for example `command.v1`,
`command.v1.3`, `handoff.v1`. The trailing minor is optional and defaults to
zero. The contract name may itself contain dots, so the version is parsed from
the right.

**Compatibility is same name and same major.** `SchemaVersion.IsCompatibleWith`
is the only place this rule is implemented. A producer may carry a higher minor
than the consumer understands, because the consumer is required to ignore
fields it does not know.

**Rejection is explicit and names the field.** `EnvelopeValidator` and the
other validators return `EnvelopeError` records rather than throwing, so a
message can be refused with a reason that an operator can act on (doc 150
NFR-006 forbids a context-free error string). An incompatible major version is
rejected; it is never coerced into a default.

**Consumers deserialise permissively.** Unknown fields are tolerated. A strict
deserialiser would turn every additive producer change into a breaking one and
defeat the minor-version rule entirely.

**Durable records are immutable.** WorkflowVersion, WorkflowRun, Assignment and
PolicyRevision are positional records, so a persisted version identifier, lease
epoch or policy revision cannot be edited after construction. The type system,
not discipline, is what stops a durable record from being reinterpreted.

## Consequences

- Adding an optional field is a minor bump and needs no coordination between
  Server and Node.
- Removing or retyping a field is a major bump and requires a migration window
  with dual read / dual write, because an offline Node may still be carrying
  messages in the old shape.
- A producer that wants to switch schema must first confirm the consumer's
  capability; there is no implicit upgrade path.
- `SchemaVersion.TryParse` returning false is a rejection, not a fallback to
  "assume latest". Assuming latest is how an old client silently corrupts a new
  server's state.
